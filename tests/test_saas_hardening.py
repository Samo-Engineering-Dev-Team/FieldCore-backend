from uuid import uuid4

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.core.metrics import RequestMetric, tenant_metrics
from app.core.rate_limiter import RateLimitIdentity, TenantRateLimiter
from app.exceptions.http import ForbiddenException
from app.models import (
    AuditLog,
    SystemSetting,
    Tenant,
    TenantOperationLog,
    TenantOperationType,
    User,
    Webhook,
)
from app.models.auth import TokenData
from app.services.support_diagnostics import get_support_diagnostics_service
from app.utils.enums import UserRole


def test_rate_limiter_enforces_per_tenant_subject() -> None:
    limiter = TenantRateLimiter(enabled=True, limit=2, window_seconds=60, redis_url="")
    tenant_a = RateLimitIdentity(
        tenant_id="tenant-a",
        subject_id="user-1",
        subject_type="user",
        role=UserRole.ADMIN,
    )
    tenant_b = RateLimitIdentity(
        tenant_id="tenant-b",
        subject_id="user-1",
        subject_type="user",
        role=UserRole.ADMIN,
    )

    assert limiter.check_identity(tenant_a).allowed is True
    assert limiter.check_identity(tenant_a).allowed is True
    assert limiter.check_identity(tenant_a).allowed is False
    assert limiter.check_identity(tenant_b).allowed is True


def test_rate_limiter_allows_super_admin_emergency_override() -> None:
    limiter = TenantRateLimiter(enabled=True, limit=1, window_seconds=60, redis_url="")
    identity = RateLimitIdentity(
        tenant_id="platform",
        subject_id="super-admin",
        subject_type="user",
        role=UserRole.SUPER_ADMIN,
        override_requested=True,
    )

    assert limiter.check_identity(identity).allowed is True
    assert limiter.check_identity(identity).allowed is True


def test_prometheus_metrics_include_tenant_label() -> None:
    tenant_metrics.reset()
    tenant_metrics.record_request(
        RequestMetric(
            tenant_id="tenant-a",
            method="GET",
            path="/api/v1/tasks",
            status_code=200,
            duration_seconds=0.25,
        )
    )

    rendered = tenant_metrics.render_prometheus()

    assert 'tenant_id="tenant-a"' in rendered
    assert 'path="/api/v1/tasks"' in rendered
    assert "fieldcore_http_requests_total" in rendered


def _diagnostics_session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            TenantOperationLog.__table__,
            AuditLog.__table__,
            User.__table__,
            SystemSetting.__table__,
            Webhook.__table__,
        ],
    )
    return Session(engine)


def _token(role: UserRole, tenant_id: str | None) -> TokenData:
    return TokenData(
        user_id=uuid4(),
        role=role,
        name="Support",
        surname="User",
        tenant_id=tenant_id,
        token_type="access",
    )


def test_support_diagnostics_returns_tenant_health_summary() -> None:
    service = get_support_diagnostics_service()

    with _diagnostics_session() as session:
        session.add(Tenant(id="tenant-alpha", slug="tenant-alpha", name="Tenant Alpha"))
        session.add(
            User(
                name="Ada",
                surname="Admin",
                email="admin@alpha.test",
                role=UserRole.ADMIN,
                tenant_id="tenant-alpha",
                password_hash="hash",
            )
        )
        session.add(
            SystemSetting(
                tenant_id="tenant-alpha",
                key="noc_email_addresses",
                value={"emails": ["admin@alpha.test"]},
                category="notifications",
            )
        )
        session.add(
            Webhook(
                tenant_id="tenant-alpha",
                url="https://example.test/webhook",
                event_type="incident_created",
                is_active=True,
            )
        )
        session.add(
            TenantOperationLog(
                tenant_id="tenant-alpha",
                operation=TenantOperationType.BOOTSTRAP,
                dry_run=False,
                status="completed",
                message="Tenant created",
            )
        )
        session.commit()

        response = service.read_tenant_diagnostics(
            "tenant-alpha",
            session,
            _token(UserRole.ADMIN, "tenant-alpha"),
        )

        assert response.tenant_id == "tenant-alpha"
        assert response.counts["users_total"] == 1
        assert response.counts["system_settings"] == 1
        assert response.counts["webhooks_active"] == 1
        checks = {check.name: check.ok for check in response.checks}
        assert checks["tenant_exists"] is True
        assert checks["active_admin"] is True


def test_support_diagnostics_rejects_other_tenant_admin() -> None:
    service = get_support_diagnostics_service()

    with _diagnostics_session() as session:
        session.add(Tenant(id="tenant-alpha", slug="tenant-alpha", name="Tenant Alpha"))
        session.commit()

        with pytest.raises(ForbiddenException):
            service.read_tenant_diagnostics(
                "tenant-alpha",
                session,
                _token(UserRole.ADMIN, "tenant-beta"),
            )
