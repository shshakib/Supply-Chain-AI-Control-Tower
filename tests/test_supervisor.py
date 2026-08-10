from __future__ import annotations

from sqlalchemy.orm import Session

from supplyscope.access import AccessService
from supplyscope.agents.specialists import (
    DocumentSpecialist,
    InventorySpecialist,
    ShipmentSpecialist,
)
from supplyscope.agents.supervisor import SupplyRiskSupervisor
from supplyscope.synthetic import DEMO_AS_OF
from supplyscope.tools import DocumentTools, InventoryTools, ShipmentTools

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
        "noah.east@supplyscope.demo",
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


def test_supervisor_result_respects_regional_scope(session: Session) -> None:
    access = AccessService(session).resolve(
        "mia.west@supplyscope.demo",
        "meridian-assembly",
    )

    report = build_supervisor(session).analyze(QUESTION, access, as_of=DEMO_AS_OF)

    assert "SS-CRITICAL-001" not in report.answer
    assert "No delayed inbound shipment" in report.answer
