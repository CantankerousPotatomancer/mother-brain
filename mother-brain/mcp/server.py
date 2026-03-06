import asyncio
import logging
from contextlib import asynccontextmanager
from typing import get_args
from fastmcp import FastMCP
from config import config
from db import init_pool, close_pool
from embeddings import close_client as close_embeddings_client
from tools import read, write
from models.schemas import (
    EntityType, FactCategory, FactSource,
    ObligationStatus, GoalHorizon, GoalStatus,
    EventRecurrence, EventCategory,
)

logger = logging.getLogger("mother-brain.server")

# Build sets from Literal types for fast validation
_ENTITY_TYPES = set(get_args(EntityType))
_FACT_CATEGORIES = set(get_args(FactCategory))
_FACT_SOURCES = set(get_args(FactSource))
_OBLIGATION_STATUSES = set(get_args(ObligationStatus))
_GOAL_HORIZONS = set(get_args(GoalHorizon))
_GOAL_STATUSES = set(get_args(GoalStatus))
_EVENT_RECURRENCES = set(get_args(EventRecurrence))
_EVENT_CATEGORIES = set(get_args(EventCategory))


def _validate_enum(value: str, allowed: set[str], field_name: str) -> str | None:
    """Validate a value against an allowed set. Returns error message or None."""
    if value not in allowed:
        return f"Invalid {field_name}: {value!r}. Allowed: {', '.join(sorted(allowed))}"
    return None


@asynccontextmanager
async def lifespan(app):
    """Manage startup and shutdown of the MCP server."""
    logger.info("Starting Mother Brain MCP server...")
    pool = await init_pool()

    # Verify postgres connection
    async with pool.acquire() as conn:
        version = await conn.fetchval("SELECT version()")
        logger.info(f"Connected to PostgreSQL: {version[:60]}...")

    logger.info("Mother Brain MCP server ready")
    yield
    logger.info("Shutting down Mother Brain MCP server...")
    await close_pool()
    await close_embeddings_client()
    logger.info("Mother Brain MCP server stopped")


mcp = FastMCP(
    "Mother Brain",
    instructions="Persistent structured memory for Claude — facts, summaries, events, obligations, and goals with semantic search.",
    lifespan=lifespan,
)

# --- Read Tools ---

@mcp.tool()
async def recall(query: str, limit: int = 5) -> dict:
    """Primary retrieval. Checks working memory, then searches summaries by semantic similarity.
    Returns matching entities with their summaries, obligation counts, and event counts."""
    limit = max(1, min(limit, 50))
    return await read.recall(query, limit)


@mcp.tool()
async def get_facts(entity_name: str, category: str | None = None, include_expired: bool = False) -> list | dict:
    """Get facts for an entity by name or alias. Optionally filter by category.
    Set include_expired=True to see invalidated facts."""
    if category is not None:
        if err := _validate_enum(category, _FACT_CATEGORIES, "category"):
            return {"error": err}
    return await read.get_facts(entity_name, category, include_expired)


@mcp.tool()
async def get_working_memory() -> list:
    """Return active working memory entries. Auto-expires stale entries and
    auto-promotes entities with upcoming obligations or events."""
    return await read.get_working_memory()


@mcp.tool()
async def get_upcoming(days: int = 14) -> dict:
    """Return events and active obligations due within the next N days, sorted by date."""
    days = max(1, min(days, 365))
    return await read.get_upcoming(days)


@mcp.tool()
async def get_obligations(status: str = "active", priority: int | None = None) -> list:
    """Return obligations filtered by status and optionally priority."""
    if err := _validate_enum(status, _OBLIGATION_STATUSES, "status"):
        return {"error": err}
    if priority is not None and not 1 <= priority <= 5:
        return {"error": "priority must be between 1 and 5"}
    return await read.get_obligations(status, priority)


@mcp.tool()
async def get_goals(horizon: str | None = None, status: str = "active") -> list:
    """Return goals, optionally filtered by horizon (immediate/short/medium/long/life)."""
    if err := _validate_enum(status, _GOAL_STATUSES, "status"):
        return {"error": err}
    if horizon is not None:
        if err := _validate_enum(horizon, _GOAL_HORIZONS, "horizon"):
            return {"error": err}
    return await read.get_goals(horizon, status)


@mcp.tool()
async def recent_episodes(n: int = 5) -> list:
    """Return the last N conversation summaries, newest first."""
    n = max(1, min(n, 100))
    return await read.recent_episodes(n)


@mcp.tool()
async def search_facts(query: str, limit: int = 10) -> list:
    """Keyword search across raw fact content. Splits multi-word queries into individual terms
    and matches facts containing ALL terms (falls back to ANY term if no results).
    Best for exact name, date, or identifier lookups. Use `recall` for semantic/conceptual queries."""
    limit = max(1, min(limit, 100))
    return await read.search_facts(query, limit)


