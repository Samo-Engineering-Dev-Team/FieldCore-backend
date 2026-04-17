from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import app.services.pdf as pdf_module
import app.services.report as report_module

from app.models.auth import TokenData
from app.services.incident_report import _IncidentReportService
from app.services.report import _ReportService
from app.utils.enums import ReportStatus, ReportType, UserRole


class TrackingSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.refresh_calls: list[object] = []

    def commit(self) -> None:
        self.commit_calls += 1
        raise AssertionError("export should not commit")

    def refresh(self, obj: object) -> None:
        self.refresh_calls.append(obj)


def _make_user(role: UserRole) -> TokenData:
    return TokenData(
        user_id=uuid4(),
        role=role,
        name="Test",
        surname="User",
        token_type="access",
    )


def test_incident_export_pdf_is_download_only(monkeypatch) -> None:
    report = SimpleNamespace(
        id=uuid4(),
        incident_id=uuid4(),
        technician_id=uuid4(),
        report_date=datetime(2026, 4, 17, 18, 0, tzinfo=timezone.utc),
        attachments={
            "files": [
                {
                    "original_name": "legacy-export.pdf",
                    "path": "incident-reports/legacy-export.pdf",
                }
            ],
            "photos": [],
        },
    )
    original_attachments = deepcopy(report.attachments)
    incident = SimpleNamespace(ref_no="INC-001")
    session = TrackingSession()
    service = _IncidentReportService()

    class StubPDFService:
        def generate_incident_report_pdf(self, report_obj, incident=None):
            assert report_obj is report
            assert incident is not None
            return BytesIO(b"%PDF-1.4 incident export")

    def fail_upload(*args, **kwargs):
        raise AssertionError("storage upload should not be called during incident export")

    monkeypatch.setattr(service, "_get_report", lambda report_id, db: report)
    monkeypatch.setattr(service, "_get_incident", lambda incident_id, db: incident)
    monkeypatch.setattr(pdf_module, "get_pdf_service", lambda: StubPDFService())
    monkeypatch.setattr("app.services.file.FileService.upload_file_sync", fail_upload)

    pdf_buffer, filename = service.export_report_pdf(report.id, session, _make_user(UserRole.ADMIN))

    assert pdf_buffer.getvalue() == b"%PDF-1.4 incident export"
    assert filename == f"Incident_Report_20260417_{str(report.id)[:8]}.pdf"
    assert report.attachments == original_attachments
    assert session.commit_calls == 0
    assert session.refresh_calls == []


def test_report_export_pdf_is_download_only(monkeypatch) -> None:
    report = SimpleNamespace(
        id=uuid4(),
        status=ReportStatus.COMPLETED,
        report_type=ReportType.DIESEL,
        created_at=datetime(2026, 4, 17, 18, 0, tzinfo=timezone.utc),
        attachments={"files": [{"original_name": "legacy.pdf"}]},
    )
    original_attachments = deepcopy(report.attachments)
    session = TrackingSession()
    service = _ReportService()

    class StubPDFService:
        def generate_report_pdf(self, report_obj):
            assert report_obj is report
            return BytesIO(b"%PDF-1.4 field report")

    def fail_upload(*args, **kwargs):
        raise AssertionError("storage upload should not be called during report export")

    monkeypatch.setattr(service, "_get_report", lambda report_id, db: report)
    monkeypatch.setattr(report_module, "get_pdf_service", lambda: StubPDFService())
    monkeypatch.setattr("app.services.file.FileService.upload_file_sync", fail_upload)

    pdf_buffer, filename = service.export_report_pdf(report.id, session)

    assert pdf_buffer.getvalue() == b"%PDF-1.4 field report"
    assert filename == f"report_diesel_20260417_{str(report.id)[:8]}.pdf"
    assert report.attachments == original_attachments
    assert session.commit_calls == 0
    assert session.refresh_calls == [report]
