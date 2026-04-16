from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from app.services.report import _ReportService
from app.utils.enums import ReportType


def make_report(
    report_type: ReportType,
    *,
    report_ref: str | None = None,
    task_ref: str | None = None,
):
    return SimpleNamespace(
        id=UUID("12345678-1234-5678-1234-567812345678"),
        report_type=report_type,
        seacom_ref=report_ref,
        task=SimpleNamespace(seacom_ref=task_ref) if task_ref is not None else None,
        created_at=datetime(2026, 4, 16, 10, 30, 0),
    )


def test_build_export_filename_uses_repeater_site_name_and_task_reference() -> None:
    service = _ReportService()
    report = make_report(ReportType.REPEATER, task_ref="REF 123/45")

    filename = service._build_export_filename(report)  # type: ignore[arg-type]

    assert filename == "Repeater-Site-Visit-REF-123-45.pdf"


def test_build_export_filename_prefers_report_reference_for_diesel() -> None:
    service = _ReportService()
    report = make_report(
        ReportType.DIESEL,
        report_ref="GEN-009",
        task_ref="TASK-001",
    )

    filename = service._build_export_filename(report)  # type: ignore[arg-type]

    assert filename == "Diesel-Report-GEN-009.pdf"


def test_build_export_filename_falls_back_when_reference_missing() -> None:
    service = _ReportService()
    report = make_report(ReportType.DIESEL)

    filename = service._build_export_filename(report)  # type: ignore[arg-type]

    assert filename == "Diesel-Report-20260416-12345678.pdf"
