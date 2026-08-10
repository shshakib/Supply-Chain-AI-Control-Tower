from __future__ import annotations

from datetime import date

from supplyscope.access import AccessContext
from supplyscope.agents.specialists import (
    DocumentSpecialist,
    InventorySpecialist,
    ShipmentSpecialist,
)
from supplyscope.agents.types import SpecialistFinding, SupplyRiskReport


class SupplyRiskSupervisor:
    """A deterministic first workflow behind a future natural-language router."""

    def __init__(
        self,
        inventory: InventorySpecialist,
        shipments: ShipmentSpecialist,
        documents: DocumentSpecialist,
    ) -> None:
        self.inventory = inventory
        self.shipments = shipments
        self.documents = documents

    def analyze(self, question: str, access: AccessContext, *, as_of: date) -> SupplyRiskReport:
        inventory_finding = self.inventory.assess_critical_stock(access, as_of=as_of)
        shipment_finding = self.shipments.assess_inbound_delays(
            access,
            as_of=as_of,
            horizon_days=21,
        )

        risks_by_sku = {item["sku"]: item for item in inventory_finding.evidence}
        affected_shipments = [
            item for item in shipment_finding.evidence if item["sku"] in risks_by_sku
        ]

        findings: list[SpecialistFinding] = [inventory_finding, shipment_finding]
        if not affected_shipments:
            return SupplyRiskReport(
                question=question,
                answer=(
                    "No delayed inbound shipment currently overlaps with a critical item "
                    "below its reorder point in the accessible warehouses."
                ),
                findings=findings,
            )

        affected_shipments.sort(key=lambda item: item["delay_days"], reverse=True)
        top_shipment = affected_shipments[0]
        risk = risks_by_sku[top_shipment["sku"]]
        document_finding = self.documents.find_delivery_remedies(
            access,
            supplier_id=top_shipment["supplier_id"],
        )
        findings.append(document_finding)

        contract_note = "No matching contractual remedy was retrieved."
        if document_finding.evidence:
            remedy = next(
                (
                    item
                    for item in document_finding.evidence
                    if "credit" in item["content"].lower() or "penalty" in item["content"].lower()
                ),
                document_finding.evidence[0],
            )
            contract_note = remedy["content"]

        answer = (
            f"{top_shipment['warehouse_code']} is at immediate risk for "
            f"{top_shipment['sku']} ({top_shipment['product_name']}). It has "
            f"{risk['available_units']} available units, about {risk['days_of_cover']} days "
            f"of cover, while shipment {top_shipment['tracking_number']} from "
            f"{top_shipment['supplier_name']} is delayed by {top_shipment['delay_days']} days "
            f"and is now expected on {top_shipment['estimated_arrival']}. "
            f"Contract evidence: {contract_note}"
        )
        return SupplyRiskReport(question=question, answer=answer, findings=findings)
