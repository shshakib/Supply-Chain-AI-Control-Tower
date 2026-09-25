from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from agents import Runner
from agents import trace as sdk_trace
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.models.openai_provider import OpenAIProvider
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from control_tower.access import AccessService
from control_tower.agent_service import AgentService
from control_tower.agents.llm import MCPTraceHooks, _tool_references, build_agent_system
from control_tower.agents.runtime import AgentRuntime
from control_tower.config import get_settings
from control_tower.limits import RunBudget, RunLimitExceeded, RunLimits
from control_tower.observability import ExecutionTrace
from control_tower.synthetic import DEMO_AS_OF


def test_call_limit_is_shared_and_sticky() -> None:
    budget = RunBudget(RunLimits(max_model_calls=2))
    budget.before_model()
    budget.before_model()
    with pytest.raises(RunLimitExceeded, match="model-call"):
        budget.before_model()
    assert budget.model_calls == 2
    with pytest.raises(RunLimitExceeded):
        budget.check()


def test_token_limit_and_usage_count_each_response_once() -> None:
    budget = RunBudget(RunLimits(max_total_tokens=100))
    budget.after_model(SimpleNamespace(input_tokens=30, output_tokens=10))
    with pytest.raises(RunLimitExceeded, match="token"):
        budget.after_model(SimpleNamespace(input_tokens=50, output_tokens=10))
    assert budget.input_tokens == 80 and budget.output_tokens == 20


def test_dollar_budget_uses_configured_rates_and_rejects_missing_rates() -> None:
    with pytest.raises(ValueError, match="rates"):
        RunLimits(max_cost_usd=1)
    budget = RunBudget(
        RunLimits(max_cost_usd=0.01, input_usd_per_million=10, output_usd_per_million=20)
    )
    with pytest.raises(RunLimitExceeded, match="cost"):
        budget.after_model(SimpleNamespace(input_tokens=500, output_tokens=250))
    assert budget.estimated_cost_usd == 0.01
    assert RunBudget(RunLimits()).estimated_cost_usd is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("timeout_seconds", 0),
        ("max_model_calls", -1),
        ("max_cost_usd", float("nan")),
        ("request_timeout_seconds", float("inf")),
    ],
)
def test_invalid_limits_fail_configuration(field, value) -> None:
    with pytest.raises(ValueError):
        RunLimits(**{field: value})


def test_missing_usage_fails_closed() -> None:
    with pytest.raises(RunLimitExceeded, match="usage"):
        RunBudget(RunLimits()).after_model(None)


def test_limits_apply_without_trace(session) -> None:
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")
    runtime = AgentRuntime(
        session,
        access,
        DEMO_AS_OF,
        SimpleNamespace(),
        budget=RunBudget(RunLimits(max_model_calls=1)),
    )
    context = SimpleNamespace(context=runtime)
    hook = MCPTraceHooks()
    asyncio.run(hook.on_llm_start(context, SimpleNamespace(name="Shipment specialist"), None, []))
    with pytest.raises(RunLimitExceeded):
        asyncio.run(
            hook.on_llm_start(context, SimpleNamespace(name="Inventory specialist"), None, [])
        )


