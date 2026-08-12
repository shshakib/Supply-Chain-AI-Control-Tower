from __future__ import annotations

from datetime import date

from control_tower.access import AccessContext
from control_tower.agents.specialists import (
    DocumentSpecialist,
    InventorySpecialist,
    ShipmentSpecialist,
)
from control_tower.agents.types import SpecialistFinding, SupplyRiskReport
from control_tower.observability import ExecutionTrace


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

    def analyze(
        self,
        question: str,
        access: AccessContext,
        *,
        as_of: date,
        trace: ExecutionTrace | None = None,
    ) -> SupplyRiskReport:
        if trace is not None:
            trace.start(
                event_type="agent",
                node="supervisor",
                label="Deterministic supervisor started",
                source="application",
            )
            trace.info(
                event_type="routing",
                node="supervisor",
                label="Routed work to inventory specialist",
                details={
                    "specialist": "inventory",
                    "delegated_task": (
                        f"Find critical stock below reorder point on {as_of.isoformat()} "
                        "within the authorized warehouse scope."
                    ),
                },
            )
            trace.info(
                event_type="routing",
                node="supervisor",
                label="Routed work to shipment specialist",
                details={
                    "specialist": "shipments",
                    "delegated_task": (
                        "Find delayed inbound shipments in the next 21 days within the "
                        "authorized warehouse scope."
                    ),
                },
            )
            trace.start(
                event_type="agent",
                node="inventory",
                label="Inventory specialist started",
                parent_node="supervisor",
                source="application",
            )
            trace.start(
                event_type="tool",
                node="postgresql",
                label="Query low-stock inventory",
                parent_node="inventory",
                source="postgresql",
                details={"tool": "list_low_stock", "critical_only": True},
                operation_key="offline:inventory-query",
            )
        inventory_finding = self.inventory.assess_critical_stock(access, as_of=as_of)
        if trace is not None:
            trace.complete(
                node="postgresql",
                label="Low-stock inventory returned",
                details={"result_count": len(inventory_finding.evidence)},
                operation_key="offline:inventory-query",
            )
            trace.complete(
                node="inventory",
                label="Inventory specialist completed",
                details=_finding_trace_details(inventory_finding, as_of=as_of),
            )
            trace.mark_specialist_completed("inventory")
            trace.start(
                event_type="agent",
                node="shipments",
                label="Shipment specialist started",
                parent_node="supervisor",
                source="application",
            )
            trace.start(
                event_type="tool",
                node="postgresql",
                label="Query delayed shipments",
                parent_node="shipments",
                source="postgresql",
                details={"tool": "list_delayed_shipments", "horizon_days": 21},
                operation_key="offline:shipment-query",
            )
        shipment_finding = self.shipments.assess_inbound_delays(
            access,
            as_of=as_of,
            horizon_days=21,
        )
        if trace is not None:
            trace.complete(
                node="postgresql",
                label="Delayed shipments returned",
                details={"result_count": len(shipment_finding.evidence)},
                operation_key="offline:shipment-query",
            )
            trace.complete(
                node="shipments",
                label="Shipment specialist completed",
                details=_finding_trace_details(shipment_finding, as_of=as_of),
            )
            trace.mark_specialist_completed("shipments")

        risks_by_sku = {item["sku"]: item for item in inventory_finding.evidence}
        affected_shipments = [
            item for item in shipment_finding.evidence if item["sku"] in risks_by_sku
        ]

        findings: list[SpecialistFinding] = [inventory_finding, shipment_finding]
        if not affected_shipments:
            answer = (
                "No delayed inbound shipment currently overlaps with a critical item "
                "below its reorder point in the accessible warehouses."
            )
            report = SupplyRiskReport(
                question=question,
                answer=answer,
                findings=findings,
            )
            self._finish_trace(trace, answer=answer, used_contracts=False)
            return report

        affected_shipments.sort(key=lambda item: item["delay_days"], reverse=True)
        top_shipment = affected_shipments[0]
        risk = risks_by_sku[top_shipment["sku"]]
        if trace is not None:
            trace.info(
                event_type="routing",
                node="supervisor",
                label="Routed supplier evidence to contracts specialist",
                details={
                    "specialist": "contracts_compliance",
                    "delegated_task": (
                        "Retrieve late-delivery remedies and force-majeure terms for the "
                        "supplier on the highest-risk delayed shipment."
                    ),
                },
            )
            trace.start(
                event_type="agent",
                node="contracts_compliance",
                label="Contracts and compliance specialist started",
                parent_node="supervisor",
                source="application",
            )
            trace.start(
                event_type="tool",
                node="pgvector",
                label="Retrieve contract evidence",
                parent_node="contracts_compliance",
                source="pgvector",
                details={
                    "tool": "keyword_search",
                    "query": "late delivery remedies and force majeure",
                },
                operation_key="offline:contract-retrieval",
            )
        document_finding = self.documents.find_delivery_remedies(
            access,
            supplier_id=top_shipment["supplier_id"],
        )
        if trace is not None:
            trace.complete(
                node="pgvector",
                label="Contract evidence retrieved",
                details={
                    "result_count": len(document_finding.evidence),
                    "retrieval_mode": "keyword fallback",
                },
                operation_key="offline:contract-retrieval",
            )
            trace.complete(
                node="contracts_compliance",
                label="Contracts and compliance specialist completed",
                details=_finding_trace_details(document_finding, as_of=as_of),
            )
            trace.mark_specialist_completed("contracts_compliance")
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

        if trace is not None:
            trace.start(
                event_type="synthesis",
                node="synthesis",
                label="Combining operational and contract findings",
                parent_node="supervisor",
                source="application",
                details={"specialists": sorted(trace.completed_specialists)},
                operation_key="offline:synthesis",
            )
        answer = (
            f"{top_shipment['warehouse_code']} is at immediate risk for "
            f"{top_shipment['sku']} ({top_shipment['product_name']}). It has "
            f"{risk['available_units']} available units, about {risk['days_of_cover']} days "
            f"of cover, while shipment {top_shipment['tracking_number']} from "
            f"{top_shipment['supplier_name']} is delayed by {top_shipment['delay_days']} days "
            f"and is now expected on {top_shipment['estimated_arrival']}. "
            f"Contract evidence: {contract_note}"
        )
        if trace is not None:
            trace.complete(
                node="synthesis",
                label="Operational answer composed",
                operation_key="offline:synthesis",
            )
        self._finish_trace(trace, answer=answer, used_contracts=True)
        return SupplyRiskReport(question=question, answer=answer, findings=findings)

    @staticmethod
    def _finish_trace(
        trace: ExecutionTrace | None,
        *,
        answer: str,
        used_contracts: bool,
    ) -> None:
        if trace is None:
            return
        if not trace.was_started("synthesis"):
            trace.start(
                event_type="synthesis",
                node="synthesis",
                label="Combining specialist findings",
                parent_node="supervisor",
                source="application",
                operation_key="offline:synthesis",
            )
            trace.complete(
                node="synthesis",
                label="Operational answer composed",
                operation_key="offline:synthesis",
            )
        trace.complete(
            node="supervisor",
            label="Deterministic supervisor completed",
            details={"specialists": sorted(trace.completed_specialists)},
        )
        trace.start(
            event_type="answer",
            node="answer",
            label="Preparing answer",
            parent_node="synthesis",
            source="application",
        )
        trace.complete(
            node="answer",
            label="Answer ready",
            details={"answer_length": len(answer)},
        )
        trace.skip(node="supplier_risk", label="Supplier-risk specialist not needed")
        trace.skip(node="mcp", label="External-risk MCP not used in offline workflow")
        if not used_contracts:
            trace.skip(
                node="contracts_compliance",
                label="Contracts specialist not needed",
            )
            trace.skip(node="pgvector", label="Contract retrieval not needed")


