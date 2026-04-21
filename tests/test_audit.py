from uuid import uuid4

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.api.v1.audit import router as audit_router
from app.exceptions.http import ForbiddenException
from app.models import AuditLog
from app.models.auth import TokenData
from app.services.audit import AuditService, write_audit_event
from app.services.auth import require_platform_admin
from app.utils.enums import UserRole


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[AuditLog.__table__])
    return Session(engine)


def _token(role: UserRole, tenant_id: str | None = None) -> TokenData:
    return TokenData(
        user_id=uuid4(),
        role=role,
        tenant_id=tenant_id,
        token_type="access",
    )


def test_write_audit_event_and_filter_by_tenant_action_and_request() -> None:
    actor_user_id = uuid4()

    with _session() as session:
        write_audit_event(
            session,
            actor_user_id=actor_user_id,
            tenant_id="tenant-alpha",
            action_type="tenant.bootstrap",
            resource="tenant:tenant-alpha",
            before=None,
            after={"status": "active"},
            request_id="req-123",
        )
        write_audit_event(
            session,
            actor_user_id=actor_user_id,
            tenant_id="tenant-beta",
            action_type="tenant.offboard",
            resource="tenant:tenant-beta",
            before={"status": "active"},
            after={"status": "archived"},
            request_id="req-456",
        )
        session.commit()

        result = AuditService().list_logs(
            session,
            tenant_id="tenant-alpha",
            action_type="tenant.bootstrap",
            request_id="req-123",
        )

        assert result.total == 1
        assert result.data[0].tenant_id == "tenant-alpha"
        assert result.data[0].actor_user_id == actor_user_id
        assert result.data[0].after == {"status": "active"}


def test_audit_router_requires_platform_admin() -> None:
    assert any(
        getattr(dependency, "dependency", None) == require_platform_admin
        for dependency in audit_router.dependencies
    )


def test_platform_admin_dependency_rejects_tenant_admin() -> None:
    assert require_platform_admin(_token(UserRole.SUPER_ADMIN)).role == UserRole.SUPER_ADMIN
    assert require_platform_admin(_token(UserRole.ADMIN, tenant_id=None)).role == UserRole.ADMIN

    with pytest.raises(ForbiddenException, match="Platform admin"):
        require_platform_admin(_token(UserRole.ADMIN, tenant_id="tenant-alpha"))
