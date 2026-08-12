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
