from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.orm import Session

from supplyscope.access import AccessService
from supplyscope.agent_service import AgentService, MissingOpenAIConfiguration
from supplyscope.agents.llm import build_agent_system, list_delayed_shipments
from supplyscope.config import get_settings
from supplyscope.synthetic import DEMO_AS_OF


def test_supervisor_exposes_four_specialists() -> None:
    supervisor = build_agent_system(get_settings())

    assert [tool.name for tool in supervisor.tools] == [
        "ask_shipment_specialist",
        "ask_inventory_specialist",
        "ask_supplier_risk_specialist",
        "ask_contracts_compliance_specialist",
    ]


def test_function_tool_schema_does_not_expose_local_access_context() -> None:
    properties = list_delayed_shipments.params_json_schema["properties"]

    assert "ctx" not in properties
    assert set(properties) == {"horizon_days", "warehouse_code"}


def test_llm_service_fails_clearly_without_api_key(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    access = AccessService(session).resolve(
        "noah.east@supplyscope.demo",
        "meridian-assembly",
    )

    with pytest.raises(MissingOpenAIConfiguration, match="OPENAI_API_KEY"):
        asyncio.run(
            AgentService(get_settings()).ask(
                session,
                access,
                question="Which shipments are late?",
                as_of=DEMO_AS_OF,
            )
        )
