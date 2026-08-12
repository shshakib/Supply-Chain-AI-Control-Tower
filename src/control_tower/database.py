from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from control_tower.config import get_settings
from control_tower.models import Base


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    engine_options = {}
    if url == "sqlite://":
        engine_options = {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    elif url.startswith("sqlite"):
        engine_options = {"connect_args": {"check_same_thread": False}}
    engine = create_engine(url, **engine_options)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
