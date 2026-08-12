from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from control_tower.agent_service import AgentRunResponse
from control_tower.agents.llm import OperationsAnswer
from control_tower.agents.runtime import ToolEvent
from control_tower.config import get_settings
from control_tower.web import create_app


class FakeAgentService:
    mcp_status = {
        "enabled": True,
        "state": "connected",
        "url": "http://risk.test/mcp",
        "tools": ["search_disruption_events"],
        "detail": "Synthetic external disruption feed is available.",
    }

    async def ask(self, _session, _access, *, question, as_of, history, trace=None):
        if trace is not None:
            trace.start(
                event_type="agent",
                node="supervisor",
                label="Supervisor started",
            )
            trace.complete(node="supervisor", label="Supervisor completed")
            trace.start(event_type="answer", node="answer", label="Preparing answer")
            trace.complete(node="answer", label="Answer ready")
        return AgentRunResponse(
            output=OperationsAnswer(
                answer=f"Evidence-backed answer for: {question}",
                key_findings=["One delayed shipment overlaps with low stock."],
                citations=["sup-001-master-supply-agreement.md#chunk-1"],
                specialists_used=["shipments", "inventory", "contracts_compliance"],
            ),
            tool_events=[
                ToolEvent(
                    specialist="shipments",
                    tool="list_delayed_shipments",
                    arguments={"horizon_days": 21},
                    result_count=1,
                    occurred_at=datetime.now(UTC).isoformat(),
                )
            ],
            response_id="resp_test",
        )


def test_web_chat_persists_scoped_conversation(engine: Engine) -> None:
    app = create_app(
        settings=get_settings(),
        engine=engine,
        agent_service=FakeAgentService(),
    )

    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "Live execution trace" in index.text
        assert "Suggested inquiries" in index.text
        assert 'id="theme-toggle"' in index.text
        assert 'data-flow="specialists"' in index.text
        assert len(client.get("/api/personas").json()) == 6
        assert client.get("/api/health").json()["external_risk_mcp"]["state"] == "connected"

        response = client.post(
            "/api/chat",
            json={
                "question": "Which shipments threaten production?",
                "user_email": "noah.east@controltower.demo",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["output"]["citations"]
        assert payload["tool_events"][0]["result_count"] == 1
        assert payload["execution_trace"][0]["node"] == "request"

        conversation_id = payload["conversation_id"]
        messages = client.get(
            f"/api/conversations/{conversation_id}/messages",
            params={"user_email": "noah.east@controltower.demo"},
        )
        assert [message["role"] for message in messages.json()] == ["user", "assistant"]

        unauthorized = client.get(
            f"/api/conversations/{conversation_id}/messages",
            params={"user_email": "mia.west@controltower.demo"},
        )
        assert unauthorized.status_code == 403


def test_demo_stream_emits_live_trace_and_final_result(engine: Engine) -> None:
    app = create_app(
        settings=get_settings(),
        engine=engine,
        agent_service=FakeAgentService(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/demo/stream",
            json={"user_email": "noah.east@controltower.demo"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    trace_events = [payload for name, payload in events if name == "trace"]
    result = next(payload for name, payload in events if name == "result")

    nodes = {event["node"] for event in trace_events}
    assert {
        "request",
        "access",
        "supervisor",
        "inventory",
        "shipments",
        "contracts_compliance",
        "postgresql",
        "pgvector",
        "synthesis",
        "answer",
    }.issubset(nodes)
    assert any(event["node"] == "mcp" and event["status"] == "skipped" for event in trace_events)
    inventory_route = next(
        event
        for event in trace_events
        if event["event_type"] == "routing"
        and event.get("details", {}).get("specialist") == "inventory"
    )
    inventory_result = next(
        event
        for event in trace_events
        if event["node"] == "inventory" and event["status"] == "completed"
    )
    assert inventory_route["details"]["delegated_task"]
    assert inventory_result["details"]["summary"]
    assert inventory_result["details"]["evidence"][0]["reference"].startswith("inventory:")
    assert result["output"]["answer"]
    assert result["output"]["citations"]
    assert len(result["execution_trace"]) == len(trace_events)


def _parse_sse(content: str) -> list[tuple[str, dict]]:
    import json

    parsed = []
    for frame in content.strip().split("\n\n"):
        lines = frame.splitlines()
        event_line = next((line for line in lines if line.startswith("event: ")), None)
        data_line = next((line for line in lines if line.startswith("data: ")), None)
        if event_line and data_line:
            parsed.append((event_line[7:], json.loads(data_line[6:])))
    return parsed
