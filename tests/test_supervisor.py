from __future__ import annotations

from sqlalchemy.orm import Session

from control_tower.access import AccessService
from control_tower.agents.specialists import (
    DocumentSpecialist,
    InventorySpecialist,
    ShipmentSpecialist,
)
from control_tower.agents.supervisor import SupplyRiskSupervisor
from control_tower.observability import ExecutionTrace
from control_tower.synthetic import DEMO_AS_OF
from control_tower.tools import DocumentTools, InventoryTools, ShipmentTools

QUESTION = (
    "Which delayed shipments could stop production, and do the responsible supplier "
    "contracts include late-delivery remedies?"
)


def build_supervisor(session: Session) -> SupplyRiskSupervisor:
    return SupplyRiskSupervisor(
        inventory=InventorySpecialist(InventoryTools(session)),
        shipments=ShipmentSpecialist(ShipmentTools(session)),
        documents=DocumentSpecialist(DocumentTools(session)),
    )


def test_supervisor_combines_structured_and_document_findings(session: Session) -> None:
    access = AccessService(session).resolve(
        "noah.east@controltower.demo",
        "meridian-assembly",
    )

    report = build_supervisor(session).analyze(QUESTION, access, as_of=DEMO_AS_OF)

    assert "SS-CRITICAL-001" in report.answer
    assert "4%" in report.answer
    assert [finding.specialist for finding in report.findings] == [
        "inventory",
        "shipments",
        "contracts_and_documents",
    ]


def test_offline_trace_exercises_bounded_evidence_review_loop(session: Session) -> None:
    access = AccessService(session).resolve(
        "noah.east@controltower.demo",
        "meridian-assembly",
    )
    trace = ExecutionTrace(run_id="offline-review-test")

    build_supervisor(session).analyze(QUESTION, access, as_of=DEMO_AS_OF, trace=trace)

    decisions = [
        event.details["decision"]
        for event in trace.events
        if event.node == "review" and event.status == "completed"
    ]
    assert decisions == ["more_evidence", "evidence_sufficient"]


def test_supervisor_result_respects_regional_scope(session: Session) -> None:
    access = AccessService(session).resolve(
        "mia.west@controltower.demo",
        "meridian-assembly",
    )

    report = build_supervisor(session).analyze(QUESTION, access, as_of=DEMO_AS_OF)

    assert "SS-CRITICAL-001" not in report.answer
    assert "No delayed inbound shipment" in report.answer
