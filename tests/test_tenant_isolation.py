from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.user import _resolve_tenant_scope, read_users
from app.exceptions.http import ForbiddenException
from app.models.auth import TokenData
from app.services.auth import get_current_user
from app.services.report import _ReportService
from app.services.task import _TaskService
from app.utils.enums import ReportStatus, ReportType, UserRole


class CapturingSession:
    def __init__(self) -> None:
        self.statement = None

    def exec(self, statement):
        self.statement = statement
        return self

    def all(self):
        return []

    def first(self):
        return None


def make_user(role: UserRole, *, tenant_id: str | None = "tenant-alpha") -> TokenData:
    return TokenData(
        user_id=uuid4(),
        role=role,
        name="Tenant",
        surname="User",
        tenant_id=tenant_id,
        token_type="access",
    )


def test_auth_rehydrates_tenant_scope_from_live_user(monkeypatch) -> None:
    user_id = uuid4()
    token_data = TokenData(
        user_id=user_id,
        role=UserRole.MANAGER,
        name="Forged",
        surname="Token",
        tenant_id="tenant-beta",
        token_type="access",
    )
    db_user = SimpleNamespace(
        id=user_id,
        role=UserRole.MANAGER,
        name="Live",
        surname="User",
        tenant_id="tenant-alpha",
        must_change_password=False,
        credentials_updated_at=None,
        is_active=lambda: True,
    )
    session = CapturingSession()
    session.first = lambda: db_user

    monkeypatch.setattr("app.services.auth.SecurityUtils.decode_token", lambda token, token_type: token_data)

    current_user = get_current_user("encoded-token", session)

    assert current_user.tenant_id == "tenant-alpha"
    assert current_user.name == "Live"


def test_user_scope_rejects_cross_tenant_header() -> None:
    request = SimpleNamespace(headers={"X-Tenant-ID": "tenant-beta"})

    with pytest.raises(ForbiddenException, match="does not match authenticated user"):
        _resolve_tenant_scope(request, None, make_user(UserRole.MANAGER, tenant_id="tenant-alpha"))


def test_user_scope_rejects_query_header_mismatch() -> None:
    request = SimpleNamespace(headers={"X-Tenant-ID": "tenant-alpha"})

    with pytest.raises(ForbiddenException, match="Tenant scope mismatch"):
        _resolve_tenant_scope(request, "tenant-beta", make_user(UserRole.ADMIN, tenant_id=None))


def test_super_admin_can_list_users_with_optional_tenant_scope() -> None:
    calls = {}

    class StubUserService:
        def read_users(self, session, status, role, offset, limit, tenant_id):
            calls["tenant_id"] = tenant_id
            calls["limit"] = limit
            return []

    result = read_users(
        StubUserService(),
        SimpleNamespace(),
        make_user(UserRole.SUPER_ADMIN, tenant_id=None),
        limit=1000,
        tenant_id="tenant-alpha",
        request=SimpleNamespace(headers={}),
    )

    assert result == []
    assert calls == {"tenant_id": "tenant-alpha", "limit": 1000}


def test_task_list_scopes_management_user_to_own_tenant() -> None:
    service = _TaskService()
    session = CapturingSession()

    service.read_tasks(session=session, current_user=make_user(UserRole.MANAGER))

    compiled = session.statement.compile()
    assert "users.tenant_id" in str(session.statement)
    assert "tenant-alpha" in compiled.params.values()


def test_task_detail_rejects_cross_tenant_management_access(monkeypatch) -> None:
    service = _TaskService()
    service._get_task = lambda task_id, session: SimpleNamespace(id=task_id, technician_id=uuid4())  # type: ignore[method-assign]
    monkeypatch.setattr("app.services.task.get_task_tenant_id", lambda session, task: "tenant-beta")

    with pytest.raises(ForbiddenException, match="view this task"):
        service.read_task(uuid4(), CapturingSession(), make_user(UserRole.MANAGER))


def test_report_list_scopes_management_user_to_own_tenant() -> None:
    service = _ReportService()
    session = CapturingSession()

    service.read_reports(session=session, current_user=make_user(UserRole.MANAGER))

    compiled = session.statement.compile()
    assert "users.tenant_id" in str(session.statement)
    assert "tenant-alpha" in compiled.params.values()


def test_report_detail_rejects_cross_tenant_management_access(monkeypatch) -> None:
    service = _ReportService()
    service._get_report = lambda report_id, session: SimpleNamespace(id=report_id, technician_id=uuid4())  # type: ignore[method-assign]
    monkeypatch.setattr("app.services.report.get_technician_tenant_id", lambda session, technician_id: "tenant-beta")

    with pytest.raises(ForbiddenException, match="view this report"):
        service.read_report(uuid4(), CapturingSession(), make_user(UserRole.MANAGER))


def test_report_export_rejects_cross_tenant_management_access(monkeypatch) -> None:
    service = _ReportService()
    service._get_report = lambda report_id, session: SimpleNamespace(  # type: ignore[method-assign]
        id=report_id,
        technician_id=uuid4(),
        status=ReportStatus.COMPLETED,
        report_type=ReportType.DIESEL,
        created_at=None,
        task=None,
    )
    monkeypatch.setattr("app.services.report.get_technician_tenant_id", lambda session, technician_id: "tenant-beta")

    with pytest.raises(ForbiddenException, match="export this report"):
        service.export_report_pdf(uuid4(), CapturingSession(), make_user(UserRole.MANAGER))
