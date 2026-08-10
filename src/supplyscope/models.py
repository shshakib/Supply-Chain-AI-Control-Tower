from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Role(StrEnum):
    GLOBAL_ADMIN = "global_admin"
    REGIONAL_OPERATIONS = "regional_operations"
    PROCUREMENT_ANALYST = "procurement_analyst"
    QUALITY_ANALYST = "quality_analyst"
    VIEWER = "viewer"


class ShipmentStatus(StrEnum):
    PLANNED = "planned"
    IN_TRANSIT = "in_transit"
    DELAYED = "delayed"
    DELIVERED = "delivered"


class PurchaseOrderStatus(StrEnum):
    OPEN = "open"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)


class Membership(IdMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Role] = mapped_column(String(40), nullable=False)
    all_warehouses: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Warehouse(IdMixin, TimestampMixin, Base):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str] = mapped_column(String(80), nullable=False)


class MembershipWarehouse(IdMixin, Base):
    __tablename__ = "membership_warehouses"
    __table_args__ = (UniqueConstraint("membership_id", "warehouse_id"),)

    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )


class Supplier(IdMixin, TimestampMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    country: Mapped[str] = mapped_column(String(80), nullable=False)
    typical_lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    late_delivery_probability: Mapped[float] = mapped_column(Float, nullable=False)
    defect_probability: Mapped[float] = mapped_column(Float, nullable=False)


class Product(IdMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("organization_id", "sku"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), default="unit", nullable=False)
    critical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SupplierProduct(IdMixin, Base):
    __tablename__ = "supplier_products"
    __table_args__ = (UniqueConstraint("supplier_id", "product_id"),)

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class PurchaseOrder(IdMixin, TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("organization_id", "order_number"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    destination_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_number: Mapped[str] = mapped_column(String(40), nullable=False)
    ordered_on: Mapped[date] = mapped_column(Date, nullable=False)
    expected_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PurchaseOrderStatus] = mapped_column(String(40), nullable=False)


class PurchaseOrderLine(IdMixin, Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (UniqueConstraint("purchase_order_id", "product_id"),)

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class Shipment(IdMixin, TimestampMixin, Base):
    __tablename__ = "shipments"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tracking_number: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    carrier: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[ShipmentStatus] = mapped_column(String(40), nullable=False)
    departed_on: Mapped[date | None] = mapped_column(Date)
    estimated_arrival: Mapped[date] = mapped_column(Date, nullable=False)
    actual_arrival: Mapped[date | None] = mapped_column(Date)
    delay_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delay_reason: Mapped[str | None] = mapped_column(String(255))


class TrackingEvent(IdMixin, Base):
    __tablename__ = "tracking_events"

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)


class InventorySnapshot(IdMixin, Base):
    __tablename__ = "inventory_snapshots"
    __table_args__ = (UniqueConstraint("warehouse_id", "product_id", "snapshot_date"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    on_hand: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False)
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_usage_rate: Mapped[float] = mapped_column(Float, nullable=False)


class QualityIncident(IdMixin, TimestampMixin, Base):
    __tablename__ = "quality_incidents"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    reported_on: Mapped[date] = mapped_column(Date, nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    defect_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class Document(IdMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), index=True
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), index=True
    )
    document_type: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)


embedding_type = Vector(384).with_variant(JSON(none_as_null=True), "sqlite")


class DocumentChunk(IdMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(embedding_type)


class Conversation(IdMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChatMessage(IdMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
