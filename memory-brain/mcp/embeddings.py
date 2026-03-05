import httpx
import logging
from config import config

logger = logging.getLogger("memory-brain.embeddings")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def embed(text: str) -> list[float] | None:
    """Generate an embedding for the given text via Ollama. Returns None on failure."""
    try:
        client = _get_client()
        resp = await client.post(
            f"{config.OLLAMA_URL}/api/embeddings",
            json={"model": config.OLLAMA_MODEL, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return None


async def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Generate embeddings for multiple texts. Returns None for any that fail."""
    results = []
    for text in texts:
        results.append(await embed(text))
    return results


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
