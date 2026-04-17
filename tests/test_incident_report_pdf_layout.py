import base64
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pdfplumber

from app.services.pdf import PDFService
from app.utils.enums import IncidentSeverity, IncidentStatus


def _sample_report():
    return SimpleNamespace(
        id=uuid4(),
        incident_id=uuid4(),
        site_name="Esperanza",
        report_date=datetime(2026, 4, 15, 7, 21, tzinfo=timezone.utc),
        technician_name="Ishmael Maumela",
        introduction="Low fuel Alarm",
        problem_statement="Low fuel Alarm",
        findings="Low fuel Alarm",
        actions_taken="Low fuel Alarm",
        root_cause_analysis="Low fuel Alarm",
        conclusion="Low fuel Alarm",
        attachments={"photos": []},
    )


def _sample_incident():
    return SimpleNamespace(
        seacom_ref="E8813862",
        ref_no="E8813862",
        severity=IncidentSeverity.MAJOR,
        status=IncidentStatus.RESOLVED,
        description="Low fuel Alarm",
    )


def test_incident_pdf_uses_incident_metadata_and_stays_two_pages() -> None:
    service = PDFService()

    pdf_buffer = service.generate_incident_report_pdf(_sample_report(), incident=_sample_incident())

    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        assert len(pdf.pages) == 2

        page1 = (pdf.pages[0].extract_text() or "").upper()
        page2 = (pdf.pages[1].extract_text() or "").upper()

    assert "E8813862" in page1
    assert "MAJOR" in page1
    assert "FIELD CORE" in page1
    assert "SAMO TELECOMS" not in page1
    assert "INCIDENT OVERVIEW" in page2
    assert "RESOLVED" in page2
    assert page2.count("LOW FUEL ALARM") >= 6


def test_incident_pdf_embeds_photo_images_when_attachments_exist() -> None:
    service = PDFService()
    report = _sample_report()
    report.attachments = {
        "photos": [
            {
                "url": "https://example.com/test-photo.png",
                "original_name": "test-photo.png",
            }
        ]
    }

    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
    )
    service._fetch_image_bytes = lambda url: BytesIO(png_bytes)  # type: ignore[method-assign]

    pdf_buffer = service.generate_incident_report_pdf(report, incident=_sample_incident())

    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        image_count = sum(len(page.images) for page in pdf.pages)
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

    assert "SITE PHOTOS" in extracted
    assert image_count >= 1
