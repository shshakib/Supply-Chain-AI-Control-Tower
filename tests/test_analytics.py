from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from supplyscope.access import AccessDeniedError, AccessService
from supplyscope.analytics import ShipmentAnalytics, SupplierRiskAnalytics


def test_supplier_risk_ranking_has_explainable_metrics(session: Session) -> None:
    access = AccessService(session).resolve(
        "priya.procurement@supplyscope.demo",
        "meridian-assembly",
    )

    ranked = SupplierRiskAnalytics(session).rank(access, limit=5)

    assert len(ranked) == 5
    assert ranked == sorted(ranked, key=lambda item: item.risk_score, reverse=True)
    assert all(0 <= item.risk_score <= 100 for item in ranked)
    assert all(item.shipments >= item.delayed_shipments for item in ranked)


def test_tracking_history_enforces_warehouse_scope(session: Session) -> None:
    access = AccessService(session).resolve(
        "mia.west@supplyscope.demo",
        "meridian-assembly",
    )

    with pytest.raises(AccessDeniedError):
        ShipmentAnalytics(session).tracking_history(
            access,
            tracking_number="SS-CRITICAL-001",
        )
