from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SpecialistFinding:
    specialist: str
    summary: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SupplyRiskReport:
    question: str
    answer: str
    findings: list[SpecialistFinding]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
