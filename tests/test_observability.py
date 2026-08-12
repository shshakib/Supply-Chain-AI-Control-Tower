from __future__ import annotations

import uuid

from control_tower.observability import ExecutionEvent, ExecutionTrace


def test_execution_trace_publishes_ordered_redacted_events() -> None:
    published: list[ExecutionEvent] = []
    trace = ExecutionTrace(sink=published.append, run_id="run-test")

    trace.start(
        event_type="access",
        node="access",
        label="Resolving access",
        details={
            "role": "viewer",
            "user_id": uuid.uuid4(),
            "token": "secret-token",
        },
        operation_key="access",
    )
    trace.complete(
        node="access",
        label="Access resolved",
        details={"warehouse_count": 1},
        operation_key="access",
    )

    assert [event.sequence for event in published] == [1, 2]
    assert published[0].operation_id == published[1].operation_id
    assert published[1].duration_ms is not None
    assert published[0].details == {
        "role": "viewer",
        "user_id": "[redacted]",
        "token": "[redacted]",
    }


def test_execution_trace_closes_active_operations_on_failure() -> None:
    trace = ExecutionTrace(run_id="run-failure")
    trace.start(event_type="request", node="request", label="Started")
    trace.start(event_type="agent", node="supervisor", label="Started")

    trace.fail_open_operations("Run failed")

    assert trace.is_active("request") is False
    assert trace.is_active("supervisor") is False
    assert [event.status for event in trace.events[-2:]] == ["failed", "failed"]


def test_execution_trace_redacts_key_variants_and_secrets_inside_text() -> None:
    trace = ExecutionTrace(run_id="run-redaction")
    api_key = "sk-" + ("x" * 24)

    trace.info(
        event_type="diagnostic",
        node="request",
        label="Sanitized diagnostic",
        details={
            "clientSecret": "test-value",
            "nested": {"refresh-token": "test-value"},
            "message": f"Upstream returned Bearer {'a' * 20} and {api_key}",
            "connection": "postgresql://demo:local-password@localhost:5432/demo",
        },
    )

    assert trace.events[0].details == {
        "clientSecret": "[redacted]",
        "nested": {"refresh-token": "[redacted]"},
        "message": "Upstream returned Bearer [redacted] and [redacted-api-key]",
        "connection": "postgresql://demo:[redacted]@localhost:5432/demo",
    }
