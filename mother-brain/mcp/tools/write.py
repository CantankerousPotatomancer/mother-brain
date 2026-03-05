import asyncio
import logging
from uuid import UUID
from datetime import datetime
from db import get_pool, touch
from embeddings import embed
from llm import generate_summary
from dedup import resolve_entity, check_duplicate_fact

logger = logging.getLogger("mother-brain.tools.write")


async def _regenerate_summary(entity_id: UUID, entity_name: str, entity_type: str) -> None:
    """Background task: regenerate summary for an entity after a fact write."""
    try:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT content FROM facts "
            "WHERE entity_id = $1 AND valid_until IS NULL "
            "ORDER BY created_at DESC",
            entity_id,
        )
        if not rows:
            return

        facts = [r["content"] for r in rows]
        summary_text = await generate_summary(entity_name, entity_type, facts)
        if summary_text is None:
            return

        summary_embedding = await embed(summary_text)

        await pool.execute(
            "INSERT INTO summaries (entity_id, content, embedding, fact_count, last_updated) "
            "VALUES ($1, $2, $3, $4, NOW()) "
            "ON CONFLICT (entity_id) DO UPDATE SET "
            "content = $2, embedding = $3, fact_count = $4, last_updated = NOW()",
            entity_id,
            summary_text,
            str(summary_embedding) if summary_embedding else None,
            len(facts),
        )
        logger.info(f"Summary regenerated for entity '{entity_name}'")
    except Exception as e:
        logger.error(f"Summary regeneration failed for '{entity_name}': {e}")


async def remember(
    entity_name: str,
    content: str,
    category: str,
    confidence: float = 1.0,
    source: str = "conversation",
) -> dict:
    """Primary write tool. Resolves/creates entity, deduplicates fact, writes, triggers summary regen."""
    pool = await get_pool()

    # 1. Resolve or create entity
    entity_id = await resolve_entity(pool, entity_name)
    if entity_id is None:
        row = await pool.fetchrow(
            "INSERT INTO entities (name, type) VALUES ($1, 'concept') "
            "RETURNING id, type",
            entity_name,
        )
        entity_id = row["id"]
        entity_type = row["type"]
        logger.info(f"Created new entity '{entity_name}' ({entity_id})")
    else:
        entity_row = await pool.fetchrow(
            "SELECT type FROM entities WHERE id = $1", entity_id
        )
        entity_type = entity_row["type"]
        await touch("entities", [entity_id])

    # 2. Deduplicate fact
    content_embedding = await embed(content)
    dedup_result = await check_duplicate_fact(pool, entity_id, content, content_embedding)

    if dedup_result.action == "replace" and dedup_result.existing_id:
        await pool.execute(
            "UPDATE facts SET valid_until = NOW() WHERE id = $1",
            dedup_result.existing_id,
        )
        logger.info(
            f"Replaced fact {dedup_result.existing_id} (similarity={dedup_result.score:.3f})"
        )

    # 3. Write new fact
    fact_row = await pool.fetchrow(
        "INSERT INTO facts (entity_id, content, category, confidence, source) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        entity_id, content, category, confidence, source,
    )
    fact_id = fact_row["id"]

    # 4. Fire-and-forget summary regeneration
    entity_name_resolved = await pool.fetchval(
        "SELECT name FROM entities WHERE id = $1", entity_id
    )
    asyncio.create_task(
        _regenerate_summary(entity_id, entity_name_resolved, entity_type)
    )

    action_taken = dedup_result.action
    if dedup_result.action == "flag":
        action_taken = f"inserted (similar fact exists, score={dedup_result.score:.3f})"

    return {
        "status": "ok",
        "fact_id": str(fact_id),
        "entity_id": str(entity_id),
        "entity_name": entity_name_resolved,
        "action": action_taken,
    }


