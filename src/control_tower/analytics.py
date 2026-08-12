from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from control_tower.access import AccessContext
from control_tower.models import (
    InventorySnapshot,
    Product,
    PurchaseOrder,
    QualityIncident,
    Shipment,
    Supplier,
    TrackingEvent,
    Warehouse,
)
from control_tower.tools import SupplierTools


class ScopeResolver:
    def __init__(self, session: Session, access: AccessContext) -> None:
        self.session = session
        self.access = access

    def warehouse_id(self, code: str) -> uuid.UUID:
        warehouse_id = self.session.scalar(
            select(Warehouse.id).where(
                Warehouse.organization_id == self.access.organization_id,
                func.lower(Warehouse.code) == code.lower(),
            )
        )
        if warehouse_id is None:
            raise ValueError(f"Unknown warehouse code: {code}")
        self.access.require_warehouse(warehouse_id)
        return warehouse_id

    def supplier_id(self, code: str) -> uuid.UUID:
        supplier_id = self.session.scalar(
            select(Supplier.id).where(
                Supplier.organization_id == self.access.organization_id,
                func.lower(Supplier.code) == code.lower(),
            )
        )
        if supplier_id is None:
            raise ValueError(f"Unknown supplier code: {code}")
        self.access.require_supplier(supplier_id)
        return supplier_id


@dataclass(frozen=True)
class InventoryPoint:
    snapshot_date: date
    warehouse_code: str
    sku: str
    available_units: int
    reorder_point: int
    daily_usage_rate: float


@dataclass(frozen=True)
class ShipmentEventRecord:
    tracking_number: str
    occurred_at: str
    location: str
    event_type: str
    details: str


@dataclass(frozen=True)
class QualityIncidentRecord:
    supplier_code: str
    sku: str
    reported_on: date
    severity: str
    status: str
    defect_quantity: int
    description: str


@dataclass(frozen=True)
class SupplierRiskRecord:
    supplier_code: str
    supplier_name: str
    shipments: int
    delayed_shipments: int
    on_time_rate: float | None
    average_delay_days: float
    open_quality_incidents: int
    high_severity_incidents: int
    risk_score: float


class InventoryAnalytics:
    def __init__(self, session: Session) -> None:
        self.session = session

    def history(
        self,
        access: AccessContext,
        *,
        warehouse_code: str,
        sku: str,
        end_date: date,
        days: int = 30,
    ) -> list[InventoryPoint]:
        if not 2 <= days <= 90:
            raise ValueError("days must be between 2 and 90")
        warehouse_id = ScopeResolver(self.session, access).warehouse_id(warehouse_code)
        rows = self.session.execute(
            select(
                InventorySnapshot.snapshot_date,
                Warehouse.code,
                Product.sku,
                InventorySnapshot.on_hand,
                InventorySnapshot.reserved,
                InventorySnapshot.reorder_point,
                InventorySnapshot.daily_usage_rate,
            )
            .join(Warehouse, Warehouse.id == InventorySnapshot.warehouse_id)
            .join(Product, Product.id == InventorySnapshot.product_id)
            .where(
                InventorySnapshot.organization_id == access.organization_id,
                InventorySnapshot.warehouse_id == warehouse_id,
                func.lower(Product.sku) == sku.lower(),
                InventorySnapshot.snapshot_date.between(
                    end_date - timedelta(days=days - 1), end_date
                ),
            )
            .order_by(InventorySnapshot.snapshot_date)
        ).all()
        return [
            InventoryPoint(
                snapshot_date=row[0],
                warehouse_code=row[1],
                sku=row[2],
                available_units=row[3] - row[4],
                reorder_point=row[5],
                daily_usage_rate=row[6],
            )
            for row in rows
        ]


