from datetime import timedelta
from uuid import uuid4

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from app.api.v1.licensing import router as licensing_router
from app.exceptions.http import ConflictException
from app.models import (
    Entitlement,
    EntitlementCreate,
    LicenseHistory,
    LicensePlan,
    LicensePlanCreate,
    LicenseProduct,
    LicenseProductCreate,
    TenantLicense,
    TenantLicenseAssign,
    TenantLicenseUnassign,
)
from app.services.auth import require_admin
from app.services.licensing import LicensingService, tenant_has_entitlement
from app.utils.enums import LicenseHistoryAction
from app.utils.funcs import utcnow


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            LicenseProduct.__table__,
            LicensePlan.__table__,
            Entitlement.__table__,
            TenantLicense.__table__,
            LicenseHistory.__table__,
        ],
    )
    with Session(engine) as session:
        yield session


def test_licensing_router_requires_admin() -> None:
    assert any(
        getattr(dependency, "dependency", None) == require_admin
        for dependency in licensing_router.dependencies
    )


def test_tenant_entitlement_lookup_and_history_flow(session: Session) -> None:
    service = LicensingService()
    actor_user_id = uuid4()

    product = service.create_product(
        LicenseProductCreate(
            sku="FIELDCORE-PRO",
            name="FieldCore Pro",
            description="Paid platform product",
        ),
        session,
    )
    plan = service.create_plan(
        LicensePlanCreate(
            license_product_id=product.id,
            code="PRO-MONTHLY",
            name="Pro Monthly",
        ),
        session,
    )
    service.create_entitlement(
        EntitlementCreate(
            license_plan_id=plan.id,
            feature_key="reports.export",
            feature_name="Report export",
        ),
        session,
    )

    assignment = service.assign_tenant_license(
        TenantLicenseAssign(
            tenant_id="tenant-123",
            license_plan_id=plan.id,
            note="Initial assignment",
        ),
        session,
        actor_user_id=actor_user_id,
    )

    assert tenant_has_entitlement(session, "tenant-123", "reports.export") is True
    assert tenant_has_entitlement(session, "tenant-123", "unknown.feature") is False

    ends_at = utcnow() + timedelta(minutes=5)
    service.unassign_tenant_license(
        assignment.id,
        TenantLicenseUnassign(
            ends_at=ends_at,
            note="Customer downgraded",
        ),
        session,
        actor_user_id=actor_user_id,
    )

    assert tenant_has_entitlement(
        session,
        "tenant-123",
        "reports.export",
        at=ends_at + timedelta(seconds=1),
    ) is False

    history_rows = list(
        session.exec(select(LicenseHistory).order_by(LicenseHistory.effective_at)).all()
    )
    assert [row.action for row in history_rows] == [
        LicenseHistoryAction.ASSIGNED,
        LicenseHistoryAction.UNASSIGNED,
    ]
    assert history_rows[0].note == "Initial assignment"
    assert history_rows[1].note == "Customer downgraded"


def test_assign_tenant_license_rejects_overlap(session: Session) -> None:
    service = LicensingService()

    product = service.create_product(
        LicenseProductCreate(sku="FIELDCORE-BASE", name="FieldCore Base"),
        session,
    )
    plan = service.create_plan(
        LicensePlanCreate(
            license_product_id=product.id,
            code="BASE",
            name="Base",
        ),
        session,
    )

    service.assign_tenant_license(
        TenantLicenseAssign(
            tenant_id="tenant-123",
            license_plan_id=plan.id,
        ),
        session,
        actor_user_id=uuid4(),
    )

    with pytest.raises(ConflictException, match="Overlapping tenant license assignment"):
        service.assign_tenant_license(
            TenantLicenseAssign(
                tenant_id="tenant-123",
                license_plan_id=plan.id,
            ),
            session,
            actor_user_id=uuid4(),
        )
