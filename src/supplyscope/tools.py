from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from supplyscope.access import AccessContext, AccessDeniedError
from supplyscope.models import (
    Document,
    DocumentChunk,
    InventorySnapshot,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    Shipment,
    ShipmentStatus,
    Supplier,
    Warehouse,
)


@dataclass(frozen=True)
class DelayedShipment:
    tracking_number: str
    purchase_order: str
    supplier_id: uuid.UUID
    supplier_code: str
    supplier_name: str
    warehouse_code: str
    sku: str
    product_name: str
    quantity: int
    estimated_arrival: date
    delay_days: int
    delay_reason: str | None


@dataclass(frozen=True)
class InventoryRisk:
    warehouse_code: str
    sku: str
    product_name: str
    available_units: int
    reorder_point: int
    daily_usage_rate: float
    days_of_cover: float
    critical: bool


@dataclass(frozen=True)
class DocumentMatch:
    document_id: uuid.UUID
    title: str
    document_type: str
    heading: str | None
    content: str
    source_filename: str


class ShipmentTools:
    def __init__(self, session: Session):
        self.session = session

    def list_delayed_shipments(
        self,
        access: AccessContext,
        *,
        as_of: date,
        horizon_days: int = 21,
        warehouse_id: uuid.UUID | None = None,
    ) -> list[DelayedShipment]:
        if not 1 <= horizon_days <= 90:
            raise ValueError("horizon_days must be between 1 and 90")
        if warehouse_id is not None:
            access.require_warehouse(warehouse_id)
            warehouse_ids = (warehouse_id,)
        else:
            warehouse_ids = access.allowed_warehouse_ids
        if not warehouse_ids:
            return []

        rows = self.session.execute(
            select(
                Shipment.tracking_number,
                PurchaseOrder.order_number,
                Supplier.id,
                Supplier.code,
                Supplier.name,
                Warehouse.code,
                Product.sku,
                Product.name,
                PurchaseOrderLine.quantity,
                Shipment.estimated_arrival,
                Shipment.delay_days,
                Shipment.delay_reason,
            )
            .join(PurchaseOrder, PurchaseOrder.id == Shipment.purchase_order_id)
            .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
            .join(Warehouse, Warehouse.id == PurchaseOrder.destination_warehouse_id)
            .join(
                PurchaseOrderLine,
                PurchaseOrderLine.purchase_order_id == PurchaseOrder.id,
            )
            .join(Product, Product.id == PurchaseOrderLine.product_id)
            .where(
                Shipment.organization_id == access.organization_id,
                PurchaseOrder.destination_warehouse_id.in_(warehouse_ids),
                Shipment.status.in_([ShipmentStatus.DELAYED, ShipmentStatus.IN_TRANSIT]),
                Shipment.estimated_arrival <= as_of + timedelta(days=horizon_days),
                or_(Shipment.delay_days > 0, Shipment.estimated_arrival < as_of),
            )
            .order_by(Shipment.delay_days.desc(), Shipment.estimated_arrival)
        ).all()

        return [DelayedShipment(*row) for row in rows]


class InventoryTools:
    def __init__(self, session: Session):
        self.session = session

    def list_low_stock(
        self,
        access: AccessContext,
        *,
        snapshot_date: date,
        warehouse_id: uuid.UUID | None = None,
        critical_only: bool = False,
    ) -> list[InventoryRisk]:
        if warehouse_id is not None:
            access.require_warehouse(warehouse_id)
            warehouse_ids = (warehouse_id,)
        else:
            warehouse_ids = access.allowed_warehouse_ids
        if not warehouse_ids:
            return []

        conditions = [
            InventorySnapshot.organization_id == access.organization_id,
            InventorySnapshot.warehouse_id.in_(warehouse_ids),
            InventorySnapshot.snapshot_date == snapshot_date,
            InventorySnapshot.on_hand - InventorySnapshot.reserved
            < InventorySnapshot.reorder_point,
        ]
        if critical_only:
            conditions.append(Product.critical.is_(True))

        rows = self.session.execute(
            select(
                Warehouse.code,
                Product.sku,
                Product.name,
                InventorySnapshot.on_hand,
                InventorySnapshot.reserved,
                InventorySnapshot.reorder_point,
                InventorySnapshot.daily_usage_rate,
                Product.critical,
            )
            .join(Warehouse, Warehouse.id == InventorySnapshot.warehouse_id)
            .join(Product, Product.id == InventorySnapshot.product_id)
            .where(*conditions)
            .order_by(Product.critical.desc(), InventorySnapshot.on_hand)
        ).all()

        risks = []
        for row in rows:
            available = row.on_hand - row.reserved
            days_of_cover = (
                round(max(available, 0) / row.daily_usage_rate, 1)
                if row.daily_usage_rate > 0
                else float("inf")
            )
            risks.append(
                InventoryRisk(
                    warehouse_code=row.code,
                    sku=row.sku,
                    product_name=row.name,
                    available_units=available,
                    reorder_point=row.reorder_point,
                    daily_usage_rate=row.daily_usage_rate,
                    days_of_cover=days_of_cover,
                    critical=row.critical,
                )
            )
        return risks


