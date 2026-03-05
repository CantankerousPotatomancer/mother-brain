import logging
from uuid import UUID
from db import get_pool, touch
from embeddings import embed
from models.schemas import (
    Entity, Fact, Summary, Episode, WorkingMemoryEntry,
    Event, Obligation, Goal, Relationship,
    RecallResult, RecallResultItem, UpcomingResult,
)

logger = logging.getLogger("memory-brain.tools.read")

# Session-level hit tracker for auto-promotion (keyed by entity_id)
_session_hits: dict[UUID, int] = {}


def get_session_hits() -> dict[UUID, int]:
    return _session_hits


async def recall(query: str, limit: int = 5) -> dict:
    """Primary retrieval: working memory -> vector search summaries."""
    pool = await get_pool()

    # 1. Check working memory for active entries
    wm_rows = await pool.fetch(
        "SELECT wm.*, e.name AS entity_name, e.type AS entity_type "
        "FROM working_memory wm "
        "JOIN entities e ON e.id = wm.entity_id "
        "WHERE wm.expires_at > NOW() "
        "ORDER BY wm.touches DESC"
    )
    wm_entries = [WorkingMemoryEntry(**dict(r)) for r in wm_rows]
    if wm_rows:
        await touch("working_memory", [r["id"] for r in wm_rows])

    # 2. Embed query and cosine search summaries
    query_embedding = await embed(query)
    results: list[RecallResultItem] = []

    if query_embedding is not None:
        rows = await pool.fetch(
            "SELECT s.id AS summary_id, s.entity_id, s.content AS summary_content, "
            "s.fact_count, s.touches AS s_touches, s.last_touched AS s_last_touched, "
            "s.last_updated, "
            "e.id AS e_id, e.name, e.type, e.aliases, e.touches AS e_touches, "
            "e.last_touched AS e_last_touched, e.created_at, e.updated_at, "
            "1 - (s.embedding <=> $1::vector) AS similarity "
            "FROM summaries s "
            "JOIN entities e ON e.id = s.entity_id "
            "WHERE s.embedding IS NOT NULL "
            "ORDER BY s.embedding <=> $1::vector "
            "LIMIT $2",
            str(query_embedding),
            limit,
        )

        summary_ids = []
        entity_ids = []

        for r in rows:
            entity = Entity(
                id=r["e_id"], name=r["name"], type=r["type"],
                aliases=r["aliases"] or [], touches=r["e_touches"],
                last_touched=r["e_last_touched"], created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            summary = Summary(
                id=r["summary_id"], entity_id=r["entity_id"],
                content=r["summary_content"], fact_count=r["fact_count"],
                touches=r["s_touches"], last_touched=r["s_last_touched"],
                last_updated=r["last_updated"],
                entity_name=r["name"], entity_type=r["type"],
            )

            # Count active obligations and events for this entity
            ob_count = await pool.fetchval(
                "SELECT COUNT(*) FROM obligations "
                "WHERE $1 = ANY(entity_ids) AND status = 'active'",
                r["entity_id"],
            )
            ev_count = await pool.fetchval(
                "SELECT COUNT(*) FROM events "
                "WHERE $1 = ANY(entity_ids) AND event_date > NOW()",
                r["entity_id"],
            )

            results.append(RecallResultItem(
                summary=summary, entity=entity,
                active_obligation_count=ob_count or 0,
                active_event_count=ev_count or 0,
                similarity=r["similarity"],
            ))

            summary_ids.append(r["summary_id"])
            entity_ids.append(r["e_id"])

            # Track session hits for auto-promotion
            _session_hits[r["e_id"]] = _session_hits.get(r["e_id"], 0) + 1

        if summary_ids:
            await touch("summaries", summary_ids)
        if entity_ids:
            await touch("entities", entity_ids)

    # 3. Auto-promote: if entity appeared in top 2 more than once this session
    for item in results[:2]:
        eid = item.entity.id
        if _session_hits.get(eid, 0) > 1:
            try:
                await pool.execute(
                    "INSERT INTO working_memory (entity_id, reason, expires_at) "
                    "VALUES ($1, 'auto-promoted: frequent recall', NOW() + INTERVAL '7 days') "
                    "ON CONFLICT (entity_id) DO UPDATE SET "
                    "touches = working_memory.touches + 1, last_touched = NOW(), "
                    "expires_at = GREATEST(working_memory.expires_at, NOW() + INTERVAL '7 days')",
                    eid,
                )
            except Exception as e:
                logger.error(f"Auto-promote failed for {eid}: {e}")

    return RecallResult(working_memory=wm_entries, results=results).model_dump(mode="json")


async def get_facts(
    entity_name: str, category: str | None = None, include_expired: bool = False
) -> list[dict]:
    """Get facts for an entity by name or alias."""
    pool = await get_pool()

    # Resolve entity
    entity_row = await pool.fetchrow(
        "SELECT id FROM entities "
        "WHERE LOWER(name) = LOWER($1) OR LOWER($1) = ANY("
        "SELECT LOWER(unnest(aliases)))",
        entity_name,
    )
    if not entity_row:
        return {"error": f"Entity '{entity_name}' not found"}

    entity_id = entity_row["id"]
    query = "SELECT f.*, e.name AS entity_name FROM facts f JOIN entities e ON e.id = f.entity_id WHERE f.entity_id = $1"
    params: list = [entity_id]

    if not include_expired:
        query += " AND f.valid_until IS NULL"

    if category:
        params.append(category)
        query += f" AND f.category = ${len(params)}"

    query += " ORDER BY f.created_at DESC"

    rows = await pool.fetch(query, *params)
    facts = [Fact(**dict(r)) for r in rows]

    if rows:
        await touch("facts", [r["id"] for r in rows])
        await touch("entities", [entity_id])

    return [f.model_dump(mode="json") for f in facts]


async def get_working_memory() -> list[dict]:
    """Return non-expired working memory entries. Expire stale ones first."""
    pool = await get_pool()

    # Expire stale entries
    await pool.execute("DELETE FROM working_memory WHERE expires_at < NOW()")

    # Auto-activate entities linked to obligations due within 72h
    await pool.execute(
        "INSERT INTO working_memory (entity_id, reason, expires_at) "
        "SELECT DISTINCT unnest(o.entity_ids), "
        "'auto: obligation due within 72h', NOW() + INTERVAL '3 days' "
        "FROM obligations o "
        "WHERE o.status = 'active' AND o.due_date IS NOT NULL "
        "AND o.due_date BETWEEN NOW() AND NOW() + INTERVAL '72 hours' "
        "ON CONFLICT (entity_id) DO UPDATE SET "
        "touches = working_memory.touches + 1, last_touched = NOW()"
    )

    # Auto-activate entities linked to events within 48h
    await pool.execute(
        "INSERT INTO working_memory (entity_id, reason, expires_at) "
        "SELECT DISTINCT unnest(ev.entity_ids), "
        "'auto: event within 48h', NOW() + INTERVAL '2 days' "
        "FROM events ev "
        "WHERE ev.event_date BETWEEN NOW() AND NOW() + INTERVAL '48 hours' "
        "ON CONFLICT (entity_id) DO UPDATE SET "
        "touches = working_memory.touches + 1, last_touched = NOW()"
    )

    rows = await pool.fetch(
        "SELECT wm.*, e.name AS entity_name, e.type AS entity_type "
        "FROM working_memory wm "
        "JOIN entities e ON e.id = wm.entity_id "
        "ORDER BY wm.touches DESC"
    )

    entries = [WorkingMemoryEntry(**dict(r)) for r in rows]
    if rows:
        await touch("working_memory", [r["id"] for r in rows])

    return [e.model_dump(mode="json") for e in entries]


async def get_upcoming(days: int = 14) -> dict:
    """Events and active obligations due within N days."""
    pool = await get_pool()

    event_rows = await pool.fetch(
        "SELECT * FROM events "
        "WHERE event_date BETWEEN NOW() AND NOW() + ($1 || ' days')::INTERVAL "
        "ORDER BY event_date ASC",
        str(days),
    )
    events = [Event(**dict(r)) for r in event_rows]
    if event_rows:
        await touch("events", [r["id"] for r in event_rows])

    ob_rows = await pool.fetch(
        "SELECT * FROM obligations "
        "WHERE status = 'active' AND due_date IS NOT NULL "
        "AND due_date BETWEEN NOW() AND NOW() + ($1 || ' days')::INTERVAL "
        "ORDER BY due_date ASC",
        str(days),
    )
    obligations = [Obligation(**dict(r)) for r in ob_rows]
    if ob_rows:
        await touch("obligations", [r["id"] for r in ob_rows])

    return UpcomingResult(events=events, obligations=obligations).model_dump(mode="json")


async def get_obligations(
    status: str = "active", priority: int | None = None
) -> list[dict]:
    """Filtered obligations list."""
    pool = await get_pool()

    query = "SELECT * FROM obligations WHERE status = $1"
    params: list = [status]

    if priority is not None:
        params.append(priority)
        query += f" AND priority = ${len(params)}"

    query += " ORDER BY COALESCE(due_date, '9999-12-31'::timestamptz) ASC"

    rows = await pool.fetch(query, *params)
    obligations = [Obligation(**dict(r)) for r in rows]
    if rows:
        await touch("obligations", [r["id"] for r in rows])

    return [o.model_dump(mode="json") for o in obligations]


async def get_goals(
    horizon: str | None = None, status: str = "active"
) -> list[dict]:
    """Filtered goals list."""
    pool = await get_pool()

    query = "SELECT * FROM goals WHERE status = $1"
    params: list = [status]

    if horizon is not None:
        params.append(horizon)
        query += f" AND horizon = ${len(params)}"

    query += " ORDER BY created_at DESC"

    rows = await pool.fetch(query, *params)
    goals = [Goal(**dict(r)) for r in rows]
    if rows:
        await touch("goals", [r["id"] for r in rows])

    return [g.model_dump(mode="json") for g in goals]


async def recent_episodes(n: int = 5) -> list[dict]:
    """Last N conversation summaries, newest first."""
    pool = await get_pool()

    rows = await pool.fetch(
        "SELECT * FROM episodes ORDER BY occurred_at DESC LIMIT $1", n
    )
    episodes = [Episode(**dict(r)) for r in rows]
    if rows:
        await touch("episodes", [r["id"] for r in rows])

    return [e.model_dump(mode="json") for e in episodes]


async def search_facts(query: str, limit: int = 10) -> list[dict]:
    """Full-text search across fact content."""
    pool = await get_pool()

    # Use ILIKE for simple text search
    rows = await pool.fetch(
        "SELECT f.*, e.name AS entity_name FROM facts f "
        "JOIN entities e ON e.id = f.entity_id "
        "WHERE f.valid_until IS NULL AND f.content ILIKE '%' || $1 || '%' "
        "ORDER BY f.created_at DESC LIMIT $2",
        query,
        limit,
    )
    facts = [Fact(**dict(r)) for r in rows]
    if rows:
        await touch("facts", [r["id"] for r in rows])

    return [f.model_dump(mode="json") for f in facts]


async def get_relationship(entity_name: str) -> dict:
    """Get the relationship record for a named entity."""
    pool = await get_pool()

    entity_row = await pool.fetchrow(
        "SELECT id FROM entities "
        "WHERE LOWER(name) = LOWER($1) OR LOWER($1) = ANY("
        "SELECT LOWER(unnest(aliases)))",
        entity_name,
    )
    if not entity_row:
        return {"error": f"Entity '{entity_name}' not found"}

    row = await pool.fetchrow(
        "SELECT r.*, e.name AS entity_name FROM relationships r "
        "JOIN entities e ON e.id = r.entity_id "
        "WHERE r.entity_id = $1",
        entity_row["id"],
    )
    if not row:
        return {"error": f"No relationship found for '{entity_name}'"}

    rel = Relationship(**dict(row))
    await touch("relationships", [row["id"]])
    await touch("entities", [entity_row["id"]])

    return rel.model_dump(mode="json")
