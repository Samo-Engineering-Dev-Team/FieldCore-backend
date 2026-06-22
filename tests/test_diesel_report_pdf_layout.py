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
            "routineType": "Weekly",
            "dateRoutinePerformed": "2026-03-26",
            "nocRoutineTicketReference": None,
            "powerSystems": {
                "upsA": {
                    "upsStatus": "Normal",
                    "batteryChargeStatus": "100",
                    "loadPercent": "25",
                    "runtime": "12:30",
                },
                "upsB": {
                    "upsStatus": "Normal",
                    "batteryChargeStatus": "98",
                    "loadPercent": "27",
                    "runtime": "11:45",
                },
                "rectA": {
                    "loadCurrent": "18.2",
                    "outputVoltage": "56",
                    "installedModules": "3",
                    "modulesOnLine": "3",
                    "batteryChargeStatus": "100",
                },
                "rectB": {
                    "loadCurrent": "34.5",
                    "outputVoltage": "56",
                    "installedModules": "3",
                    "modulesOnLine": "3",
                    "batteryChargeStatus": "100",
                },
            },
            "sitePictures": {
                "pictures": [],
                "categories": {
                    "siteViews": {
                        "remarks": "Front gate and fence visible, no damage noted.",
                        "pictures": ["https://example.com/repeater-site-view.png"],
                    }
                },
            },
            "gen1": {
                "oilLevelFull": True,
                "serialNumber": "Test123",
                "fuelLevelFull": True,
            },
            "gen2": {},
        },
        attachments={},
    )


def _sample_routine_drive_report():
    return SimpleNamespace(
        id=uuid4(),
        report_type=ReportType.ROUTINE_DRIVE,
        status=ReportStatus.COMPLETED,
        service_provider="SEACOM",
        seacom_ref="121212",
        created_at=datetime(2026, 6, 22, 5, 52, 54, tzinfo=timezone.utc),
        technician=SimpleNamespace(
            user=SimpleNamespace(name="John", surname="Tech"),
            phone="0661547228",
        ),
        task=SimpleNamespace(
            seacom_ref="121212",
            site_id="site-3",
            site=SimpleNamespace(
                id="site-3",
                name="IS Bree",
                region=SimpleNamespace(value="western-cape"),
            ),
        ),
        data={
            "source": "route_patrol",
            "route_segment": "IS Bree",
            "patrol_date": "2026-06-22T05:52:42Z",
            "weather_conditions": "Clear",
            "anomalies_found": False,
            "anomaly_details": "",
            "photos": {
                "form_version": "2.0",
                "noc_ticket": "121212",
                "technician_name": "John Tech",
                "trip_start_photos": [],
                "trip_end_photos": [],
                "bridge_culvert_checks": [],
                "activity_checks": [],
                "manhole_inspections": [
                    {
                        "id": "3e3902e-1c22-4f56-ad4b-d1b8a10d0893",
                        "manhole_id": "MH-01",
                        "coordinates_recorded": "-26.033451, 28.076345",
                        "lid_locked": "Yes",
                        "disturbance_erosion": "N/A",
                        "manhole_exposed": "N/A",
                        "lid_disturbed": "N/A",
                        "water_ingress_rodents": "N/A",
                        "chemical_threats": "N/A",
                        "remarks": "Clean and locked.",
                        "photos": [
                            {
                                "path": "reports/routine/photo_1.jpg",
                                "original_name": "photo_1.jpg",
                                "content_type": "image/jpeg",
                            }
                        ],
                    }
                ],
                "final_notes": "Route clear.",
            },
        },
        attachments={
            "files": [
                {
                    "path": "reports/routine/photo_1.jpg",
                    "original_name": "photo_1.jpg",
                    "content_type": "image/jpeg",
                }
            ]
        },
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


def test_routine_drive_pdf_uses_presentable_patrol_layout() -> None:
    service = PDFService()
    report = _sample_routine_drive_report()
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
    )
    fetched: list[str] = []

    def fetch_image(url: str) -> BytesIO:
        fetched.append(url)
        return BytesIO(png_bytes)

    service._fetch_image_bytes = fetch_image  # type: ignore[method-assign]
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)

    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()
        image_count = sum(len(page.images) for page in pdf.pages)

    assert "FIELD OPERATIONS REPORT" in extracted
    assert "ROUTINE DRIVE REPORT" in extracted
    assert "IS BREE" in extracted
    assert "PATROL SUMMARY" in extracted
    assert "MANHOLE INSPECTIONS" in extracted
    assert "PHOTO EVIDENCE" in extracted
    assert "ATTESTATION" in extracted
    assert "ROUTE CLEAR." in extracted
    assert "URL:" not in extracted
    assert "CONTENT TYPE:" not in extracted
    assert "ORIGINAL NAME:" not in extracted
    assert "REPORT DETAILS" not in extracted
    assert fetched == ["reports/routine/photo_1.jpg"]
    assert image_count >= 1


def test_repeater_pdf_uses_new_field_cover_and_header() -> None:
    service = PDFService()
    report = _sample_repeater_report()
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
    assert "REPEATER REPORT" in extracted
    assert "GLENCAIRN" in extracted
    assert "STATUS" in extracted
    assert "SERVICE PROVIDER" in extracted
    assert "TECHNICIAN" in extracted
    assert "SITE" in extracted
    assert "1. ROUTINE INFORMATION" in extracted
    assert "ROUTINE TYPE" in extracted
    assert "WEEKLY" in extracted
    assert "UPS DISPLAY PANEL READINGS" in extracted
    assert "UPS STATUS" in extracted
    assert "RECTIFIER DISPLAY PANEL READINGS" in extracted
    assert "RECTIFIER LOAD CURRENT" in extracted
    assert "18.2" in extracted
    assert "34.5" in extracted
    assert "FRONT GATE AND FENCE VISIBLE, NO DAMAGE NOTED." in extracted
    assert image_count >= 1
    assert "REPORT DETAILS" not in extracted