class SupplierTools:
    def __init__(self, session: Session):
        self.session = session

    def get_scorecard(self, access: AccessContext, supplier_id: uuid.UUID) -> dict:
        access.require_supplier(supplier_id)
        supplier = self.session.scalar(
            select(Supplier).where(
                Supplier.id == supplier_id,
                Supplier.organization_id == access.organization_id,
            )
        )
        if supplier is None:
            raise AccessDeniedError("Supplier is not available in this organization.")

        shipment_counts = self.session.execute(
            select(
                func.count(Shipment.id),
                func.count(Shipment.id).filter(Shipment.delay_days > 0),
                func.avg(Shipment.delay_days),
            )
            .join(PurchaseOrder, PurchaseOrder.id == Shipment.purchase_order_id)
            .where(
                PurchaseOrder.supplier_id == supplier_id,
                PurchaseOrder.destination_warehouse_id.in_(access.allowed_warehouse_ids),
            )
        ).one()

        total = shipment_counts[0] or 0
        delayed = shipment_counts[1] or 0
        return {
            "supplier_id": str(supplier.id),
            "supplier_code": supplier.code,
            "supplier_name": supplier.name,
            "shipments": total,
            "delayed_shipments": delayed,
            "on_time_rate": round((total - delayed) / total, 3) if total else None,
            "average_delay_days": round(float(shipment_counts[2] or 0), 1),
        }


class DocumentTools:
    def __init__(self, session: Session):
        self.session = session

    def keyword_search(
        self,
        access: AccessContext,
        *,
        query: str,
        limit: int = 5,
        supplier_id: uuid.UUID | None = None,
    ) -> list[DocumentMatch]:
        terms = [term.strip(".,:;!?()[]").lower() for term in query.split()]
        terms = [term for term in terms if len(term) >= 4]
        if not terms:
            raise ValueError("Search query must contain at least one meaningful term.")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        if supplier_id is not None:
            access.require_supplier(supplier_id)

        warehouse_scope = (
            or_(
                Document.warehouse_id.is_(None),
                Document.warehouse_id.in_(access.allowed_warehouse_ids),
            )
            if access.allowed_warehouse_ids
            else Document.warehouse_id.is_(None)
        )
        supplier_scope = (
            or_(
                Document.supplier_id.is_(None),
                Document.supplier_id.in_(access.allowed_supplier_ids),
            )
            if access.allowed_supplier_ids
            else Document.supplier_id.is_(None)
        )
        term_match = or_(*[DocumentChunk.content.ilike(f"%{term}%") for term in terms])
        supplier_filter = Document.supplier_id == supplier_id if supplier_id is not None else True

        rows = self.session.execute(
            select(
                Document.id,
                Document.title,
                Document.document_type,
                DocumentChunk.heading,
                DocumentChunk.content,
                Document.source_filename,
            )
            .join(DocumentChunk, DocumentChunk.document_id == Document.id)
            .where(
                and_(
                    Document.organization_id == access.organization_id,
                    warehouse_scope,
                    supplier_scope,
                    supplier_filter,
                    term_match,
                )
            )
            .limit(limit)
        ).all()

        return [DocumentMatch(*row) for row in rows]


def serialize_records(records: list[object]) -> list[dict]:
    return [asdict(record) for record in records]