def test_timeout_cancels_work_and_fails_trace(session, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    settings = replace(get_settings(), run_limits=RunLimits(timeout_seconds=0.2))
    service = AgentService(settings)
    service._started = True
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")
    trace = ExecutionTrace()
    cancelled = []

    async def run(*_args, **_kwargs):
        trace.start(event_type="agent", node="supervisor", label="Supervisor")
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(Runner, "run", run)
    with pytest.raises(RunLimitExceeded, match="time"):
        asyncio.run(
            service.ask(session, access, question="Check shipments", as_of=DEMO_AS_OF, trace=trace)
        )
    assert cancelled
    assert not trace.is_active("supervisor")
    assert not trace.was_started("answer")
    assert trace.events[-1].details["stopped_reason"] == "Run time limit reached."


def test_unknown_citation_never_becomes_success(session, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    service = AgentService(get_settings())
    service._started = True
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")

    async def run(*_args, **_kwargs):
        return SimpleNamespace(final_output={"answer": "Invented fact", "citations": ["fake:001"]})

    monkeypatch.setattr(Runner, "run", run)
    trace = ExecutionTrace()
    with pytest.raises(ValueError, match="cites evidence"):
        asyncio.run(
            service.ask(session, access, question="Check shipments", as_of=DEMO_AS_OF, trace=trace)
        )
    assert not trace.was_started("answer")


def test_tool_reference_extraction_does_not_treat_prose_as_evidence() -> None:
    payload = '{"chunks":[{"citation":"doc.md#chunk-0"}],"text":"A claim: fake:001"}'
    assert _tool_references(payload) == {"doc.md#chunk-0"}
    assert _tool_references({"events": [{"reference": "external-risk:EXT-1"}]}) == {
        "external-risk:EXT-1"
    }


def test_model_output_cap_and_environment_config(monkeypatch) -> None:
    monkeypatch.setenv("CONTROL_TOWER_MAX_OUTPUT_TOKENS", "1234")
    monkeypatch.setenv("CONTROL_TOWER_MAX_MODEL_CALLS", "5")
    settings = get_settings()
    assert settings.run_limits.max_model_calls == 5
    assert build_agent_system(settings).model_settings.max_tokens == 1234


@pytest.mark.parametrize("call_limit", [2, 24])
def test_real_sdk_nested_specialist_shares_budget_without_network(session, monkeypatch, call_limit):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    calls = []

    class ScriptedModel(Model):
        def __init__(self, supervisor):
            self.supervisor = supervisor
            self.turns = 0

        async def get_response(self, *args, **kwargs):
            calls.append(self.supervisor)
            self.turns += 1
            assert kwargs["model_settings"].max_tokens == 3000
            if self.supervisor and self.turns == 1:
                output = [
                    ResponseFunctionToolCall(
                        type="function_call",
                        call_id="call-1",
                        name="ask_shipment_specialist",
                        arguments='{"input":"Check shipments"}',
                    )
                ]
            else:
                payload = (
                    {
                        "answer": "No evidence available.",
                        "citations": [],
                        "key_findings": [],
                        "specialists_used": ["shipments"],
                        "caveats": ["No evidence available."],
                    }
                    if self.supervisor
                    else {
                        "domain": "shipments",
                        "summary": "No evidence available.",
                        "evidence": [],
                        "limitations": ["No evidence available."],
                    }
                )
                output = [
                    ResponseOutputMessage(
                        id="msg-1",
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                type="output_text", text=json.dumps(payload), annotations=[]
                            )
                        ],
                    )
                ]
            return ModelResponse(
                output=output,
                usage=Usage(requests=1, input_tokens=10, output_tokens=5, total_tokens=15),
                response_id=None,
            )

        async def stream_response(self, *args, **kwargs):
            raise NotImplementedError
            yield  # pragma: no cover

    models = {"supervisor-test": ScriptedModel(True), "shipment-test": ScriptedModel(False)}
    monkeypatch.setattr(OpenAIProvider, "get_model", lambda _self, name: models[name])
    settings = replace(
        get_settings(),
        supervisor_model="supervisor-test",
        shipment_model="shipment-test",
        run_limits=RunLimits(max_model_calls=call_limit),
    )
    service = AgentService(settings)
    service._started = True
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")
    with sdk_trace("local-test", disabled=True):
        if call_limit == 2:
            with pytest.raises(RunLimitExceeded):
                asyncio.run(
                    service.ask(session, access, question="Check shipments", as_of=DEMO_AS_OF)
                )
            assert len(calls) == 2
        else:
            result = asyncio.run(
                service.ask(session, access, question="Check shipments", as_of=DEMO_AS_OF)
            )
            assert result.usage["model_calls"] == 3
            assert result.usage["input_tokens"] == 30
