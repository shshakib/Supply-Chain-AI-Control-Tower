from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DATABASE_URL = "postgresql+psycopg://supplyscope:supplyscope@localhost:5433/supplyscope"


@dataclass(frozen=True)
class Settings:
    database_url: str
    document_dir: Path
    supervisor_model: str
    specialist_model: str
    embedding_model: str
    embedding_dimensions: int
    host: str
    port: int

    @property
    def openai_configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))


def get_settings() -> Settings:
    load_dotenv(override=False)
    return Settings(
        database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        document_dir=Path(os.getenv("SUPPLYSCOPE_DOCUMENT_DIR", "synthetic_documents")),
        supervisor_model=os.getenv("SUPPLYSCOPE_SUPERVISOR_MODEL", "gpt-5.6-terra"),
        specialist_model=os.getenv("SUPPLYSCOPE_SPECIALIST_MODEL", "gpt-5.6-luna"),
        embedding_model=os.getenv("SUPPLYSCOPE_EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dimensions=int(os.getenv("SUPPLYSCOPE_EMBEDDING_DIMENSIONS", "384")),
        host=os.getenv("SUPPLYSCOPE_HOST", "127.0.0.1"),
        port=int(os.getenv("SUPPLYSCOPE_PORT", "8000")),
    )