async def upsert_entity(
    name: str, type: str, aliases: list[str] | None = None
) -> dict:
    """Create or update entity with dedup check."""
    pool = await get_pool()

    existing_id = await resolve_entity(pool, name)
    if existing_id:
        if aliases:
            await pool.execute(
                "UPDATE entities SET aliases = ARRAY(SELECT DISTINCT unnest(aliases || $1)), updated_at = NOW() "
                "WHERE id = $2",
                aliases, existing_id,
            )
        await touch("entities", [existing_id])
        entity = await pool.fetchrow("SELECT * FROM entities WHERE id = $1", existing_id)
        return {
            "status": "updated",
            "id": str(existing_id),
            "name": entity["name"],
            "type": entity["type"],
        }

    row = await pool.fetchrow(
        "INSERT INTO entities (name, type, aliases) VALUES ($1, $2, $3) RETURNING *",
        name, type, aliases or [],
    )
    return {
        "status": "created",
        "id": str(row["id"]),
        "name": row["name"],
        "type": row["type"],
    }


async def invalidate_fact(fact_id: str, reason: str | None = None) -> dict:
    """Soft-delete a fact by setting valid_until."""
    pool = await get_pool()

    try:
        uid = UUID(fact_id)
    except ValueError:
        return {"error": "Invalid fact_id format"}

    result = await pool.execute(
        "UPDATE facts SET valid_until = NOW() WHERE id = $1 AND valid_until IS NULL",
        uid,
    )

    if result == "UPDATE 0":
        return {"error": "Fact not found or already invalidated"}

    if reason:
        logger.info(f"Fact {fact_id} invalidated: {reason}")

    return {"status": "ok", "fact_id": fact_id, "reason": reason}


async def merge_entities(keep_id: str, discard_id: str) -> dict:
    """Merge two entities: migrate all linked data from discard to keep."""
    pool = await get_pool()

    try:
        keep_uuid = UUID(keep_id)
        discard_uuid = UUID(discard_id)
    except ValueError:
        return {"error": "Invalid UUID format"}

    keep_entity = await pool.fetchrow("SELECT * FROM entities WHERE id = $1", keep_uuid)
    discard_entity = await pool.fetchrow("SELECT * FROM entities WHERE id = $1", discard_uuid)

    if not keep_entity or not discard_entity:
        return {"error": "One or both entities not found"}

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Migrate facts
            await conn.execute(
                "UPDATE facts SET entity_id = $1 WHERE entity_id = $2",
                keep_uuid, discard_uuid,
            )

            # Migrate relationships
            await conn.execute(
                "UPDATE relationships SET entity_id = $1 WHERE entity_id = $2",
                keep_uuid, discard_uuid,
            )

            # Update entity_ids arrays in obligations, goals, events, episodes
            for table in ["obligations", "goals", "events", "episodes"]:
                await conn.execute(
                    f"UPDATE {table} SET entity_ids = array_replace(entity_ids, $1, $2) "
                    f"WHERE $1 = ANY(entity_ids)",
                    discard_uuid, keep_uuid,
                )

            # Copy aliases from discard to keep
            discard_aliases = discard_entity["aliases"] or []
            new_aliases = discard_aliases + [discard_entity["name"]]
            await conn.execute(
                "UPDATE entities SET aliases = ARRAY(SELECT DISTINCT unnest(aliases || $1)), updated_at = NOW() "
                "WHERE id = $2",
                new_aliases, keep_uuid,
            )

            # Remove working memory for discard
            await conn.execute(
                "DELETE FROM working_memory WHERE entity_id = $1", discard_uuid
            )

            # Remove summary for discard
            await conn.execute(
                "DELETE FROM summaries WHERE entity_id = $1", discard_uuid
            )

            # Delete discarded entity
            await conn.execute(
                "DELETE FROM entities WHERE id = $1", discard_uuid
            )

    # Trigger summary regen for kept entity
    asyncio.create_task(
        _regenerate_summary(keep_uuid, keep_entity["name"], keep_entity["type"])
    )

    return {
        "status": "ok",
        "kept": {"id": str(keep_uuid), "name": keep_entity["name"]},
        "discarded": {"id": str(discard_uuid), "name": discard_entity["name"]},
    }


