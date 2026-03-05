import anthropic
import logging
from config import config

logger = logging.getLogger("memory-brain.llm")

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


async def generate_summary(
    entity_name: str, entity_type: str, facts: list[str]
) -> str | None:
    """Generate a summary of an entity from its facts using Claude Haiku."""
    if not config.ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY set, skipping summary generation")
        return None

    fact_list = "\n".join(f"- {f}" for f in facts)

    try:
        client = _get_client()
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system="You are maintaining a persistent memory system. Respond only with the summary text, no preamble, no markdown.",
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Generate a dense, specific summary of everything known about "
                        f"'{entity_name}' (type: {entity_type}) based on these facts. "
                        f"Write it as a briefing for someone who needs to get up to speed "
                        f"instantly. Include all concrete details, numbers, statuses, and "
                        f"decisions. Be specific, not vague.\n\n"
                        f"Facts:\n{fact_list}"
                    ),
                }
            ],
        )
        return message.content[0].text
    except Exception as e:
        logger.error(f"Summary generation failed for '{entity_name}': {e}")
        return None
