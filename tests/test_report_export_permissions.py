from io import BytesIO
from uuid import uuid4

import pytest

from app.api.v1.report import export_report_pdf
from app.exceptions.http import ForbiddenException
from app.models.auth import TokenData
from app.utils.enums import UserRole


class StubReportService:
    def __init__(self) -> None:
        self.called = False

    def export_report_pdf(self, report_id, session):
        self.called = True
        return BytesIO(b"%PDF-1.4 test"), f"report_{report_id}.pdf"


def make_user(role: UserRole) -> TokenData:
    return TokenData(
        user_id=uuid4(),
        role=role,
        name="Test",
        surname="User",
        token_type="access",
    )


def test_export_report_pdf_rejects_technician_with_forbidden() -> None:
    service = StubReportService()

    with pytest.raises(ForbiddenException):
        export_report_pdf(uuid4(), service, object(), make_user(UserRole.TECHNICIAN))

    assert service.called is False


def test_export_report_pdf_returns_pdf_for_manager() -> None:
    report_id = uuid4()
    service = StubReportService()

    response = export_report_pdf(report_id, service, object(), make_user(UserRole.MANAGER))

    assert service.called is True
    assert response.body == b"%PDF-1.4 test"
    assert response.media_type == "application/pdf"
    assert response.headers["Content-Length"] == str(len(response.body))
    assert response.headers["Content-Disposition"] == f"attachment; filename=report_{report_id}.pdf"
