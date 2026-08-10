from __future__ import annotations

from sqlalchemy.orm import Session

from supplyscope.access import AccessService
from supplyscope.synthetic import DEMO_AS_OF
from supplyscope.tools import DocumentTools, InventoryTools, ShipmentTools


def test_east_persona_can_see_correlated_critical_scenario(session: Session) -> None:
    access = AccessService(session).resolve(
        "noah.east@supplyscope.demo",
        "meridian-assembly",
    )

    inventory = InventoryTools(session).list_low_stock(
        access,
        snapshot_date=DEMO_AS_OF,
        critical_only=True,
    )
    shipments = ShipmentTools(session).list_delayed_shipments(
        access,
        as_of=DEMO_AS_OF,
    )

    mcu_risk = next(item for item in inventory if item.sku == "MCU-X100")
    critical_shipment = next(
        item for item in shipments if item.tracking_number == "SS-CRITICAL-001"
    )

    assert mcu_risk.warehouse_code == "TOR-01"
    assert mcu_risk.available_units == 30
    assert mcu_risk.days_of_cover == 3.8
    assert critical_shipment.delay_days == 9
    assert critical_shipment.supplier_name == "Apex Circuits"


def test_west_persona_cannot_retrieve_east_incident_document(session: Session) -> None:
    access = AccessService(session).resolve(
        "mia.west@supplyscope.demo",
        "meridian-assembly",
    )

    matches = DocumentTools(session).keyword_search(
        access,
        query="unloading terminal",
    )

    assert matches == []