async def _resolve_entity_ids(pool, entity_names: list[str] | None) -> list[UUID]:
    """Resolve a list of entity names to UUIDs, creating as needed."""
    if not entity_names:
        return []

    ids = []
    for name in entity_names:
        eid = await resolve_entity(pool, name)
        if eid is None:
            row = await pool.fetchrow(
                "INSERT INTO entities (name, type) VALUES ($1, 'concept') "
                "ON CONFLICT (name) DO UPDATE SET updated_at = NOW() "
                "RETURNING id",
                name,
            )
            eid = row["id"]
        ids.append(eid)
    return ids


async def add_event(
    title: str,
    event_date: str,
    category: str,
    description: str | None = None,
    recurrence: str = "none",
    entity_names: list[str] | None = None,
) -> dict:
    """Add a date-based event."""
    pool = await get_pool()

    try:
        parsed_date = datetime.fromisoformat(event_date)
    except ValueError:
        return {"error": f"Invalid date format: {event_date}. Use ISO format."}

    entity_ids = await _resolve_entity_ids(pool, entity_names)

    row = await pool.fetchrow(
        "INSERT INTO events (title, description, entity_ids, event_date, recurrence, category) "
        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        title, description, entity_ids, parsed_date, recurrence, category,
    )

    return {"status": "ok", "event_id": str(row["id"]), "title": title}


async def add_obligation(
    title: str,
    description: str | None = None,
    priority: int = 2,
    due_date: str | None = None,
    entity_names: list[str] | None = None,
) -> dict:
    """Add an actionable commitment."""
    pool = await get_pool()

    parsed_date = None
    if due_date:
        try:
            parsed_date = datetime.fromisoformat(due_date)
        except ValueError:
            return {"error": f"Invalid date format: {due_date}. Use ISO format."}

    entity_ids = await _resolve_entity_ids(pool, entity_names)

    row = await pool.fetchrow(
        "INSERT INTO obligations (title, description, entity_ids, priority, due_date) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        title, description, entity_ids, priority, parsed_date,
    )

    return {"status": "ok", "obligation_id": str(row["id"]), "title": title}


async def update_obligation(
    obligation_id: str,
    status: str | None = None,
    priority: int | None = None,
    due_date: str | None = None,
) -> dict:
    """Update obligation status or metadata."""
    pool = await get_pool()

    try:
        uid = UUID(obligation_id)
    except ValueError:
        return {"error": "Invalid obligation_id format"}

    existing = await pool.fetchrow("SELECT * FROM obligations WHERE id = $1", uid)
    if not existing:
        return {"error": "Obligation not found"}

    updates = []
    params: list = []
    idx = 1

    if status is not None:
        params.append(status)
        updates.append(f"status = ${idx}")
        idx += 1
        if status == "completed":
            updates.append("completed_at = NOW()")

    if priority is not None:
        params.append(priority)
        updates.append(f"priority = ${idx}")
        idx += 1

    if due_date is not None:
        try:
            parsed = datetime.fromisoformat(due_date)
        except ValueError:
            return {"error": f"Invalid date format: {due_date}"}
        params.append(parsed)
        updates.append(f"due_date = ${idx}")
        idx += 1

    if not updates:
        return {"error": "No updates provided"}

    updates.append("updated_at = NOW()")
    params.append(uid)

    await pool.execute(
        f"UPDATE obligations SET {', '.join(updates)} WHERE id = ${idx}",
        *params,
    )

    return {"status": "ok", "obligation_id": obligation_id}


