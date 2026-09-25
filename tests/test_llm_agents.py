from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from agents import Agent, MaxTurnsExceeded, Runner
from agents.tool_context import ToolContext
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
    _mcp_result_count,
    build_agent_system,
    list_delayed_shipments,
    search_contracts_and_reports,
)
from control_tower.agents.runtime import AgentRuntime
from control_tower.config import get_settings
from control_tower.observability import ExecutionTrace
from control_tower.retrieval import HybridDocumentRetriever
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
    ]
    assert trace.is_active("review")
    assert trace.events[-1].details["decision"] == "pending_validation"


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


@pytest.mark.parametrize("value", [None, "not structured JSON", {"answer": "  "}, {}])
def test_invalid_final_answer_fails_trace(session: Session, monkeypatch, value) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    service = AgentService(get_settings())
    service._started = True
    trace = ExecutionTrace()
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")

    async def run(agent, _prompt, **kwargs):
        hook = MCPTraceHooks()
        context = SimpleNamespace(context=kwargs["context"])
        await hook.on_start(context, agent)
        trace.mark_specialist_completed("shipments")
        await hook.on_llm_start(context, agent, None, [])
        await hook.on_llm_end(
            context,
            agent,
            SimpleNamespace(output=[], usage=SimpleNamespace(input_tokens=10, output_tokens=5)),
        )
        await hook.on_end(context, agent, value)
        return SimpleNamespace(final_output=value)

    monkeypatch.setattr(Runner, "run", run)
    with pytest.raises(ValueError):
        asyncio.run(
            service.ask(
                session, access, question="Which shipments are late?", as_of=DEMO_AS_OF, trace=trace
            )
        )
    assert not trace.is_active("review")
    assert not trace.is_active("supervisor")
    assert {event.node for event in trace.events if event.status == "failed"} == {
        "review",
        "supervisor",
    }
    assert not any(event.status == "completed" for event in trace.events)
    assert not trace.was_started("answer")


@pytest.mark.parametrize("review", [True, False])
def test_valid_answer_closes_trace_after_validation(session: Session, monkeypatch, review) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    service = AgentService(get_settings())
    service._started = True
    trace = ExecutionTrace()
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")

    async def run(agent, _prompt, **kwargs):
        hook = MCPTraceHooks()
        context = SimpleNamespace(context=kwargs["context"])
        await hook.on_start(context, agent)
        if review:
            trace.mark_specialist_completed("shipments")
            await hook.on_llm_start(context, agent, None, [])
            await hook.on_llm_end(
                context,
                agent,
                SimpleNamespace(output=[], usage=SimpleNamespace(input_tokens=10, output_tokens=5)),
            )
            # A nonterminal SDK turn must not leave a second open review span.
            await hook.on_llm_start(context, agent, None, [])
        output = {"answer": "Arrival is not confirmed.", "caveats": ["Tracking is missing."]}
        await hook.on_end(context, agent, output)
        assert trace.is_active("supervisor")
        assert not any(event.status == "completed" for event in trace.events)
        return SimpleNamespace(final_output=json.dumps(output))

    monkeypatch.setattr(Runner, "run", run)
    response = asyncio.run(
        service.ask(
            session, access, question="Where is my shipment?", as_of=DEMO_AS_OF, trace=trace
        )
    )
    assert response.output.caveats == ["Tracking is missing."]
    assert not trace.is_active("review")
    assert not trace.is_active("supervisor")
    completed = [event.node for event in trace.events if event.status == "completed"]
    assert completed == (["review"] if review else []) + ["supervisor", "answer"]


def test_retrieval_tool_exposes_limitations_even_without_results(session: Session) -> None:
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")
    runtime = AgentRuntime(
        session=session,
        access=access,
        as_of=DEMO_AS_OF,
        retriever=HybridDocumentRetriever(session, None),
        trace=ExecutionTrace(),
    )
    result = asyncio.run(
        search_contracts_and_reports.on_invoke_tool(
            ToolContext(
                context=runtime,
                tool_name="search_contracts_and_reports",
                tool_call_id="test-retrieval",
                tool_arguments='{"query":"nonexistentword"}',
            ),
            json.dumps({"query": "nonexistentword"}),
        )
    )
    payload = json.loads(result)
    assert payload["chunks"] == []
    assert payload["limitations"]
    assert _mcp_result_count(result) == 0
    assert runtime.trace.events[-1].label == "Keyword-only retrieval"


def test_turn_limit_fails_open_review_without_success(session: Session, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    service = AgentService(get_settings())
    service._started = True
    trace = ExecutionTrace()
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")

    async def run(_agent, _prompt, **_kwargs):
        trace.start(event_type="agent", node="supervisor", label="Supervisor")
        trace.start(event_type="review", node="review", label="Reviewing evidence")
        raise MaxTurnsExceeded("Test turn limit reached")

    monkeypatch.setattr(Runner, "run", run)
    with pytest.raises(MaxTurnsExceeded):
        asyncio.run(
            service.ask(session, access, question="Check shipments", as_of=DEMO_AS_OF, trace=trace)
        )
    assert not trace.is_active("review")
    assert not trace.is_active("supervisor")
    assert not any(event.status == "completed" for event in trace.events)
