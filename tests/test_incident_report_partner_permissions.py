from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.exceptions.http import ForbiddenException
from app.models.auth import TokenData
from app.models.incident_report import IncidentReportCreate, IncidentReportUpdate
from app.services.incident_report import _IncidentReportService
from app.utils.enums import UserRole


def _make_user(role: UserRole) -> TokenData:
    return TokenData(
        user_id=uuid4(),
        role=role,
        name="Partner",
        surname="User",
        token_type="access",
    )


def test_partner_can_read_incident_report() -> None:
    service = _IncidentReportService()
    session = MagicMock()
    current_user = _make_user(UserRole.PARTNER)
    report = SimpleNamespace(id=uuid4(), technician_id=uuid4())
    response = SimpleNamespace(id=report.id)

    service._get_report = MagicMock(return_value=report)  # type: ignore[method-assign]
    service._to_response = MagicMock(return_value=response)  # type: ignore[method-assign]

    assert service.read_incident_report(report.id, session, current_user) is response


def test_partner_can_list_incident_reports() -> None:
    service = _IncidentReportService()
    session = MagicMock()
    current_user = _make_user(UserRole.PARTNER)

    session.exec.return_value.all.return_value = []
    service._get_technician_by_user = MagicMock()  # type: ignore[method-assign]

    assert service.read_incident_reports(session, current_user) == []
    service._get_technician_by_user.assert_not_called()


def test_partner_can_export_incident_report_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _IncidentReportService()
    session = MagicMock()
    current_user = _make_user(UserRole.PARTNER)
    report = SimpleNamespace(
        id=uuid4(),
        incident_id=uuid4(),
        technician_id=uuid4(),
        report_date=None,
    )
    incident = SimpleNamespace(id=report.incident_id)
    pdf_service = SimpleNamespace(
        generate_incident_report_pdf=MagicMock(return_value=BytesIO(b"%PDF-1.4 test"))
    )

    service._get_report = MagicMock(return_value=report)  # type: ignore[method-assign]
    service._get_incident = MagicMock(return_value=incident)  # type: ignore[method-assign]
    monkeypatch.setattr("app.services.pdf.get_pdf_service", lambda: pdf_service)

    buffer, filename = service.export_report_pdf(report.id, session, current_user)

    assert buffer.getvalue() == b"%PDF-1.4 test"
    assert filename.startswith("Incident_Report_")


@pytest.mark.parametrize(
    ("method_name", "args", "message"),
    [
        ("create_incident_report", tuple(), "create incident reports"),
        ("update_incident_report", (IncidentReportUpdate(),), "update incident reports"),
        ("upload_report_photo", (b"image", "photo.jpg", "image/jpeg"), "upload photos"),
        ("delete_incident_report", tuple(), "Only admins"),
    ],
)
def test_partner_cannot_mutate_incident_reports(
    method_name: str,
    args: tuple[object, ...],
    message: str,
) -> None:
    service = _IncidentReportService()
    session = MagicMock()
    current_user = _make_user(UserRole.PARTNER)
    report_id = uuid4()
    method = getattr(service, method_name)

    if method_name == "create_incident_report":
        payload = IncidentReportCreate(
            incident_id=uuid4(),
            technician_id=uuid4(),
            site_name="Site A",
            technician_name="Tech User",
            introduction="Intro",
            problem_statement="Problem",
            findings="Findings",
            actions_taken="Actions",
            root_cause_analysis="Root cause",
            conclusion="Done",
        )
        call_args = (payload, session, current_user)
    elif method_name == "delete_incident_report":
        service._get_report = MagicMock(return_value=SimpleNamespace(id=report_id))  # type: ignore[method-assign]
        call_args = (report_id, session, current_user)
    elif method_name == "upload_report_photo":
        call_args = (report_id, *args, session, current_user)
    else:
        call_args = (report_id, *args, session, current_user)

    with pytest.raises(ForbiddenException, match=message):
        method(*call_args)

    session.add.assert_not_called()
