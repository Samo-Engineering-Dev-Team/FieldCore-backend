from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.exceptions.http import ForbiddenException
from app.models import ReportCreate, ReportUpdate
from app.models.auth import TokenData
from app.services.report import _ReportService
from app.utils.enums import ReportType, UserRole


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