def _finding_trace_details(
    finding: SpecialistFinding,
    *,
    as_of: date,
) -> dict[str, object]:
    domain = (
        "contracts_compliance"
        if finding.specialist == "contracts_and_documents"
        else finding.specialist
    )
    evidence = [_public_evidence(item, as_of=as_of) for item in finding.evidence]
    return {
        "domain": domain,
        "summary": finding.summary,
        "evidence_count": len(evidence),
        "evidence": evidence,
        "limitation_count": 0,
        "limitations": [],
    }


def _public_evidence(item: dict[str, object], *, as_of: date) -> dict[str, str]:
    tracking_number = item.get("tracking_number")
    if tracking_number:
        return {
            "reference": f"shipment:{tracking_number}",
            "claim": (
                f"{tracking_number} carries {item.get('sku', 'an item')} to "
                f"{item.get('warehouse_code', 'the destination')} and is delayed by "
                f"{item.get('delay_days', 'an unknown number of')} days."
            ),
        }

    source_filename = item.get("source_filename")
    if source_filename:
        heading = item.get("heading") or item.get("title") or "Retrieved document passage"
        return {
            "reference": str(source_filename),
            "claim": f"{heading}: {item.get('content', '')}",
        }

    warehouse_code = item.get("warehouse_code")
    sku = item.get("sku")
    if warehouse_code and sku:
        return {
            "reference": f"inventory:{warehouse_code}/{sku}/{as_of.isoformat()}",
            "claim": (
                f"{warehouse_code} has {item.get('available_units', 'unknown')} available "
                f"{sku} units, representing {item.get('days_of_cover', 'unknown')} days "
                "of cover."
            ),
        }

    return {
        "reference": "local-analysis:unclassified",
        "claim": "The deterministic specialist returned an unclassified evidence record.",
    }
