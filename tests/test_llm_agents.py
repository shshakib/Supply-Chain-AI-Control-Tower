from __future__ import annotations

import asyncio

import pytest
from agents import Agent
from sqlalchemy.orm import Session

from control_tower.access import AccessService
from control_tower.agent_service import AgentService, MissingOpenAIConfiguration
from control_tower.agents.llm import (
    EvidenceItem,
    SpecialistReport,
    _agent_output_details,
    _delegated_task,
    build_agent_system,
    list_delayed_shipments,
)
from control_tower.config import get_settings
from control_tower.synthetic import DEMO_AS_OF


def test_supervisor_exposes_four_specialists() -> None:
    supervisor = build_agent_system(get_settings())

    assert [tool.name for tool in supervisor.tools] == [
        "ask_shipment_specialist",
        "ask_inventory_specialist",
        "ask_supplier_risk_specialist",
        "ask_contracts_compliance_specialist",
    ]


def test_agents_use_their_configured_models(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = {
        "CONTROL_TOWER_SUPERVISOR_MODEL": "supervisor-model",
        "CONTROL_TOWER_SHIPMENT_MODEL": "shipment-model",
        "CONTROL_TOWER_INVENTORY_MODEL": "inventory-model",
        "CONTROL_TOWER_SUPPLIER_RISK_MODEL": "supplier-risk-model",
        "CONTROL_TOWER_CONTRACTS_MODEL": "contracts-model",
    }
    for variable, model in configured.items():
        monkeypatch.setenv(variable, model)

    captured: dict[str, str] = {}
    original_as_tool = Agent.as_tool

    def capture_model(agent, *args, **kwargs):
        captured[agent.name] = agent.model
        return original_as_tool(agent, *args, **kwargs)

    monkeypatch.setattr(Agent, "as_tool", capture_model)
    supervisor = build_agent_system(get_settings())

    assert supervisor.model == "supervisor-model"
    assert captured == {
        "Shipment specialist": "shipment-model",
        "Inventory specialist": "inventory-model",
        "Supplier risk specialist": "supplier-risk-model",
        "Contracts and compliance specialist": "contracts-model",
    }


def test_function_tool_schema_does_not_expose_local_access_context() -> None:
    properties = list_delayed_shipments.params_json_schema["properties"]

    assert "ctx" not in properties
    assert set(properties) == {"horizon_days", "warehouse_code"}


def test_public_specialist_exchange_contains_task_and_structured_result() -> None:
    task = _delegated_task(
        {"input": "Check whether shipment SS-CRITICAL-001 threatens Toronto production."}
    )
    report = SpecialistReport(
        domain="shipments",
        summary="The shipment is nine days late.",
        evidence=[
            EvidenceItem(
                reference="shipment:SS-CRITICAL-001",
                claim="The revised arrival is nine days after the contractual date.",
            )
        ],
        limitations=["The carrier has not confirmed a recovery date."],
    )

    assert task == "Check whether shipment SS-CRITICAL-001 threatens Toronto production."
    assert _agent_output_details(report) == {
        "domain": "shipments",
        "summary": "The shipment is nine days late.",
        "evidence_count": 1,
        "evidence": [
            {
                "reference": "shipment:SS-CRITICAL-001",
                "claim": "The revised arrival is nine days after the contractual date.",
            }
        ],
        "limitation_count": 1,
        "limitations": ["The carrier has not confirmed a recovery date."],
    }


def test_llm_service_fails_clearly_without_api_key(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    access = AccessService(session).resolve(
        "noah.east@controltower.demo",
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
