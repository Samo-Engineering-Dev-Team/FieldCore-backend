import base64
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pdfplumber

from app.services.pdf import PDFService
from app.utils.enums import ReportStatus, ReportType


def _sample_diesel_report():
    return SimpleNamespace(
        id=uuid4(),
        report_type=ReportType.DIESEL,
        status=ReportStatus.COMPLETED,
        service_provider="SEACOM",
        created_at=datetime(2026, 4, 16, 8, 30, tzinfo=timezone.utc),
        technician=SimpleNamespace(
            user=SimpleNamespace(name="Ishmael", surname="Maumela"),
            phone="+27123456789",
        ),
        task=SimpleNamespace(
            seacom_ref="RD-12345",
            site_id="site-1",
            site=SimpleNamespace(
                id="site-1",
                name="Esperanza",
                region=SimpleNamespace(value="eastern-cape"),
            ),
        ),
        data={
            "diesel_fillups": [
                {
                    "site_id": "site-1",
                    "gen_no": 1,
                    "liters_filled": 22,
                    "fill_reason": "Routine",
                    "gen_runtime_hours": "1234.2",
                }
            ]
        },
        attachments={
            "files": [
                {
                    "path": "reports/report-1/uploads/test-photo.png",
                    "original_name": "test-photo.png",
                    "content_type": "image/png",
                }
            ]
        },
    )


def _sample_repeater_report():
    return SimpleNamespace(
        id=uuid4(),
        report_type=ReportType.REPEATER,
        status=ReportStatus.COMPLETED,
        service_provider="SEACOM",
        created_at=datetime(2026, 3, 25, 7, 34, tzinfo=timezone.utc),
        technician=SimpleNamespace(
            user=SimpleNamespace(name="Ishmael", surname="Maumela"),
            phone="073 210 0882",
        ),
        task=SimpleNamespace(
            seacom_ref="Seacom-123456",
            site_id="site-2",
            site=SimpleNamespace(
                id="site-2",
                name="Glencairn",
                region=SimpleNamespace(value="eastern-cape"),
            ),
        ),
        data={
            "dateRoutinePerformed": "2026-03-26",
            "nocRoutineTicketReference": None,
            "gen1": {
                "oilLevelFull": True,
                "serialNumber": "Test123",
                "fuelLevelFull": True,
            },
            "gen2": {},
        },
        attachments={},
    )


def test_diesel_pdf_uses_new_field_layout_and_embeds_images() -> None:
    service = PDFService()
    report = _sample_diesel_report()

    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
    )
    service._fetch_image_bytes = lambda url: BytesIO(png_bytes)  # type: ignore[method-assign]
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)

    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()
        image_count = sum(len(page.images) for page in pdf.pages)

    assert "FIELD OPERATIONS REPORT" in extracted
    assert "FIELD CORE" in extracted
    assert "SAMO TELECOMS" not in extracted
    assert "1. DIESEL SUMMARY" in extracted
    assert "2. FILL-UP ENTRIES" in extracted
    assert "FILL ENTRIES" in extracted
    assert "TOTAL LITERS" in extracted
    assert "GENERATORS" in extracted
    assert "RUNTIME RECORDS" in extracted
    assert "PRIMARY SITE" in extracted
    assert "ESPERANZA" in extracted
    assert "GEN 1" in extracted
    assert "ROUTINE" in extracted
    assert "1234H12M" in extracted
    assert "UPLOADED ATTACHMENTS" in extracted
    assert "DIESEL FILLUP SUMMARY" not in extracted
    assert "FILLUP DETAILS" not in extracted
    assert "REPORT DETAILS" not in extracted
    assert image_count >= 1


def test_repeater_pdf_uses_new_field_cover_and_header() -> None:
    service = PDFService()
    report = _sample_repeater_report()
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)

    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

    assert "FIELD OPERATIONS REPORT" in extracted
    assert "FIELD CORE" in extracted
    assert "SAMO TELECOMS" not in extracted
    assert "REPEATER REPORT" in extracted
    assert "GLENCAIRN" in extracted
    assert "STATUS" in extracted
    assert "SERVICE PROVIDER" in extracted
    assert "TECHNICIAN" in extracted
    assert "SITE" in extracted
    assert "1. ROUTINE INFORMATION" in extracted
    assert "REPORT DETAILS" not in extracted
