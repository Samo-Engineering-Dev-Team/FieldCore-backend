from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.core import SecurityUtils
from app.models import (
    AuditLog,
    Entitlement,
    EntitlementCreate,
    LicenseHistory,
    LicensePlan,
    LicensePlanCreate,
    LicenseProduct,
    LicenseProductCreate,
    TenantComplianceRecord,
    TenantFeatureUsageEvent,
    TenantLicense,
    TenantLicenseAssign,
    TenantUsageDaily,
    User,
)
from app.services.licensing import LicensingService
from app.services.licensing_compliance import LicensingComplianceService
from app.utils.enums import UserRole, UserStatus


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            LicenseProduct.__table__,
            LicensePlan.__table__,
            Entitlement.__table__,
            TenantLicense.__table__,
            LicenseHistory.__table__,
            AuditLog.__table__,
            TenantFeatureUsageEvent.__table__,
            TenantUsageDaily.__table__,
            TenantComplianceRecord.__table__,
        ],
    )
    with Session(engine) as session:
        yield session


def test_compute_daily_metering_records_usage_and_overages(session: Session) -> None:
    licensing_service = LicensingService()
    compliance_service = LicensingComplianceService()
    tenant_id = "tenant-alpha"
    usage_date = date(2026, 4, 19)

    product = licensing_service.create_product(
        LicenseProductCreate(sku="FIELDCORE-ENT", name="FieldCore Enterprise"),
        session,
    )
    plan = licensing_service.create_plan(
        LicensePlanCreate(
            license_product_id=product.id,
            code="ENT",
            name="Enterprise",
        ),
        session,
    )
    licensing_service.create_entitlement(
        EntitlementCreate(
            license_plan_id=plan.id,
            feature_key="seats",
            feature_name="Active Seats",
            grant_value="2",
        ),
        session,
    )
    licensing_service.create_entitlement(
        EntitlementCreate(
            license_plan_id=plan.id,
            feature_key="reports.export",
            feature_name="Report Export",
            grant_value="3",
        ),
        session,
    )
    licensing_service.assign_tenant_license(
        TenantLicenseAssign(
            tenant_id=tenant_id,
            license_plan_id=plan.id,
            starts_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        ),
        session,
        actor_user_id=uuid4(),
    )

    users = [
        User(
            name="User",
            surname=f"{index}",
            email=f"user{index}@example.com",
            role=UserRole.ADMIN,
            tenant_id=tenant_id,
            status=UserStatus.ACTIVE,
            password_hash=SecurityUtils.hash_password("Password123"),
        )
        for index in range(3)
    ]
    users.append(
        User(
            name="Disabled",
            surname="User",
            email="disabled@example.com",
            role=UserRole.MANAGER,
            tenant_id=tenant_id,
            status=UserStatus.DISABLED,
            password_hash=SecurityUtils.hash_password("Password123"),
        )
    )
    session.add_all(users)
    session.add(
        TenantFeatureUsageEvent(
            tenant_id=tenant_id,
            feature_key="reports.export",
            feature_name="Report Export",
            usage_quantity=4,
            occurred_at=datetime(2026, 4, 19, 8, 0, tzinfo=timezone.utc),
        )
    )
    session.commit()

    summary = compliance_service.compute_daily_metering(session, usage_date=usage_date)

    assert summary.processed_tenant_count == 1
    assert summary.usage_row_count == 2
    assert summary.compliance_row_count == 2
    assert summary.overage_tenant_count == 1

    overview = compliance_service.get_compliance_overview(
        session,
        usage_date=usage_date,
        tenant_id=tenant_id,
    )

    assert overview.usage_date == usage_date
    assert overview.tenant_count == 1
    assert overview.overage_tenant_count == 1
    assert len(overview.tenants) == 1
    assert overview.tenants[0].has_overages is True

    metrics = {
        metric.feature_key: metric
        for metric in overview.tenants[0].metrics
    }

    assert metrics["seats"].usage_value == 3
    assert metrics["seats"].entitlement_limit == 2
    assert metrics["seats"].status == "over_limit"
    assert metrics["seats"].overage_value == 1

    assert metrics["reports.export"].usage_value == 4
    assert metrics["reports.export"].entitlement_limit == 3
    assert metrics["reports.export"].status == "over_limit"
    assert metrics["reports.export"].overage_value == 1


def test_compute_daily_metering_flags_usage_without_entitlement(session: Session) -> None:
    licensing_service = LicensingService()
    compliance_service = LicensingComplianceService()
    tenant_id = "tenant-beta"
    usage_date = date(2026, 4, 19)

    product = licensing_service.create_product(
        LicenseProductCreate(sku="FIELDCORE-BASE", name="FieldCore Base"),
        session,
    )
    plan = licensing_service.create_plan(
        LicensePlanCreate(
            license_product_id=product.id,
            code="BASE",
            name="Base",
        ),
        session,
    )
    licensing_service.assign_tenant_license(
        TenantLicenseAssign(
            tenant_id=tenant_id,
            license_plan_id=plan.id,
            starts_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        ),
        session,
        actor_user_id=uuid4(),
    )

    session.add(
        TenantFeatureUsageEvent(
            tenant_id=tenant_id,
            feature_key="dashboards.executive",
            feature_name="Executive Dashboard",
            usage_quantity=2,
            occurred_at=datetime(2026, 4, 19, 9, 30, tzinfo=timezone.utc),
        )
    )
    session.commit()

    summary = compliance_service.compute_daily_metering(session, usage_date=usage_date)
    assert summary.overage_tenant_count == 1

    overview = compliance_service.get_compliance_overview(
        session,
        usage_date=usage_date,
        tenant_id=tenant_id,
    )
    metrics = {metric.feature_key: metric for metric in overview.tenants[0].metrics}

    assert metrics["dashboards.executive"].entitlement_is_enabled is False
    assert metrics["dashboards.executive"].usage_value == 2
    assert metrics["dashboards.executive"].status == "not_entitled"
    assert metrics["dashboards.executive"].overage_value == 2
