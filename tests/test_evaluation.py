import asyncio
import json

import pytest

from control_tower.agent_service import AgentRunResponse
from control_tower.agents.llm import OperationsAnswer
from control_tower.agents.runtime import ToolEvent
from control_tower.config import get_settings
from control_tower.evaluation import run_evaluations


@pytest.mark.parametrize(
    "reference,claimed,expected",
    [
        ("shipment:001", "shipments", True),
        ("invented:001", "shipments", False),
        ("shipment:001", "inventory", False),
    ],
)
def test_evaluation_checks_observed_citations_and_actual_tools(
    engine,
    tmp_path,
    monkeypatch,
    reference,
    claimed,
    expected,
):
    class FakeService:
        def __init__(self, _settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def ask(self, *_args, **_kwargs):
            return AgentRunResponse(
                OperationsAnswer(
                    answer="late", citations=[reference], specialists_used=["shipments"]
                ),
                [ToolEvent(claimed, "list_delayed_shipments", {}, 1, "test")],
                None,
                observed_references=["shipment:001"],
            )

    monkeypatch.setattr("control_tower.evaluation.AgentService", FakeService)
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {
                    "id": "test",
                    "user_email": "noah.east@controltower.demo",
                    "question": "late?",
                    "expected_specialists": ["shipments"],
                }
            ]
        )
    )
    result = asyncio.run(run_evaluations(get_settings(), engine, cases_path=cases))
    assert result["results"][0]["passed"] is expected
