from __future__ import annotations

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from control_tower.database import create_database_engine
from control_tower.models import Organization
from control_tower.schema import downgrade_database, upgrade_database
from control_tower.synthetic import SyntheticDataGenerator


def test_migrations_create_seed_and_remove_sqlite_schema(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration-test.db'}"

    upgrade_database(database_url)
    upgrade_database(database_url)
    engine = create_database_engine(database_url)

    tables = set(inspect(engine).get_table_names())
    assert {"alembic_version", "organizations", "shipments", "document_chunks"} <= tables

    with Session(engine) as session:
        SyntheticDataGenerator(session, seed=42).generate()
        session.commit()
        assert session.scalar(select(Organization.slug)) == "meridian-assembly"

    engine.dispose()
    downgrade_database(database_url)
    downgraded_engine = create_database_engine(database_url)
    assert "organizations" not in inspect(downgraded_engine).get_table_names()
    downgraded_engine.dispose()