class ShipmentAnalytics:
    def __init__(self, session: Session) -> None:
        self.session = session

    def tracking_history(
        self,
        access: AccessContext,
        *,
        tracking_number: str,
    ) -> list[ShipmentEventRecord]:
        warehouse_id = self.session.scalar(
            select(PurchaseOrder.destination_warehouse_id)
            .join(Shipment, Shipment.purchase_order_id == PurchaseOrder.id)
            .where(
                Shipment.organization_id == access.organization_id,
                Shipment.tracking_number == tracking_number,
            )
        )
        if warehouse_id is None:
            raise ValueError(f"Unknown tracking number: {tracking_number}")
        access.require_warehouse(warehouse_id)

        rows = self.session.execute(
            select(
                Shipment.tracking_number,
                TrackingEvent.occurred_at,
                TrackingEvent.location,
                TrackingEvent.event_type,
                TrackingEvent.details,
            )
            .join(Shipment, Shipment.id == TrackingEvent.shipment_id)
            .where(Shipment.tracking_number == tracking_number)
            .order_by(TrackingEvent.occurred_at)
        ).all()
        return [
            ShipmentEventRecord(
                tracking_number=row[0],
                occurred_at=row[1].isoformat(),
                location=row[2],
                event_type=row[3],
                details=row[4],
            )
            for row in rows
        ]


class SupplierRiskAnalytics:
    def __init__(self, session: Session) -> None:
        self.session = session

    def scorecard(self, access: AccessContext, *, supplier_code: str) -> dict:
        supplier_id = ScopeResolver(self.session, access).supplier_id(supplier_code)
        scorecard = SupplierTools(self.session).get_scorecard(access, supplier_id)
        incidents = self.incidents(access, supplier_code=supplier_code, status="open")
        scorecard["open_quality_incidents"] = len(incidents)
        scorecard["high_severity_incidents"] = sum(
            incident.severity == "high" for incident in incidents
        )
        return scorecard

    def incidents(
        self,
        access: AccessContext,
        *,
        supplier_code: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[QualityIncidentRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        supplier_ids = access.allowed_supplier_ids
        if supplier_code is not None:
            supplier_ids = (ScopeResolver(self.session, access).supplier_id(supplier_code),)
        if not supplier_ids:
            return []

        conditions = [
            QualityIncident.organization_id == access.organization_id,
            QualityIncident.supplier_id.in_(supplier_ids),
        ]
        if status is not None:
            conditions.append(func.lower(QualityIncident.status) == status.lower())

        rows = self.session.execute(
            select(
                Supplier.code,
                Product.sku,
                QualityIncident.reported_on,
                QualityIncident.severity,
                QualityIncident.status,
                QualityIncident.defect_quantity,
                QualityIncident.description,
            )
            .join(Supplier, Supplier.id == QualityIncident.supplier_id)
            .join(Product, Product.id == QualityIncident.product_id)
            .where(*conditions)
            .order_by(QualityIncident.reported_on.desc())
            .limit(limit)
        ).all()
        return [QualityIncidentRecord(*row) for row in rows]

    def rank(self, access: AccessContext, *, limit: int = 10) -> list[SupplierRiskRecord]:
        if not 1 <= limit <= 25:
            raise ValueError("limit must be between 1 and 25")
        suppliers = self.session.execute(
            select(Supplier.id, Supplier.code, Supplier.name)
            .where(
                Supplier.organization_id == access.organization_id,
                Supplier.id.in_(access.allowed_supplier_ids),
            )
            .order_by(Supplier.code)
        ).all()

        ranked = []
        for supplier_id, supplier_code, supplier_name in suppliers:
            scorecard = SupplierTools(self.session).get_scorecard(access, supplier_id)
            incidents = self.incidents(
                access,
                supplier_code=supplier_code,
                status="open",
            )
            high_severity = sum(item.severity == "high" for item in incidents)
            late_rate = 1.0 - (scorecard["on_time_rate"] or 1.0)
            risk_score = min(
                100.0,
                late_rate * 60
                + scorecard["average_delay_days"] * 3
                + len(incidents) * 4
                + high_severity * 10,
            )
            ranked.append(
                SupplierRiskRecord(
                    supplier_code=supplier_code,
                    supplier_name=supplier_name,
                    shipments=scorecard["shipments"],
                    delayed_shipments=scorecard["delayed_shipments"],
                    on_time_rate=scorecard["on_time_rate"],
                    average_delay_days=scorecard["average_delay_days"],
                    open_quality_incidents=len(incidents),
                    high_severity_incidents=high_severity,
                    risk_score=round(risk_score, 1),
                )
            )
        ranked.sort(key=lambda item: item.risk_score, reverse=True)
        return ranked[:limit]
