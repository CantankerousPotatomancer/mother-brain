import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("mother-brain")


class Config:
    POSTGRES_HOST: str = os.environ["POSTGRES_HOST"]
    POSTGRES_PORT: int = int(os.environ.get("POSTGRES_PORT", "5432"))
    POSTGRES_DB: str = os.environ["POSTGRES_DB"]
    POSTGRES_USER: str = os.environ["POSTGRES_USER"]
    POSTGRES_PASSWORD: str = os.environ["POSTGRES_PASSWORD"]

    OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://ollama:11434")
    OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "nomic-embed-text")

    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

    MCP_SERVER_PORT: int = int(os.environ.get("MCP_SERVER_PORT", "8765"))
    MCP_LOG_LEVEL: str = os.environ.get("MCP_LOG_LEVEL", "INFO")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


config = Config()

logging.basicConfig(
    level=getattr(logging, config.MCP_LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
