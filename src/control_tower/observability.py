from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from time import perf_counter
from typing import Any, Literal

logger = logging.getLogger(__name__)

TraceStatus = Literal["started", "completed", "failed", "skipped", "info"]
TraceSink = Callable[["ExecutionEvent"], None]

_SENSITIVE_DETAIL_KEYS = {
    "api_key",
    "authorization",
    "conversation_id",
    "organization_id",
    "password",
    "secret",
    "supplier_id",
    "system_prompt",
    "token",
    "user_id",
    "warehouse_id",
}


@dataclass(frozen=True)
class ExecutionEvent:
    run_id: str
    sequence: int
    event_type: str
    node: str
    status: TraceStatus
    label: str
    occurred_at: str
    operation_id: str
    parent_node: str | None = None
    source: str | None = None
    duration_ms: float | None = None
    details: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _OpenOperation:
    key: str
    public_id: str
    node: str
    started_at: float
    event_type: str
    parent_node: str | None
    source: str | None


class ExecutionTrace:
    """Collect and optionally publish a safe, user-visible execution trace."""

    def __init__(self, *, sink: TraceSink | None = None, run_id: str | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.events: list[ExecutionEvent] = []
        self._sink = sink
        self._sequence = 0
        self._open: dict[str, _OpenOperation] = {}
        self._node_stack: dict[str, list[str]] = {}
        self._started_nodes: set[str] = set()
        self.completed_specialists: set[str] = set()

    def start(
        self,
        *,
        event_type: str,
        node: str,
        label: str,
        parent_node: str | None = None,
        source: str | None = None,
        details: Mapping[str, Any] | None = None,
        operation_key: str | None = None,
    ) -> str:
        key = operation_key or f"{node}:{uuid.uuid4().hex}"
        if key in self._open:
            return self._open[key].public_id

        public_id = f"op-{self._sequence + 1}"
        operation = _OpenOperation(
            key=key,
            public_id=public_id,
            node=node,
            started_at=perf_counter(),
            event_type=event_type,
            parent_node=parent_node,
            source=source,
        )
        self._open[key] = operation
        self._node_stack.setdefault(node, []).append(key)
        self._started_nodes.add(node)
        self._emit(
            event_type=event_type,
            node=node,
            status="started",
            label=label,
            operation_id=public_id,
            parent_node=parent_node,
            source=source,
            details=details,
        )
        return public_id

    def complete(
        self,
        *,
        node: str,
        label: str,
        details: Mapping[str, Any] | None = None,
        operation_key: str | None = None,
    ) -> None:
        operation = self._take_operation(node, operation_key)
        self._emit_closed(operation, status="completed", label=label, details=details)

    def fail(
        self,
        *,
        node: str,
        label: str,
        details: Mapping[str, Any] | None = None,
        operation_key: str | None = None,
    ) -> None:
        operation = self._take_operation(node, operation_key)
        self._emit_closed(operation, status="failed", label=label, details=details)

    def info(
        self,
        *,
        event_type: str,
        node: str,
        label: str,
        parent_node: str | None = None,
        source: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self._started_nodes.add(node)
        self._emit(
            event_type=event_type,
            node=node,
            status="info",
            label=label,
            operation_id=f"op-{self._sequence + 1}",
            parent_node=parent_node,
            source=source,
            details=details,
        )

    def skip(self, *, node: str, label: str) -> None:
        if node in self._started_nodes:
            return
        self._emit(
            event_type="stage",
            node=node,
            status="skipped",
            label=label,
            operation_id=f"op-{self._sequence + 1}",
        )

    def fail_open_operations(self, message: str) -> None:
        for operation in list(self._open.values()):
            self.fail(
                node=operation.node,
                label=message,
                operation_key=operation.key,
            )

    def is_active(self, node: str) -> bool:
        return bool(self._node_stack.get(node))

    def was_started(self, node: str) -> bool:
        return node in self._started_nodes

    def mark_specialist_completed(self, node: str) -> None:
        self.completed_specialists.add(node)

    def to_list(self) -> list[dict[str, object]]:
        return [event.to_dict() for event in self.events]

    def _take_operation(
        self,
        node: str,
        operation_key: str | None,
    ) -> _OpenOperation | None:
        key = operation_key
        if key is None:
            node_operations = self._node_stack.get(node, [])
            key = node_operations[-1] if node_operations else None
        if key is None:
            return None

        operation = self._open.pop(key, None)
        if operation is None:
            return None
        node_operations = self._node_stack.get(operation.node, [])
        if key in node_operations:
            node_operations.remove(key)
        return operation

    def _emit_closed(
        self,
        operation: _OpenOperation | None,
        *,
        status: Literal["completed", "failed"],
        label: str,
        details: Mapping[str, Any] | None,
    ) -> None:
        if operation is None:
            return
        self._emit(
            event_type=operation.event_type,
            node=operation.node,
            status=status,
            label=label,
            operation_id=operation.public_id,
            parent_node=operation.parent_node,
            source=operation.source,
            duration_ms=round((perf_counter() - operation.started_at) * 1000, 1),
            details=details,
        )

    def _emit(
        self,
        *,
        event_type: str,
        node: str,
        status: TraceStatus,
        label: str,
        operation_id: str,
        parent_node: str | None = None,
        source: str | None = None,
        duration_ms: float | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self._sequence += 1
        event = ExecutionEvent(
            run_id=self.run_id,
            sequence=self._sequence,
            event_type=event_type,
            node=node,
            status=status,
            label=label,
            occurred_at=datetime.now(UTC).isoformat(),
            operation_id=operation_id,
            parent_node=parent_node,
            source=source,
            duration_ms=duration_ms,
            details=_safe_details(details),
        )
        self.events.append(event)
        if self._sink is None:
            return
        try:
            self._sink(event)
        except Exception as exc:  # pragma: no cover - observability must not break a run
            logger.warning("Execution trace sink failed (%s)", type(exc).__name__)


def _safe_details(details: Mapping[str, Any] | None) -> dict[str, object] | None:
    if not details:
        return None
    return {str(key): _safe_value(str(key), value, depth=0) for key, value in details.items()}


def _safe_value(key: str, value: Any, *, depth: int) -> object:
    if key.casefold() in _SENSITIVE_DETAIL_KEYS:
        return "[redacted]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, uuid.UUID):
        return "[redacted-id]"
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str):
        return value if len(value) <= 500 else f"{value[:497]}..."
    if is_dataclass(value) and not isinstance(value, type):
        return _safe_value(key, asdict(value), depth=depth)
    if depth >= 3:
        return "[nested value]"
    if isinstance(value, Mapping):
        return {
            str(child_key): _safe_value(str(child_key), child_value, depth=depth + 1)
            for child_key, child_value in list(value.items())[:20]
        }
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_safe_value(key, item, depth=depth + 1) for item in list(value)[:20]]
    text = str(value)
    return text if len(text) <= 500 else f"{text[:497]}..."
