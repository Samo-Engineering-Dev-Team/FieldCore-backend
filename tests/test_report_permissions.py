from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.exceptions.http import ForbiddenException
from app.models import ReportCreate, ReportUpdate
from app.models.auth import TokenData
from app.services.report import _ReportService
from app.utils.enums import ReportStatus, ReportType, TaskType, UserRole


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


def _make_user(role: UserRole) -> TokenData:
    return TokenData(
        user_id=uuid4(),
        role=role,
        name="Tech",
        surname="User",
        token_type="access",
    )


def test_read_reports_scopes_technician_to_own_reports() -> None:
    service = _ReportService()
    session = CapturingSession()
    current_user = _make_user(UserRole.TECHNICIAN)
    own_technician_id = uuid4()

    service._get_technician_by_user = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(id=own_technician_id)
    )

    service.read_reports(
        session=session,
        current_user=current_user,
        technician_id=uuid4(),
    )

    compiled = session.statement.compile()

    assert service._get_technician_by_user.called
    assert own_technician_id in compiled.params.values()


def test_read_report_rejects_technician_viewing_another_technician_report() -> None:
    service = _ReportService()
    session = MagicMock()
    current_user = _make_user(UserRole.TECHNICIAN)
    own_technician_id = uuid4()
    other_report = SimpleNamespace(id=uuid4(), technician_id=uuid4())

    service._get_technician_by_user = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(id=own_technician_id)
    )
    service._get_report = MagicMock(return_value=other_report)  # type: ignore[method-assign]

    with pytest.raises(ForbiddenException, match="view their own reports"):
        service.read_report(other_report.id, session, current_user)


def test_partner_can_read_any_report() -> None:
    service = _ReportService()
    session = MagicMock()
    current_user = _make_user(UserRole.PARTNER)
    report = SimpleNamespace(id=uuid4(), technician_id=uuid4())
    response = SimpleNamespace(id=report.id)

    service._get_report = MagicMock(return_value=report)  # type: ignore[method-assign]
    service.report_to_response = MagicMock(return_value=response)  # type: ignore[method-assign]

    assert service.read_report(report.id, session, current_user) is response


def test_partner_can_list_reports_without_technician_scope() -> None:
    service = _ReportService()
    session = CapturingSession()
    current_user = _make_user(UserRole.PARTNER)

    service._get_technician_by_user = MagicMock()  # type: ignore[method-assign]

    service.read_reports(session=session, current_user=current_user)

    service._get_technician_by_user.assert_not_called()


def test_read_reports_orders_by_created_at_desc_and_defaults_to_a_high_limit() -> None:
    """Regression guard: with no ORDER BY, Postgres could return rows in an
    arbitrary order once total report count exceeded the page limit, which
    silently dropped an unpredictable subset of reports (repeater/diesel
    disappeared from the web list once total rows passed the old default of
    100). Ordering makes the drop deterministic (oldest first); raising the
    default limit to the API's own ceiling (1000) means the drop doesn't
    happen at today's report volumes at all.
    """
    service = _ReportService()
    session = CapturingSession()
    current_user = _make_user(UserRole.PARTNER)

    service.read_reports(session=session, current_user=current_user)

    compiled = session.statement.compile()
    rendered_sql = str(compiled)

    assert "ORDER BY" in rendered_sql
    assert "created_at DESC" in rendered_sql
    assert compiled.params.get("param_1") == 1000 or 1000 in compiled.params.values()


def test_delete_unfinished_self_started_field_report_deletes_linked_task() -> None:
    service = _ReportService()
    technician_id = uuid4()
    task = SimpleNamespace(
        task_type=TaskType.ROUTINE_MAINTENANCE,
        assigned_by_name="Technician self-started",
        technician_id=technician_id,
        soft_delete=MagicMock(),
    )
    report = SimpleNamespace(
        status=ReportStatus.STARTED,
        task=task,
        technician_id=technician_id,
    )

    service._delete_self_started_field_work_task(report)  # type: ignore[arg-type]

    task.soft_delete.assert_called_once()


def test_delete_unfinished_assigned_field_report_keeps_linked_task() -> None:
    service = _ReportService()
    technician_id = uuid4()
    task = SimpleNamespace(
        task_type=TaskType.ROUTINE_MAINTENANCE,
        assigned_by_name="NOC User",
        technician_id=technician_id,
        soft_delete=MagicMock(),
    )
    report = SimpleNamespace(
        status=ReportStatus.STARTED,
        task=task,
        technician_id=technician_id,
    )

    service._delete_self_started_field_work_task(report)  # type: ignore[arg-type]

    task.soft_delete.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "args", "message"),
    [
        ("update_report", (ReportUpdate(),), "update their own reports"),
        ("delete_report", tuple(), "delete their own reports"),
        ("start_report", tuple(), "start their own reports"),
        ("complete_report", tuple(), "complete their own reports"),
    ],
)
def test_technician_cannot_modify_another_technician_report(
    method_name: str,
    args: tuple[object, ...],
    message: str,
) -> None:
    service = _ReportService()
    session = MagicMock()
    current_user = _make_user(UserRole.TECHNICIAN)
    report = SimpleNamespace(id=uuid4(), technician_id=uuid4())

    service._get_technician_by_user = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(id=uuid4())
    )
    service._get_report = MagicMock(return_value=report)  # type: ignore[method-assign]

    method = getattr(service, method_name)

    with pytest.raises(ForbiddenException, match=message):
        method(report.id, *args, session, current_user)


def test_create_report_rejects_technician_creating_for_another_technician() -> None:
    service = _ReportService()
    session = MagicMock()
    current_user = _make_user(UserRole.TECHNICIAN)
    own_technician_id = uuid4()

    service._get_technician_by_user = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(id=own_technician_id)
    )

    payload = ReportCreate(
        report_type=ReportType.DIESEL,
        data={"summary": "test"},
        attachments=None,
        service_provider="Samo",
        seacom_ref="SEA-001",
        technician_id=uuid4(),
        task_id=uuid4(),
    )

    with pytest.raises(ForbiddenException, match="create reports for themselves"):
        service.create_report(payload, session, current_user)

    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "args", "message"),
    [
        ("create_report", tuple(), "create reports"),
        ("update_report", (ReportUpdate(),), "update reports"),
        ("delete_report", tuple(), "delete reports"),
        ("start_report", tuple(), "start reports"),
        ("complete_report", tuple(), "complete reports"),
    ],
)
def test_partner_cannot_mutate_reports(
    method_name: str,
    args: tuple[object, ...],
    message: str,
) -> None:
    service = _ReportService()
    session = MagicMock()
    current_user = _make_user(UserRole.PARTNER)
    report_id = uuid4()
    method = getattr(service, method_name)

    if method_name == "create_report":
        payload = ReportCreate(
            report_type=ReportType.DIESEL,
            data={"summary": "test"},
            attachments=None,
            service_provider="Samo",
            seacom_ref="SEA-001",
            technician_id=uuid4(),
            task_id=uuid4(),
        )
        call_args = (payload, session, current_user)
    else:
        call_args = (report_id, *args, session, current_user)

    with pytest.raises(ForbiddenException, match=message):
        method(*call_args)

    session.add.assert_not_called()
