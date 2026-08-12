from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.access import AccessDeniedError, AccessService
from control_tower.models import Role, Warehouse


def test_regional_persona_receives_only_assigned_warehouses(session: Session) -> None:
    access = AccessService(session).resolve(
        "noah.east@controltower.demo",
        "meridian-assembly",
    )
    codes = set(
        session.scalars(
            select(Warehouse.code).where(Warehouse.id.in_(access.allowed_warehouse_ids))
        )
    )

    assert access.role == Role.REGIONAL_OPERATIONS
    assert codes == {"TOR-01", "CHI-01"}


def test_regional_persona_cannot_request_another_regions_warehouse(session: Session) -> None:
    access = AccessService(session).resolve(
        "mia.west@controltower.demo",
        "meridian-assembly",
    )
    toronto_id = session.scalar(select(Warehouse.id).where(Warehouse.code == "TOR-01"))

    with pytest.raises(AccessDeniedError):
        access.require_warehouse(toronto_id)


def test_unknown_membership_is_denied(session: Session) -> None:
    with pytest.raises(AccessDeniedError):
        AccessService(session).resolve("unknown@example.com", "meridian-assembly")
