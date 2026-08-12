from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://control_tower:control_tower@localhost:5433/control_tower"
)


@dataclass(frozen=True)
class Settings:
    database_url: str
    document_dir: Path
    supervisor_model: str
    specialist_model: str
    embedding_model: str
    embedding_dimensions: int
    risk_mcp_enabled: bool
    risk_mcp_url: str
    risk_mcp_connect_timeout_seconds: float
    host: str
    port: int

    @property
    def openai_configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))


def get_settings() -> Settings:
    load_dotenv(override=False)
    return Settings(
        database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        document_dir=Path(os.getenv("CONTROL_TOWER_DOCUMENT_DIR", "synthetic_documents")),
        supervisor_model=os.getenv("CONTROL_TOWER_SUPERVISOR_MODEL", "gpt-5.6-terra"),
        specialist_model=os.getenv("CONTROL_TOWER_SPECIALIST_MODEL", "gpt-5.6-luna"),
        embedding_model=os.getenv("CONTROL_TOWER_EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dimensions=int(os.getenv("CONTROL_TOWER_EMBEDDING_DIMENSIONS", "384")),
        risk_mcp_enabled=_env_bool("CONTROL_TOWER_RISK_MCP_ENABLED", default=True),
        risk_mcp_url=os.getenv(
            "CONTROL_TOWER_RISK_MCP_URL",
            "http://127.0.0.1:8010/mcp",
        ),
        risk_mcp_connect_timeout_seconds=float(
            os.getenv("CONTROL_TOWER_RISK_MCP_CONNECT_TIMEOUT_SECONDS", "2")
        ),
        host=os.getenv("CONTROL_TOWER_HOST", "127.0.0.1"),
        port=int(os.getenv("CONTROL_TOWER_PORT", "8000")),
    )


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}
