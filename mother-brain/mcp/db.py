import asyncpg
import logging
from uuid import UUID
from config import config

logger = logging.getLogger("mother-brain.db")

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        host=config.POSTGRES_HOST,
        port=config.POSTGRES_PORT,
        database=config.POSTGRES_DB,
        user=config.POSTGRES_USER,
        password=config.POSTGRES_PASSWORD,
        min_size=2,
        max_size=10,
    )
    logger.info("Database connection pool initialized")
    return _pool


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        return await init_pool()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")


_TOUCHABLE_TABLES = frozenset({
    "entities", "facts", "summaries", "episodes",
    "working_memory", "events", "obligations", "goals", "relationships",
})


async def touch(table: str, ids: list[UUID]) -> None:
    """Increment touches and update last_touched for a list of UUIDs."""
    if not ids:
        return
    if table not in _TOUCHABLE_TABLES:
        logger.error(f"Touch called with invalid table name: {table!r}")
        return
    try:
        pool = await get_pool()
        await pool.execute(
            f'UPDATE "{table}" SET touches = touches + 1, last_touched = NOW() '
            f"WHERE id = ANY($1::uuid[])",
            ids,
        )
    except Exception as e:
        logger.error(f"Touch tracking failed for {table}: {e}")
