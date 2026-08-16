from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from agents import Agent
from sqlalchemy.orm import Session

from control_tower.access import AccessService
from control_tower.agent_service import AgentService, MissingOpenAIConfiguration
from control_tower.agents.llm import (
    SPECIALIST_MAX_TURNS,
    SUPERVISOR_MAX_TURNS,
    EvidenceItem,
    MCPTraceHooks,
    SpecialistReport,
    _agent_output_details,
    _delegated_task,
    build_agent_system,
    list_delayed_shipments,
)
from control_tower.agents.runtime import AgentRuntime
from control_tower.config import get_settings
from control_tower.observability import ExecutionTrace
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


def test_agent_turn_budgets_are_hard_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}
    original_as_tool = Agent.as_tool

    def capture_turn_budget(agent, *args, **kwargs):
        captured[args[0]] = kwargs["max_turns"]
        return original_as_tool(agent, *args, **kwargs)

    monkeypatch.setattr(Agent, "as_tool", capture_turn_budget)
    build_agent_system(get_settings())

    assert SUPERVISOR_MAX_TURNS == 14
    assert set(captured.values()) == {SPECIALIST_MAX_TURNS}
    assert SPECIALIST_MAX_TURNS == 6


def test_supervisor_review_trace_distinguishes_follow_up_from_final(
    session: Session,
) -> None:
    access = AccessService(session).resolve(
        "noah.east@controltower.demo",
        "meridian-assembly",
    )
    trace = ExecutionTrace(run_id="review-hook-test")
    runtime = AgentRuntime(
        session=session,
        access=access,
        as_of=DEMO_AS_OF,
        retriever=SimpleNamespace(),
        trace=trace,
    )
    context = SimpleNamespace(context=runtime)
    supervisor = SimpleNamespace(name="Supply Chain AI Control Tower supervisor")
    hook = MCPTraceHooks()

    asyncio.run(hook.on_llm_start(context, supervisor, None, []))
    assert not trace.was_started("review")

    trace.mark_specialist_completed("shipments")
    asyncio.run(hook.on_llm_start(context, supervisor, None, []))
    asyncio.run(
        hook.on_llm_end(
            context,
            supervisor,
            SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="ask_inventory_specialist",
                    )
                ]
            ),
        )
    )

    trace.mark_specialist_completed("inventory")
    asyncio.run(hook.on_llm_start(context, supervisor, None, []))
    asyncio.run(
        hook.on_llm_end(
            context,
            supervisor,
            SimpleNamespace(output=[SimpleNamespace(type="message")]),
        )
    )

    review_starts = [
        event for event in trace.events if event.node == "review" and event.status == "started"
    ]
    review_decisions = [
        event.details
        for event in trace.events
        if event.node == "review" and event.status == "completed"
    ]
    assert [event.details["review_round"] for event in review_starts] == [1, 2]
    assert review_decisions == [
        {
            "decision": "more_evidence",
            "requested_specialists": ["inventory"],
        },
        {
            "decision": "evidence_sufficient",
            "specialists": ["inventory", "shipments"],
        },
    ]


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
