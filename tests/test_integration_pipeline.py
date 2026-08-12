from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from control_tower.database import create_database_engine
from control_tower.integrations.risk_mcp_client import ALL_RISK_MCP_TOOLS, RiskMCPConnector
from control_tower.models import Shipment
from control_tower.schema import downgrade_database, upgrade_database
from control_tower.synthetic import SyntheticDataGenerator

pytestmark = pytest.mark.integration


def test_postgresql_migration_seed_and_pgvector() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    if not (make_url(database_url).database or "").endswith("_test"):
        pytest.fail("Integration migrations require a database name ending in '_test'.")

    downgrade_database(database_url)
    upgrade_database(database_url)
    engine = create_database_engine(database_url)

    with engine.connect() as connection:
        extension = connection.scalar(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
        column_type = connection.scalar(
            text(
                "SELECT format_type(atttypid, atttypmod) "
                "FROM pg_attribute "
                "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
            )
        )
    assert extension == "vector"
    assert column_type == "vector(384)"

    with Session(engine) as session:
        summary = SyntheticDataGenerator(session, seed=42).generate()
        session.commit()
        shipment_count = session.scalar(select(func.count(Shipment.id)))

    assert summary.shipments == 181
    assert shipment_count == 181
    engine.dispose()


def test_live_mcp_transport_advertises_required_tools() -> None:
    risk_mcp_url = os.getenv("TEST_RISK_MCP_URL")
    if not risk_mcp_url:
        pytest.skip("TEST_RISK_MCP_URL is not configured")

    async def verify() -> None:
        connector = RiskMCPConnector(
            enabled=True,
            url=risk_mcp_url,
            connect_timeout_seconds=5,
            connect_attempts=1,
        )
        try:
            status = await connector.connect()
            assert status.state == "connected"
            assert set(status.tools) == ALL_RISK_MCP_TOOLS
        finally:
            await connector.close()

    asyncio.run(verify())
