from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def migration_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parent / "migrations"),
    )
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["database_url"] = database_url
    return config


def upgrade_database(database_url: str, revision: str = "head") -> None:
    command.upgrade(migration_config(database_url), revision)


def downgrade_database(database_url: str, revision: str = "base") -> None:
    command.downgrade(migration_config(database_url), revision)
