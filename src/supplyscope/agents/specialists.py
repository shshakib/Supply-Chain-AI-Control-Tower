from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date

from supplyscope.access import AccessContext
from supplyscope.agents.types import SpecialistFinding
from supplyscope.tools import DocumentTools, InventoryTools, ShipmentTools


class InventorySpecialist:
    name = "inventory"

    def __init__(self, tools: InventoryTools):
        self.tools = tools

    def assess_critical_stock(
        self,
        access: AccessContext,
        *,
        as_of: date,
    ) -> SpecialistFinding:
        risks = self.tools.list_low_stock(
            access,
            snapshot_date=as_of,
            critical_only=True,
        )
        summary = (
            f"Found {len(risks)} critical products below their reorder point."
            if risks
            else "No critical products are below their reorder point."
        )
        return SpecialistFinding(
            specialist=self.name,
            summary=summary,
            evidence=[asdict(risk) for risk in risks],
        )


class ShipmentSpecialist:
    name = "shipments"

    def __init__(self, tools: ShipmentTools):
        self.tools = tools

    def assess_inbound_delays(
        self,
        access: AccessContext,
        *,
        as_of: date,
        horizon_days: int,
    ) -> SpecialistFinding:
        delays = self.tools.list_delayed_shipments(
            access,
            as_of=as_of,
            horizon_days=horizon_days,
        )
        summary = (
            f"Found {len(delays)} delayed inbound shipment lines in the planning horizon."
            if delays
            else "No delayed inbound shipments were found in the planning horizon."
        )
        return SpecialistFinding(
            specialist=self.name,
            summary=summary,
            evidence=[asdict(delay) for delay in delays],
        )


class DocumentSpecialist:
    name = "contracts_and_documents"

    def __init__(self, tools: DocumentTools):
        self.tools = tools

    def find_delivery_remedies(
        self,
        access: AccessContext,
        *,
        supplier_id: uuid.UUID,
    ) -> SpecialistFinding:
        matches = self.tools.keyword_search(
            access,
            query="late delivery penalty credit grace period force majeure",
            supplier_id=supplier_id,
            limit=5,
        )
        summary = (
            f"Retrieved {len(matches)} relevant contract sections."
            if matches
            else "No relevant contract sections were found."
        )
        return SpecialistFinding(
            specialist=self.name,
            summary=summary,
            evidence=[asdict(match) for match in matches],
        )
