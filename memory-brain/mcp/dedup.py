import asyncpg
import logging
from uuid import UUID
from embeddings import embed
from models.schemas import DedupResult

logger = logging.getLogger("memory-brain.dedup")

ENTITY_SIMILARITY_THRESHOLD = 0.88
FACT_REPLACE_THRESHOLD = 0.90
FACT_FLAG_THRESHOLD = 0.75


async def resolve_entity(pool: asyncpg.Pool, name: str) -> UUID | None:
    """Resolve an entity by name, alias, or semantic similarity.

    Returns the entity UUID if found, None if the name is new.
    """
    # 1. Exact name match (case-insensitive)
    row = await pool.fetchrow(
        "SELECT id FROM entities WHERE LOWER(name) = LOWER($1)", name
    )
    if row:
        return row["id"]

    # 2. Alias match
    row = await pool.fetchrow(
        "SELECT id FROM entities WHERE LOWER($1) = ANY("
        "SELECT LOWER(unnest(aliases)))",
        name,
    )
    if row:
        return row["id"]

    # 3. Semantic similarity against summaries
    name_embedding = await embed(name)
    if name_embedding is not None:
        row = await pool.fetchrow(
            "SELECT s.entity_id, 1 - (s.embedding <=> $1::vector) AS similarity "
            "FROM summaries s "
            "WHERE s.embedding IS NOT NULL "
            "ORDER BY s.embedding <=> $1::vector "
            "LIMIT 1",
            str(name_embedding),
        )
        if row and row["similarity"] > ENTITY_SIMILARITY_THRESHOLD:
            logger.info(
                f"Entity '{name}' resolved via semantic similarity "
                f"(score={row['similarity']:.3f}) to entity {row['entity_id']}"
            )
            return row["entity_id"]

    return None


async def check_duplicate_fact(
    pool: asyncpg.Pool,
    entity_id: UUID,
    content: str,
    content_embedding: list[float] | None,
) -> DedupResult:
    """Check if a fact is a duplicate of an existing fact for this entity.

    Returns a DedupResult indicating whether to insert, replace, or flag.
    """
    if content_embedding is None:
        return DedupResult(action="insert")

    # Search existing active facts for this entity by embedding similarity.
    # Facts don't have their own embeddings stored, so we compare content textually
    # by embedding each active fact on the fly. For efficiency, we limit to recent facts.
    rows = await pool.fetch(
        "SELECT id, content FROM facts "
        "WHERE entity_id = $1 AND valid_until IS NULL "
        "ORDER BY created_at DESC LIMIT 50",
        entity_id,
    )

    if not rows:
        return DedupResult(action="insert")

    best_score = 0.0
    best_id: UUID | None = None

    # Batch embed existing facts for comparison
    from embeddings import embed as embed_text

    for row in rows:
        existing_embedding = await embed_text(row["content"])
        if existing_embedding is None:
            continue
        # Cosine similarity
        score = _cosine_similarity(content_embedding, existing_embedding)
        if score > best_score:
            best_score = score
            best_id = row["id"]

    if best_score > FACT_REPLACE_THRESHOLD:
        return DedupResult(action="replace", existing_id=best_id, score=best_score)
    elif best_score > FACT_FLAG_THRESHOLD:
        return DedupResult(action="flag", existing_id=best_id, score=best_score)
    else:
        return DedupResult(action="insert", score=best_score)


async def find_similar_entities(
    pool: asyncpg.Pool,
    name: str,
    name_embedding: list[float],
    threshold: float = ENTITY_SIMILARITY_THRESHOLD,
) -> list[dict]:
    """Find entities with summaries semantically similar to the given name."""
    rows = await pool.fetch(
        "SELECT s.entity_id, e.name, "
        "1 - (s.embedding <=> $1::vector) AS similarity "
        "FROM summaries s "
        "JOIN entities e ON e.id = s.entity_id "
        "WHERE s.embedding IS NOT NULL "
        "ORDER BY s.embedding <=> $1::vector "
        "LIMIT 5",
        str(name_embedding),
    )
    return [
        {"entity_id": r["entity_id"], "name": r["name"], "similarity": r["similarity"]}
        for r in rows
        if r["similarity"] > threshold
    ]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
