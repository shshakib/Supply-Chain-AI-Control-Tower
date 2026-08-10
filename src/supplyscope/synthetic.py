from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from supplyscope.models import (
    Document,
    DocumentChunk,
    InventorySnapshot,
    Membership,
    MembershipWarehouse,
    Organization,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    QualityIncident,
    Role,
    Shipment,
    ShipmentStatus,
    Supplier,
    SupplierProduct,
    TrackingEvent,
    User,
    Warehouse,
)

DEMO_AS_OF = date(2026, 6, 30)
UUID_NAMESPACE = uuid.UUID("53f2ef74-eec8-4ae3-a129-6416d22664d4")


def stable_uuid(entity: str, key: str) -> uuid.UUID:
    return uuid.uuid5(UUID_NAMESPACE, f"{entity}:{key}")


@dataclass(frozen=True)
class SeedSummary:
    organization: str
    as_of: date
    users: int
    warehouses: int
    suppliers: int
    products: int
    purchase_orders: int
    shipments: int
    inventory_snapshots: int
    documents: int


class SyntheticDataGenerator:
    def __init__(
        self,
        session: Session,
        *,
        seed: int = 42,
        as_of: date = DEMO_AS_OF,
        document_dir: Path | None = None,
    ) -> None:
        self.session = session
        self.rng = random.Random(seed)
        self.as_of = as_of
        self.document_dir = document_dir

    def generate(self) -> SeedSummary:
        existing = self.session.scalar(
            select(Organization).where(Organization.slug == "meridian-assembly")
        )
        if existing is not None:
            raise RuntimeError(
                "Synthetic data already exists for meridian-assembly. "
                "Start with an empty database to regenerate it."
            )

        organization = Organization(
            id=stable_uuid("organization", "meridian-assembly"),
            slug="meridian-assembly",
            name="Meridian Assembly Group",
        )
        self.session.add(organization)
        self.session.flush()

        warehouses = self._create_warehouses(organization)
        self.session.flush()
        self._create_users_and_access(organization, warehouses)
        suppliers = self._create_suppliers(organization)
        products = self._create_products(organization)
        self.session.flush()
        supplier_products = self._create_supplier_products(suppliers, products)
        self.session.flush()

        purchase_orders, shipments = self._create_background_orders(
            organization,
            warehouses,
            suppliers,
            products,
            supplier_products,
        )
        scenario_order, scenario_shipment = self._create_critical_scenario(
            organization,
            warehouses[0],
            suppliers[0],
            products[0],
            supplier_products[(suppliers[0].id, products[0].id)],
        )
        purchase_orders.append(scenario_order)
        shipments.append(scenario_shipment)
        self.session.flush()

        inventory_count = self._create_inventory(organization, warehouses, products)
        self._create_quality_incidents(organization, suppliers, products)
        self.session.flush()

        document_count = self._create_documents(
            organization,
            warehouses,
            suppliers,
            scenario_order,
            scenario_shipment,
        )

        self.session.flush()
        return SeedSummary(
            organization=organization.name,
            as_of=self.as_of,
            users=6,
            warehouses=len(warehouses),
            suppliers=len(suppliers),
            products=len(products),
            purchase_orders=len(purchase_orders),
            shipments=len(shipments),
            inventory_snapshots=inventory_count,
            documents=document_count,
        )

    def _create_warehouses(self, organization: Organization) -> list[Warehouse]:
        definitions = [
            ("TOR-01", "Toronto Components Hub", "Toronto", "east"),
            ("CHI-01", "Chicago Assembly Center", "Chicago", "east"),
            ("VAN-01", "Vancouver Distribution Hub", "Vancouver", "west"),
            ("AUS-01", "Austin Manufacturing Center", "Austin", "west"),
        ]
        warehouses = [
            Warehouse(
                id=stable_uuid("warehouse", code),
                organization_id=organization.id,
                code=code,
                name=name,
                city=city,
                region=region,
            )
            for code, name, city, region in definitions
        ]
        self.session.add_all(warehouses)
        return warehouses

    def _create_users_and_access(
        self,
        organization: Organization,
        warehouses: list[Warehouse],
    ) -> None:
        personas = [
            ("ava.admin@supplyscope.demo", "Ava Chen", Role.GLOBAL_ADMIN, True, []),
            (
                "noah.east@supplyscope.demo",
                "Noah Williams",
                Role.REGIONAL_OPERATIONS,
                False,
                ["TOR-01", "CHI-01"],
            ),
            (
                "mia.west@supplyscope.demo",
                "Mia Garcia",
                Role.REGIONAL_OPERATIONS,
                False,
                ["VAN-01", "AUS-01"],
            ),
            (
                "priya.procurement@supplyscope.demo",
                "Priya Raman",
                Role.PROCUREMENT_ANALYST,
                True,
                [],
            ),
            (
                "leo.quality@supplyscope.demo",
                "Leo Martin",
                Role.QUALITY_ANALYST,
                True,
                [],
            ),
            (
                "sofia.viewer@supplyscope.demo",
                "Sofia Rossi",
                Role.VIEWER,
                False,
                ["TOR-01"],
            ),
        ]
        warehouse_by_code = {warehouse.code: warehouse for warehouse in warehouses}

        users = [
            User(
                id=stable_uuid("user", email),
                email=email,
                display_name=display_name,
            )
            for email, display_name, _role, _all_warehouses, _codes in personas
        ]
        self.session.add_all(users)
        self.session.flush()

        memberships: list[tuple[Membership, list[str]]] = []
        for email, _display_name, role, all_warehouses, warehouse_codes in personas:
            membership = Membership(
                id=stable_uuid("membership", email),
                user_id=stable_uuid("user", email),
                organization_id=organization.id,
                role=role,
                all_warehouses=all_warehouses,
            )
            self.session.add(membership)
            memberships.append((membership, warehouse_codes))

        self.session.flush()
        for membership, warehouse_codes in memberships:
            self.session.add_all(
                MembershipWarehouse(
                    id=stable_uuid("membership-warehouse", f"{membership.id}:{code}"),
                    membership_id=membership.id,
                    warehouse_id=warehouse_by_code[code].id,
                )
                for code in warehouse_codes
            )

    def _create_suppliers(self, organization: Organization) -> list[Supplier]:
        definitions = [
            ("SUP-001", "Apex Circuits", "Taiwan"),
            ("SUP-002", "Nordic Motion", "Sweden"),
            ("SUP-003", "Kestrel Metals", "United States"),
            ("SUP-004", "Sakura Sensors", "Japan"),
            ("SUP-005", "Rhine Controls", "Germany"),
            ("SUP-006", "Pacific Polymers", "Canada"),
            ("SUP-007", "Andes Cableworks", "Chile"),
            ("SUP-008", "Brighton Fasteners", "United Kingdom"),
            ("SUP-009", "Atlas Power Systems", "Mexico"),
            ("SUP-010", "Orchid Displays", "South Korea"),
            ("SUP-011", "Delta Precision", "United States"),
            ("SUP-012", "Terra Packaging", "Canada"),
        ]
        suppliers = []
        for index, (code, name, country) in enumerate(definitions):
            late_probability = 0.09 + (index % 4) * 0.035
            if code == "SUP-001":
                late_probability = 0.18
            supplier = Supplier(
                id=stable_uuid("supplier", code),
                organization_id=organization.id,
                code=code,
                name=name,
                country=country,
                typical_lead_time_days=12 + (index % 6) * 3,
                late_delivery_probability=late_probability,
                defect_probability=0.012 + (index % 5) * 0.008,
            )
            suppliers.append(supplier)
        self.session.add_all(suppliers)
        return suppliers

    def _create_products(self, organization: Organization) -> list[Product]:
        names = [
            ("MCU-X100", "X100 Control Microcontroller"),
            ("SNS-T900", "T900 Thermal Sensor"),
            ("MTR-S220", "S220 Servo Motor"),
            ("DRV-V410", "V410 Variable Drive"),
            ("PSU-H800", "H800 Power Supply"),
            ("BRG-6204", "6204 Sealed Bearing"),
            ("CBL-ETH6", "Industrial Ethernet Cable"),
            ("ENC-IP67", "IP67 Equipment Enclosure"),
            ("PCB-IO24", "24-Channel IO Board"),
            ("RLY-40A", "40A Safety Relay"),
            ("DSP-070", "Seven Inch Operator Display"),
            ("FAN-120", "120mm Cooling Fan"),
            ("FLT-HEPA", "Compact HEPA Filter"),
            ("VAL-P300", "P300 Proportional Valve"),
            ("PMP-C50", "C50 Circulation Pump"),
            ("FST-M6", "M6 Stainless Fastener Set"),
            ("GSK-120", "120mm Silicone Gasket"),
            ("CON-16P", "16-Pin Industrial Connector"),
            ("TRM-240", "DIN Rail Terminal Block"),
            ("BAT-24V", "24V Backup Battery"),
            ("LNS-IR5", "IR Inspection Lens"),
            ("LED-SIG", "Three-Color Signal Tower"),
            ("SWI-EST", "Emergency Stop Switch"),
            ("PNE-08", "8mm Pneumatic Tube"),
            ("REG-P10", "P10 Pressure Regulator"),
            ("FRM-A20", "A20 Aluminum Frame"),
            ("PNL-C12", "C12 Control Panel"),
            ("LBL-QR", "Serialized QR Label"),
            ("PKG-FOM", "Protective Foam Insert"),
            ("BOX-R40", "R40 Shipping Carton"),
        ]
        products = [
            Product(
                id=stable_uuid("product", sku),
                organization_id=organization.id,
                sku=sku,
                name=name,
                critical=index < 8,
            )
            for index, (sku, name) in enumerate(names)
        ]
        self.session.add_all(products)
        return products

    def _create_supplier_products(
        self,
        suppliers: list[Supplier],
        products: list[Product],
    ) -> dict[tuple[uuid.UUID, uuid.UUID], SupplierProduct]:
        links: dict[tuple[uuid.UUID, uuid.UUID], SupplierProduct] = {}
        for index, product in enumerate(products):
            supplier = suppliers[index % len(suppliers)]
            link = SupplierProduct(
                id=stable_uuid("supplier-product", f"{supplier.code}:{product.sku}"),
                supplier_id=supplier.id,
                product_id=product.id,
                unit_cost=Decimal(str(round(8 + self.rng.random() * 190, 2))),
            )
            links[(supplier.id, product.id)] = link
            self.session.add(link)

        apex_product = products[0]
        apex = suppliers[0]
        links[(apex.id, apex_product.id)].unit_cost = Decimal("84.50")
        return links

    def _create_background_orders(
        self,
        organization: Organization,
        warehouses: list[Warehouse],
        suppliers: list[Supplier],
        products: list[Product],
        supplier_products: dict[tuple[uuid.UUID, uuid.UUID], SupplierProduct],
    ) -> tuple[list[PurchaseOrder], list[Shipment]]:
        orders: list[PurchaseOrder] = []
        shipments: list[Shipment] = []
        lines: list[PurchaseOrderLine] = []
        events: list[TrackingEvent] = []
        carriers = ["NorthStar Freight", "BlueArc Logistics", "Continental Air Cargo"]
        delay_reasons = [
            "port congestion",
            "carrier capacity constraint",
            "customs documentation review",
            "severe weather",
        ]

        for index in range(180):
            product_index = index % len(products)
            product = products[product_index]
            supplier = suppliers[product_index % len(suppliers)]
            warehouse = warehouses[index % len(warehouses)]
            link = supplier_products[(supplier.id, product.id)]
            ordered_on = self.as_of - timedelta(days=self.rng.randint(10, 100))
            promised_on = ordered_on + timedelta(days=supplier.typical_lead_time_days)
            delayed = self.rng.random() < supplier.late_delivery_probability
            delay_days = self.rng.randint(2, 11) if delayed else 0
            expected_arrival = promised_on + timedelta(days=delay_days)

            if expected_arrival < self.as_of - timedelta(days=2):
                shipment_status = ShipmentStatus.DELIVERED
                order_status = PurchaseOrderStatus.RECEIVED
                actual_arrival = expected_arrival
            elif delayed:
                shipment_status = ShipmentStatus.DELAYED
                order_status = PurchaseOrderStatus.OPEN
                actual_arrival = None
            else:
                shipment_status = ShipmentStatus.IN_TRANSIT
                order_status = PurchaseOrderStatus.OPEN
                actual_arrival = None

            order_number = f"PO-{20260000 + index:08d}"
            order = PurchaseOrder(
                id=stable_uuid("purchase-order", order_number),
                organization_id=organization.id,
                supplier_id=supplier.id,
                destination_warehouse_id=warehouse.id,
                order_number=order_number,
                ordered_on=ordered_on,
                expected_on=promised_on,
                status=order_status,
            )
            line = PurchaseOrderLine(
                id=stable_uuid("purchase-order-line", order_number),
                purchase_order_id=order.id,
                product_id=product.id,
                quantity=self.rng.randint(100, 1200),
                unit_cost=link.unit_cost,
            )
            tracking_number = f"SS{index:09d}"
            shipment = Shipment(
                id=stable_uuid("shipment", tracking_number),
                organization_id=organization.id,
                purchase_order_id=order.id,
                tracking_number=tracking_number,
                carrier=carriers[index % len(carriers)],
                status=shipment_status,
                departed_on=ordered_on + timedelta(days=2),
                estimated_arrival=expected_arrival,
                actual_arrival=actual_arrival,
                delay_days=delay_days,
                delay_reason=self.rng.choice(delay_reasons) if delayed else None,
            )
            event = TrackingEvent(
                id=stable_uuid("tracking-event", f"{tracking_number}:departed"),
                shipment_id=shipment.id,
                occurred_at=datetime.combine(
                    ordered_on + timedelta(days=2), time(9, 0), tzinfo=UTC
                ),
                location=supplier.country,
                event_type="departed_origin",
                details=f"Shipment departed via {shipment.carrier}.",
            )
            orders.append(order)
            lines.append(line)
            shipments.append(shipment)
            events.append(event)

        self.session.add_all(orders)
        self.session.flush()
        self.session.add_all([*lines, *shipments])
        self.session.flush()
        self.session.add_all(events)
        return orders, shipments

    def _create_critical_scenario(
        self,
        organization: Organization,
        warehouse: Warehouse,
        supplier: Supplier,
        product: Product,
        supplier_product: SupplierProduct,
    ) -> tuple[PurchaseOrder, Shipment]:
        order = PurchaseOrder(
            id=stable_uuid("purchase-order", "PO-CRITICAL-001"),
            organization_id=organization.id,
            supplier_id=supplier.id,
            destination_warehouse_id=warehouse.id,
            order_number="PO-CRITICAL-001",
            ordered_on=self.as_of - timedelta(days=24),
            expected_on=self.as_of - timedelta(days=2),
            status=PurchaseOrderStatus.OPEN,
        )
        line = PurchaseOrderLine(
            id=stable_uuid("purchase-order-line", "PO-CRITICAL-001:MCU-X100"),
            purchase_order_id=order.id,
            product_id=product.id,
            quantity=800,
            unit_cost=supplier_product.unit_cost,
        )
        shipment = Shipment(
            id=stable_uuid("shipment", "SS-CRITICAL-001"),
            organization_id=organization.id,
            purchase_order_id=order.id,
            tracking_number="SS-CRITICAL-001",
            carrier="BlueArc Logistics",
            status=ShipmentStatus.DELAYED,
            departed_on=self.as_of - timedelta(days=19),
            estimated_arrival=self.as_of + timedelta(days=7),
            delay_days=9,
            delay_reason="Port closure following a labor disruption",
        )
        events = [
            TrackingEvent(
                id=stable_uuid("tracking-event", "SS-CRITICAL-001:departed"),
                shipment_id=shipment.id,
                occurred_at=datetime.combine(
                    self.as_of - timedelta(days=19), time(8, 30), tzinfo=UTC
                ),
                location="Kaohsiung, Taiwan",
                event_type="departed_origin",
                details="Container loaded and departed the supplier consolidation point.",
            ),
            TrackingEvent(
                id=stable_uuid("tracking-event", "SS-CRITICAL-001:exception"),
                shipment_id=shipment.id,
                occurred_at=datetime.combine(
                    self.as_of - timedelta(days=3), time(15, 20), tzinfo=UTC
                ),
                location="Port of Vancouver, Canada",
                event_type="delay_exception",
                details="Port closure delayed unloading and onward rail transfer.",
            ),
        ]
        self.session.add(order)
        self.session.flush()
        self.session.add_all([line, shipment])
        self.session.flush()
        self.session.add_all(events)
        return order, shipment

    def _create_inventory(
        self,
        organization: Organization,
        warehouses: list[Warehouse],
        products: list[Product],
    ) -> int:
        count = 0
        for warehouse in warehouses:
            for product_index, product in enumerate(products):
                base_on_hand = self.rng.randint(160, 900)
                reorder_point = self.rng.randint(80, 180)
                usage_rate = round(self.rng.uniform(3.0, 18.0), 1)
                reserved = self.rng.randint(5, 70)

                for day_offset in range(30):
                    snapshot_date = self.as_of - timedelta(days=29 - day_offset)
                    on_hand = max(0, base_on_hand - day_offset * self.rng.randint(1, 5))

                    if warehouse.code == "TOR-01" and product.sku == "MCU-X100":
                        on_hand = max(45, 190 - day_offset * 5)
                        reserved = 15
                        reorder_point = 100
                        usage_rate = 8.0

                    snapshot = InventorySnapshot(
                        id=stable_uuid(
                            "inventory",
                            f"{warehouse.code}:{product.sku}:{snapshot_date.isoformat()}",
                        ),
                        organization_id=organization.id,
                        warehouse_id=warehouse.id,
                        product_id=product.id,
                        snapshot_date=snapshot_date,
                        on_hand=on_hand,
                        reserved=min(reserved, on_hand),
                        reorder_point=reorder_point,
                        daily_usage_rate=usage_rate,
                    )
                    self.session.add(snapshot)
                    count += 1

                if product_index % 10 == 0:
                    base_on_hand = max(base_on_hand, reorder_point + reserved + 50)
        return count

    def _create_quality_incidents(
        self,
        organization: Organization,
        suppliers: list[Supplier],
        products: list[Product],
    ) -> None:
        severities = ["low", "medium", "high"]
        descriptions = [
            "Dimensional inspection exceeded the accepted tolerance.",
            "Incoming batch contained damaged protective packaging.",
            "Electrical test identified intermittent signal loss.",
            "Certificate of conformance required correction.",
        ]
        for index in range(30):
            supplier = suppliers[index % len(suppliers)]
            product = products[index % len(products)]
            incident = QualityIncident(
                id=stable_uuid("quality-incident", str(index)),
                organization_id=organization.id,
                supplier_id=supplier.id,
                product_id=product.id,
                reported_on=self.as_of - timedelta(days=self.rng.randint(1, 120)),
                severity=severities[index % len(severities)],
                status="open" if index % 4 == 0 else "closed",
                defect_quantity=self.rng.randint(1, 45),
                description=descriptions[index % len(descriptions)],
            )
            self.session.add(incident)

    def _create_documents(
        self,
        organization: Organization,
        warehouses: list[Warehouse],
        suppliers: list[Supplier],
        scenario_order: PurchaseOrder,
        scenario_shipment: Shipment,
    ) -> int:
        document_count = 0
        for index, supplier in enumerate(suppliers):
            grace_period = 5 if supplier.code == "SUP-001" else 7 + index % 3
            penalty = 4 if supplier.code == "SUP-001" else 2 + index % 4
            filename = f"{supplier.code.lower()}-master-supply-agreement.md"
            content = (
                f"# Master Supply Agreement: {supplier.name}\n\n"
                "## Delivery performance\n\n"
                f"{supplier.name} shall maintain an on-time delivery rate of at least 95%. "
                f"A delivery more than {grace_period} calendar days late is a material "
                "service-level failure.\n\n"
                "## Late-delivery remedy\n\n"
                f"After the {grace_period}-day grace period, Meridian Assembly Group may "
                f"claim a late-delivery credit equal to {penalty}% of the affected order "
                "line value. The credit does not apply when a documented force-majeure "
                "event is accepted by both parties.\n\n"
                "## Quality documentation\n\n"
                "Certificates of conformance must accompany all critical components."
            )
            document = Document(
                id=stable_uuid("document", filename),
                organization_id=organization.id,
                supplier_id=supplier.id,
                document_type="supplier_contract",
                title=f"Master Supply Agreement: {supplier.name}",
                source_filename=filename,
            )
            self._add_document(document, content)
            document_count += 1

        incident_filename = "port-disruption-ss-critical-001.md"
        incident_content = (
            "# Logistics Incident Report: SS-CRITICAL-001\n\n"
            "## Impact\n\n"
            f"Shipment {scenario_shipment.tracking_number} for purchase order "
            f"{scenario_order.order_number} contains 800 MCU-X100 control "
            "microcontrollers for Toronto Components Hub. A labor disruption closed "
            "the unloading terminal and delayed onward rail transfer.\n\n"
            "## Current estimate\n\n"
            f"The revised arrival date is {scenario_shipment.estimated_arrival.isoformat()}, "
            "nine days after the contractual date. Current inventory is expected to "
            "support fewer than four days of planned production.\n\n"
            "## Mitigation\n\n"
            "Procurement is evaluating an air-freight recovery shipment of 250 units. "
            "Legal should determine whether the port labor disruption satisfies the "
            "force-majeure clause before applying a service credit."
        )
        incident_document = Document(
            id=stable_uuid("document", incident_filename),
            organization_id=organization.id,
            warehouse_id=warehouses[0].id,
            supplier_id=suppliers[0].id,
            document_type="incident_report",
            title="Logistics Incident Report: SS-CRITICAL-001",
            source_filename=incident_filename,
        )
        self._add_document(incident_document, incident_content)
        document_count += 1

        policy_filename = "supplier-risk-policy.md"
        policy_content = (
            "# Supplier Risk Policy\n\n"
            "## Escalation thresholds\n\n"
            "Operations must escalate any delayed shipment that supplies a critical "
            "component when projected inventory cover falls below seven days.\n\n"
            "## Required review\n\n"
            "Procurement reviews contractual remedies. Quality reviews open incidents, "
            "and operations records an approved recovery plan."
        )
        policy_document = Document(
            id=stable_uuid("document", policy_filename),
            organization_id=organization.id,
            document_type="internal_policy",
            title="Supplier Risk Policy",
            source_filename=policy_filename,
        )
        self._add_document(policy_document, policy_content)
        document_count += 1
        return document_count

    def _add_document(self, document: Document, content: str) -> None:
        self.session.add(document)
        self.session.flush()
        sections = [section.strip() for section in content.split("\n\n") if section.strip()]
        heading: str | None = None
        chunk_index = 0
        for section in sections:
            if section.startswith("#"):
                heading = section.lstrip("# ")
                continue
            chunk = DocumentChunk(
                id=stable_uuid("document-chunk", f"{document.source_filename}:{chunk_index}"),
                document_id=document.id,
                chunk_index=chunk_index,
                heading=heading,
                content=section,
                chunk_metadata={"synthetic": True, "section": heading},
                embedding=None,
            )
            self.session.add(chunk)
            chunk_index += 1

        if self.document_dir is not None:
            self.document_dir.mkdir(parents=True, exist_ok=True)
            (self.document_dir / document.source_filename).write_text(content, encoding="utf-8")
