from __future__ import annotations

from sqlalchemy import inspect, select, text
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


def test_embedding_migration_preserves_legacy_vectors_without_guessing_model(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    upgrade_database(database_url, "301c2a9fdade")
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organizations (id, slug, name) VALUES ('org', 'demo', 'Demo')")
        )
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, organization_id, document_type, title, source_filename) "
                "VALUES ('doc', 'org', 'contract', 'Contract', 'contract.md')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO document_chunks "
                "(id, document_id, chunk_index, content, metadata, embedding) "
                "VALUES ('chunk', 'doc', 0, 'delivery credit', '{}', '[1.0, 2.0]')"
            )
        )
    upgrade_database(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT embedding, embedding_model, embedding_dimensions FROM document_chunks")
        ).one()
        assert row == ("[1.0, 2.0]", None, None)
    downgrade_database(database_url, "301c2a9fdade")
    assert "embedding_model" not in {
        column["name"] for column in inspect(engine).get_columns("document_chunks")
    }
    engine.dispose()
