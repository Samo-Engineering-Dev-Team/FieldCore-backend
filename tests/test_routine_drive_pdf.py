from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pdfplumber

from app.services.pdf import PDFService
from app.utils.enums import Region, ReportStatus, ReportType


def _extract_pdf_text(buffer: BytesIO) -> str:
    buffer.seek(0)
    with pdfplumber.open(buffer) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _count_pdf_images(buffer: BytesIO) -> int:
    buffer.seek(0)
    with pdfplumber.open(buffer) as pdf:
        return sum(len(page.images or []) for page in pdf.pages)


_PNG_BYTES = (Path(__file__).resolve().parents[1] / "app" / "assets" / "seacom-logo.png").read_bytes()


def test_generate_report_pdf_tabulates_routine_drive_answers() -> None:
    service = PDFService()
    report = SimpleNamespace(
        id=UUID("12345678-1234-5678-1234-567812345678"),
        report_type=ReportType.ROUTINE_DRIVE,
        status=ReportStatus.COMPLETED,
        service_provider="SEACOM",
        created_at=datetime(2026, 4, 16, 9, 15, 0),
        attachments=None,
        data={
            "route_segment": "JHB North Loop",
            "patrol_date": "2026-04-16",
            "weather_conditions": "Clear",
            "anomalies_found": True,
            "anomaly_details": "Vegetation encroachment at KM 4",
            "safety_checks": {
                "ppe_worn": True,
                "vehicle_roadworthy": True,
            },
            "checkpoint_entries": [
                {"km_mark": "4", "condition": "Vegetation", "action_taken": "Flagged"},
                {"km_mark": "8", "condition": "Clear", "action_taken": "No action"},
            ],
        },
        technician=SimpleNamespace(
            user=SimpleNamespace(name="Alex", surname="Moyo"),
            phone="0123456789",
        ),
        task=SimpleNamespace(
            seacom_ref="RD-12345",
            site=SimpleNamespace(name="JHB Hub", region=Region.GAUTENG),
        ),
    )

    pdf_buffer = service.generate_report_pdf(report)  # type: ignore[arg-type]
    pdf_text = _extract_pdf_text(pdf_buffer)

    assert "Recorded Answers" in pdf_text
    assert "Question" in pdf_text
    assert "Answer" in pdf_text
    assert "Route Segment" in pdf_text
    assert "JHB North Loop" in pdf_text
    assert "Checkpoint Entries" in pdf_text
    assert "Vegetation encroachment at KM 4" in pdf_text


def test_generate_report_pdf_renders_attachment_photos() -> None:
    service = PDFService()
    service._fetch_image_bytes = lambda url: BytesIO(_PNG_BYTES)  # type: ignore[method-assign]

    report = SimpleNamespace(
        id=UUID("12345678-1234-5678-1234-567812345678"),
        report_type=ReportType.DIESEL,
        status=ReportStatus.COMPLETED,
        service_provider="SEACOM",
        created_at=datetime(2026, 4, 16, 9, 15, 0),
        attachments={
            "files": [
                {
                    "path": "reports/test/photo-1.png",
                    "original_name": "photo-1.png",
                    "content_type": "image/png",
                }
            ]
        },
        data={
            "diesel_fillups": [
                {
                    "gen_no": 1,
                    "liters_filled": 22,
                    "gen_runtime_hours": "12H30M",
                    "fill_reason": "Routine",
                }
            ]
        },
        technician=SimpleNamespace(
            user=SimpleNamespace(name="Alex", surname="Moyo"),
            phone="0123456789",
        ),
        task=SimpleNamespace(
            seacom_ref="RD-12345",
            site=SimpleNamespace(name="JHB Hub", region=Region.GAUTENG),
        ),
    )

    pdf_buffer = service.generate_report_pdf(report)  # type: ignore[arg-type]
    pdf_text = _extract_pdf_text(pdf_buffer)

    assert "Uploaded Photos" in pdf_text
    assert "photo-1.png" in pdf_text
    assert _count_pdf_images(pdf_buffer) >= 5