@mcp.tool()
async def get_relationship(entity_name: str) -> dict:
    """Return the relationship record for a named person or organization entity."""
    return await read.get_relationship(entity_name)


# --- Write Tools ---

@mcp.tool()
async def remember(entity_name: str, content: str, category: str, confidence: float = 1.0, source: str = "conversation") -> dict:
    """Store a fact about an entity. Automatically deduplicates entities and facts,
    and triggers background summary regeneration. Categories: status, decision, preference,
    technical, personal, relationship, financial, goal, other."""
    if err := _validate_enum(category, _FACT_CATEGORIES, "category"):
        return {"error": err}
    if err := _validate_enum(source, _FACT_SOURCES, "source"):
        return {"error": err}
    if not 0.0 <= confidence <= 1.0:
        return {"error": "confidence must be between 0.0 and 1.0"}
    return await write.remember(entity_name, content, category, confidence, source)


@mcp.tool()
async def upsert_entity(name: str, type: str, aliases: list[str] | None = None) -> dict:
    """Create or update an entity. Types: self, project, person, system, organization, concept, reference.
    Checks for near-duplicates before creating."""
    if err := _validate_enum(type, _ENTITY_TYPES, "type"):
        return {"error": err}
    return await write.upsert_entity(name, type, aliases)


@mcp.tool()
async def invalidate_fact(fact_id: str, reason: str | None = None) -> dict:
    """Soft-delete a fact by setting its valid_until timestamp. Never hard-deletes."""
    return await write.invalidate_fact(fact_id, reason)


@mcp.tool()
async def merge_entities(keep_id: str, discard_id: str) -> dict:
    """Merge two entities: migrates all facts, relationships, obligations, and events
    from the discarded entity to the kept entity. The discarded entity's name becomes an alias."""
    return await write.merge_entities(keep_id, discard_id)


@mcp.tool()
async def add_event(title: str, event_date: str, category: str, description: str | None = None, recurrence: str = "none", entity_names: list[str] | None = None) -> dict:
    """Add a date-based event. Categories: deadline, birthday, anniversary, appointment, release, reminder, other.
    Recurrence: none, daily, weekly, monthly, yearly. Date format: ISO 8601."""
    if err := _validate_enum(category, _EVENT_CATEGORIES, "category"):
        return {"error": err}
    if err := _validate_enum(recurrence, _EVENT_RECURRENCES, "recurrence"):
        return {"error": err}
    return await write.add_event(title, event_date, category, description, recurrence, entity_names)


@mcp.tool()
async def add_obligation(title: str, description: str | None = None, priority: int = 2, due_date: str | None = None, entity_names: list[str] | None = None) -> dict:
    """Add an actionable commitment. Priority 1-5 (1=highest). Due date in ISO format."""
    if not 1 <= priority <= 5:
        return {"error": "priority must be between 1 and 5"}
    return await write.add_obligation(title, description, priority, due_date, entity_names)


@mcp.tool()
async def update_obligation(obligation_id: str, status: str | None = None, priority: int | None = None, due_date: str | None = None) -> dict:
    """Update an obligation's status (active/completed/deferred/dropped), priority, or due date."""
    if status is not None:
        if err := _validate_enum(status, _OBLIGATION_STATUSES, "status"):
            return {"error": err}
    if priority is not None and not 1 <= priority <= 5:
        return {"error": "priority must be between 1 and 5"}
    return await write.update_obligation(obligation_id, status, priority, due_date)


@mcp.tool()
async def add_goal(title: str, horizon: str, description: str | None = None, parent_title: str | None = None, entity_names: list[str] | None = None) -> dict:
    """Add a goal. Horizon: immediate, short, medium, long, life. Optionally nest under a parent goal by title."""
    if err := _validate_enum(horizon, _GOAL_HORIZONS, "horizon"):
        return {"error": err}
    return await write.add_goal(title, horizon, description, parent_title, entity_names)


@mcp.tool()
async def upsert_relationship(entity_name: str, relationship: str, context: str | None = None, notes: str | None = None, cadence: str | None = None) -> dict:
    """Create or update a relationship record for a person or organization.
    Relationship describes how you know them. Cadence is how often you interact."""
    return await write.upsert_relationship(entity_name, relationship, context, notes, cadence)


@mcp.tool()
async def log_episode(title: str, summary: str, entity_names: list[str] | None = None) -> dict:
    """Log a conversation summary as an episode. Call at the end of significant sessions
    to build temporal context."""
    return await write.log_episode(title, summary, entity_names)


@mcp.tool()
async def activate(entity_name: str, reason: str | None = None, days: int = 7) -> dict:
    """Push an entity into working memory so it appears in every recall check.
    Expires after the specified number of days."""
    days = max(1, min(days, 365))
    return await write.activate(entity_name, reason, days)


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=config.MCP_SERVER_PORT,
        stateless_http=True,
    )