async def add_goal(
    title: str,
    horizon: str,
    description: str | None = None,
    parent_title: str | None = None,
    entity_names: list[str] | None = None,
) -> dict:
    """Add a goal."""
    pool = await get_pool()

    parent_id = None
    if parent_title:
        parent_row = await pool.fetchrow(
            "SELECT id FROM goals WHERE LOWER(title) = LOWER($1) AND status = 'active'",
            parent_title,
        )
        if parent_row:
            parent_id = parent_row["id"]

    entity_ids = await _resolve_entity_ids(pool, entity_names)

    row = await pool.fetchrow(
        "INSERT INTO goals (title, description, entity_ids, horizon, parent_id) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        title, description, entity_ids, horizon, parent_id,
    )

    return {"status": "ok", "goal_id": str(row["id"]), "title": title}


async def upsert_relationship(
    entity_name: str,
    relationship: str,
    context: str | None = None,
    notes: str | None = None,
    cadence: str | None = None,
) -> dict:
    """Create or update a relationship record for a person/org entity."""
    pool = await get_pool()

    entity_row = await pool.fetchrow(
        "SELECT id FROM entities "
        "WHERE LOWER(name) = LOWER($1) OR LOWER($1) = ANY("
        "SELECT LOWER(unnest(aliases)))",
        entity_name,
    )
    if not entity_row:
        # Create the entity as a person
        entity_row = await pool.fetchrow(
            "INSERT INTO entities (name, type) VALUES ($1, 'person') RETURNING id",
            entity_name,
        )

    entity_id = entity_row["id"]

    existing = await pool.fetchrow(
        "SELECT id FROM relationships WHERE entity_id = $1", entity_id
    )

    if existing:
        await pool.execute(
            "UPDATE relationships SET relationship = $1, context = COALESCE($2, context), "
            "notes = COALESCE($3, notes), cadence = COALESCE($4, cadence), updated_at = NOW() "
            "WHERE entity_id = $5",
            relationship, context, notes, cadence, entity_id,
        )
        return {"status": "updated", "entity_name": entity_name}

    await pool.execute(
        "INSERT INTO relationships (entity_id, relationship, context, notes, cadence) "
        "VALUES ($1, $2, $3, $4, $5)",
        entity_id, relationship, context, notes, cadence,
    )

    return {"status": "created", "entity_name": entity_name}


async def log_episode(
    title: str, summary: str, entity_names: list[str] | None = None
) -> dict:
    """Log a conversation summary as an episode."""
    pool = await get_pool()

    entity_ids = await _resolve_entity_ids(pool, entity_names)
    summary_embedding = await embed(summary)

    row = await pool.fetchrow(
        "INSERT INTO episodes (title, summary, embedding, entity_ids) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        title,
        summary,
        str(summary_embedding) if summary_embedding else None,
        entity_ids,
    )

    return {"status": "ok", "episode_id": str(row["id"]), "title": title}


async def activate(
    entity_name: str, reason: str | None = None, days: int = 7
) -> dict:
    """Push an entity into working memory with an expiry."""
    pool = await get_pool()

    entity_row = await pool.fetchrow(
        "SELECT id FROM entities "
        "WHERE LOWER(name) = LOWER($1) OR LOWER($1) = ANY("
        "SELECT LOWER(unnest(aliases)))",
        entity_name,
    )
    if not entity_row:
        return {"error": f"Entity '{entity_name}' not found"}

    entity_id = entity_row["id"]

    await pool.execute(
        "INSERT INTO working_memory (entity_id, reason, expires_at) "
        "VALUES ($1, $2, NOW() + ($3 || ' days')::INTERVAL) "
        "ON CONFLICT (entity_id) DO UPDATE SET "
        "reason = COALESCE($2, working_memory.reason), "
        "touches = working_memory.touches + 1, last_touched = NOW(), "
        "expires_at = GREATEST(working_memory.expires_at, NOW() + ($3 || ' days')::INTERVAL)",
        entity_id, reason, str(days),
    )

    return {"status": "ok", "entity_name": entity_name, "days": days}
