from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from supplyscope.access import AccessContext
from supplyscope.analytics import ScopeResolver
from supplyscope.models import Warehouse
from supplyscope.retrieval import HybridDocumentRetriever


@dataclass(frozen=True)
class ToolEvent:
    specialist: str
    tool: str
    arguments: dict[str, Any]
    result_count: int
    occurred_at: str


@dataclass
class AgentRuntime:
    session: Session
    access: AccessContext
    as_of: date
    retriever: HybridDocumentRetriever
    events: list[ToolEvent] = field(default_factory=list)

    @property
    def resolver(self) -> ScopeResolver:
        return ScopeResolver(self.session, self.access)

    def warehouse_codes(self) -> list[str]:
        if not self.access.allowed_warehouse_ids:
            return []
        return list(
            self.session.scalars(
                select(Warehouse.code)
                .where(Warehouse.id.in_(self.access.allowed_warehouse_ids))
                .order_by(Warehouse.code)
            ).all()
        )

    def record(
        self,
        *,
        specialist: str,
        tool: str,
        arguments: dict[str, Any],
        result_count: int,
    ) -> None:
        self.events.append(
            ToolEvent(
                specialist=specialist,
                tool=tool,
                arguments=arguments,
                result_count=result_count,
                occurred_at=datetime.now(UTC).isoformat(),
            )
        )
