from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from supplyscope.agent_service import AgentRunResponse
from supplyscope.agents.llm import OperationsAnswer
from supplyscope.agents.runtime import ToolEvent
from supplyscope.config import get_settings
from supplyscope.web import create_app


class FakeAgentService:
    async def ask(self, _session, _access, *, question, as_of, history):
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
        assert client.get("/").status_code == 200
        assert len(client.get("/api/personas").json()) == 6

        response = client.post(
            "/api/chat",
            json={
                "question": "Which shipments threaten production?",
                "user_email": "noah.east@supplyscope.demo",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["output"]["citations"]
        assert payload["tool_events"][0]["result_count"] == 1

        conversation_id = payload["conversation_id"]
        messages = client.get(
            f"/api/conversations/{conversation_id}/messages",
            params={"user_email": "noah.east@supplyscope.demo"},
        )
        assert [message["role"] for message in messages.json()] == ["user", "assistant"]

        unauthorized = client.get(
            f"/api/conversations/{conversation_id}/messages",
            params={"user_email": "mia.west@supplyscope.demo"},
        )
        assert unauthorized.status_code == 403
