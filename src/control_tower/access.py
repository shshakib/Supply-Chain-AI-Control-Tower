from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.models import (
    Membership,
    MembershipWarehouse,
    Organization,
    PurchaseOrder,
    Role,
    Supplier,
    User,
    Warehouse,
)


class AccessDeniedError(RuntimeError):
    """Raised when a user has no membership or requests an unauthorized resource."""


@dataclass(frozen=True)
class AccessContext:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    organization_slug: str
    role: Role
    allowed_warehouse_ids: tuple[uuid.UUID, ...]
    allowed_supplier_ids: tuple[uuid.UUID, ...]

    def require_warehouse(self, warehouse_id: uuid.UUID) -> None:
        if warehouse_id not in self.allowed_warehouse_ids:
            raise AccessDeniedError("The selected warehouse is outside the user's access scope.")

    def require_supplier(self, supplier_id: uuid.UUID) -> None:
        if supplier_id not in self.allowed_supplier_ids:
            raise AccessDeniedError("The selected supplier is outside the user's access scope.")


class AccessService:
    def __init__(self, session: Session):
        self.session = session

    def resolve(self, user_email: str, organization_slug: str) -> AccessContext:
        row = self.session.execute(
            select(User, Membership, Organization)
            .join(Membership, Membership.user_id == User.id)
            .join(Organization, Organization.id == Membership.organization_id)
            .where(User.email == user_email, Organization.slug == organization_slug)
        ).one_or_none()

        if row is None:
            raise AccessDeniedError("No membership exists for this user and organization.")

        user, membership, organization = row
        role = Role(membership.role)

        if membership.all_warehouses:
            warehouse_ids = tuple(
                self.session.scalars(
                    select(Warehouse.id).where(Warehouse.organization_id == organization.id)
                ).all()
            )
        else:
            warehouse_ids = tuple(
                self.session.scalars(
                    select(MembershipWarehouse.warehouse_id).where(
                        MembershipWarehouse.membership_id == membership.id
                    )
                ).all()
            )

        if role in {Role.GLOBAL_ADMIN, Role.PROCUREMENT_ANALYST, Role.QUALITY_ANALYST}:
            supplier_ids = tuple(
                self.session.scalars(
                    select(Supplier.id).where(Supplier.organization_id == organization.id)
                ).all()
            )
        elif warehouse_ids:
            supplier_ids = tuple(
                self.session.scalars(
                    select(PurchaseOrder.supplier_id)
                    .where(PurchaseOrder.destination_warehouse_id.in_(warehouse_ids))
                    .distinct()
                ).all()
            )
        else:
            supplier_ids = ()

        return AccessContext(
            user_id=user.id,
            organization_id=organization.id,
            organization_slug=organization.slug,
            role=role,
            allowed_warehouse_ids=warehouse_ids,
            allowed_supplier_ids=supplier_ids,
        )
