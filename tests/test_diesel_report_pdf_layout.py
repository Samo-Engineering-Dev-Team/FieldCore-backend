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
    assert "ATTESTATION" in extracted
    assert "ROUTE CLEAR." in extracted
    assert "URL:" not in extracted
    assert "CONTENT TYPE:" not in extracted
    assert "ORIGINAL NAME:" not in extracted
    assert "REPORT DETAILS" not in extracted
    # This report's only photo belongs to its one manhole, and now renders
    # inline with that manhole's checklist instead of in a separate
    # catch-all section — so no "Photo Evidence" heading, and no orphan
    # heading with nothing under it either.
    assert "PHOTO EVIDENCE" not in extracted
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


# ── Phase 4 regression guards (see REPORT_PDF_ISSUES.md / docs/report-schemas.md) ──


def _sample_diesel_report_with_amount():
    report = _sample_diesel_report()
    report.data["diesel_fillups"][0]["amount_used"] = 350.5
    report.data["diesel_fillups"].append(
        {
            "site_id": "site-1",
            "gen_no": 2,
            "liters_filled": 10,
            "amount_used": 149.5,
            "fill_reason": "Top-up",
            "gen_runtime_hours": "10.0",
        }
    )
    return report


def test_diesel_pdf_renders_amount_used() -> None:
    """Regression guard for issue #2: amount_used was captured but never rendered."""
    service = PDFService()
    report = _sample_diesel_report_with_amount()
    service._fetch_image_bytes = lambda url: BytesIO(  # type: ignore[method-assign]
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
        )
    )
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)
    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

    assert "AMOUNT (R)" in extracted
    assert "R 350.50" in extracted
    assert "R 149.50" in extracted
    assert "TOTAL SPEND" in extracted
    assert "R 500.00" in extracted  # 350.50 + 149.50


def test_diesel_pdf_renders_week_number_derived_from_report_date() -> None:
    """Week Number is derived from report.created_at, never captured by the technician."""
    service = PDFService()
    report = _sample_diesel_report()  # created_at = 2026-04-16 -> ISO week 16
    service._fetch_image_bytes = lambda url: BytesIO(  # type: ignore[method-assign]
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
        )
    )
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)
    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

    assert "WEEK NUMBER" in extracted
    assert "WEEK 16" in extracted


def test_format_iso_week_handles_missing_and_boundary_dates() -> None:
    service = PDFService()

    assert service._format_iso_week(None) == "N/A"
    # 1 Jan 2026 falls in ISO week 1 of 2026.
    assert service._format_iso_week(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "WEEK 1"
    # 31 Dec 2024 belongs to ISO week 1 of 2025, not week 53 of 2024.
    assert service._format_iso_week(datetime(2024, 12, 31, tzinfo=timezone.utc)) == "WEEK 1"


def _sample_repeater_report_legacy_mobile_schema():
    """Matches the pre-fix mobile payload: abbreviated keys, attachments.photos."""
    report = _sample_repeater_report()
    report.data = {
        "meta": {"routineType": "Weekly", "datePerformed": "2026-03-26", "nocTicket": "N/A"},
        "gen1": {},
        "gen2": {},
        "power": {},
        "siteObs": {"perimeterFenceGood": {"passed": True}},
        "container": {"wallsAndFloorClean": {"passed": True}},
        "riskAssessment": True,
        "env": {
            "temperature": "22",
            "cycleSetting": "Auto",
            "firePanelOk": True,
            "energizerFunctioning": True,
            "doorAlarmsTestedFront": True,
        },
        "concerns": "Loose cable tray noted near ODF.",
    }
    report.attachments = {
        "photos": [
            {
                "url": "https://example.com/site-photo.jpg",
                "original_name": "site-photo.jpg",
                "geo": {"lat": -26.0335279, "lon": 28.0764029, "address": None},
            }
        ]
    }
    return report


def test_repeater_pdf_falls_back_to_legacy_mobile_schema() -> None:
    """Regression guard for issue #1: mobile's pre-fix abbreviated keys must
    still render (already-submitted reports aren't migrated), and the photo
    array must never be dumped as a raw Python repr."""
    service = PDFService()
    report = _sample_repeater_report_legacy_mobile_schema()
    service._fetch_image_bytes = lambda url: BytesIO(  # type: ignore[method-assign]
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
        )
    )
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)
    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

    assert "4. SITE OBSERVATIONS" in extracted
    assert "PERIMETER FENCE IN GOOD CONDITION" in extracted
    assert "5. CONTAINER INTERIOR" in extracted
    assert "WALLS AND FLOOR CLEAN" in extracted
    assert "6. SAFETY OBSERVATIONS" in extracted
    assert "BASIC RISK ASSESSMENT PERFORMED" in extracted
    assert "7. ENVIRONMENTAL SYSTEMS" in extracted
    assert "FIRE PANEL OK" in extracted
    assert "8. SITE CONCERNS" in extracted
    assert "LOOSE CABLE TRAY NOTED NEAR ODF." in extracted
    assert "NO SITE OBSERVATIONS RECORDED" not in extracted
    assert "NO CONTAINER INTERIOR DATA RECORDED" not in extracted
    assert "NO SAFETY OBSERVATIONS RECORDED" not in extracted
    assert "NO SITE CONCERNS RECORDED" not in extracted
    # The original bug: str(value)[:60] on a list of dicts printed the
    # Python repr verbatim, e.g. "[{'geo': {'lat': -26.03...".
    assert "{'GEO'" not in extracted
    assert "'LAT'" not in extracted
    assert "'LON'" not in extracted
    # The generic fallback "Attachments" table (Field Name / Value columns)
    # must not run for REPEATER — it renders its own photos in section 9.
    assert "FIELD NAME" not in extracted


def _sample_routine_drive_report_full_manhole():
    report = _sample_routine_drive_report()
    report.data["anomalies_found"] = "true"  # some reports persist this as a string
    report.data["photos"]["bridge_culvert_checks"] = [
        {
            "id": "b1",
            "location": "Along N6",
            "coordinates": "-32.3578, 27.1841",
            "ground_movement": "No",
            "flood_damage": "No",
            "risk_to_network": "No",
            "mitigation": "Stone banks",
            "photos": [],
        }
    ]
    report.data["photos"]["manhole_inspections"] = [
        {
            "id": "m1",
            "manhole_id": "MH-01",
            "coordinates_recorded": "-26.033451, 28.076345",
            "lid_locked": "Yes",
            "ducts_sealed": "Yes",
            "lid_disturbed": "No",
            "can_be_unlocked": "Yes",
            "clean_no_debris": "Yes",
            "manhole_exposed": "No",
            "marker_in_place": "Yes",
            "chemical_threats": "No",
            "corrosion_splice": "No",
            "slack_management": "Yes",
            "disturbance_erosion": "No",
            "water_ingress_rodents": "No",
            "remarks": "Clean and locked.",
            "photos": [],
        }
    ]
    return report


def test_routine_drive_pdf_renders_full_manhole_checklist() -> None:
    """Regression guard for issue #3: the manhole table dropped 6 fields and
    joined the rest into an unlabeled "No | No | No" string."""
    service = PDFService()
    report = _sample_routine_drive_report_full_manhole()
    service._fetch_image_bytes = lambda url: BytesIO(  # type: ignore[method-assign]
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
        )
    )
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)
    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

    assert "1. MH-01" in extracted
    assert "CAN BE UNLOCKED" in extracted
    assert "DUCTS SEALED" in extracted
    assert "CLEAN / NO DEBRIS" in extracted
    assert "MARKER IN PLACE" in extracted
    assert "SLACK MANAGEMENT" in extracted
    assert "CORROSION / SPLICE" in extracted
    assert "DISTURBANCE / EROSION" in extracted
    assert "WATER INGRESS / RODENTS" in extracted
    assert "-26.033451, 28.076345" in extracted
    assert "-32.3578" in extracted  # bridge/culvert coordinates column (wraps in the narrow cell)
    assert "27.1841" in extracted
    assert "ANOMALIES FOUND" in extracted
    assert "YES" in extracted  # normalized from the string "true"


def test_routine_drive_pdf_places_manhole_photos_with_their_own_manhole() -> None:
    """Manhole photos used to all print together in a single catch-all
    "Photo Evidence" section at the end, disconnected from which manhole
    they belonged to. Each manhole's photos must render inline with that
    manhole's own checklist instead, with no duplicate fetch/embed."""
    service = PDFService()
    report = _sample_routine_drive_report_full_manhole()
    report.data["photos"]["manhole_inspections"][0]["photos"] = [
        {
            "path": "reports/routine/mh-01.jpg",
            "original_name": "mh-01.jpg",
            "content_type": "image/jpeg",
        }
    ]
    report.data["photos"]["manhole_inspections"].append(
        {
            **report.data["photos"]["manhole_inspections"][0],
            "id": "m2",
            "manhole_id": "MH-02",
            "coordinates_recorded": "-26.04, 28.08",
            "photos": [
                {
                    "path": "reports/routine/mh-02.jpg",
                    "original_name": "mh-02.jpg",
                    "content_type": "image/jpeg",
                }
            ],
        }
    )

    fetched: list[str] = []

    def fetch_image(url: str) -> BytesIO:
        fetched.append(url)
        return BytesIO(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
            )
        )

    service._fetch_image_bytes = fetch_image  # type: ignore[method-assign]
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)
    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()
        image_count = sum(len(page.images) for page in pdf.pages)

    assert "1. MH-01" in extracted
    assert "2. MH-02" in extracted
    # Both manhole photos fetched exactly once each — no duplicate embed
    # from also being picked up by the catch-all "Photo Evidence" pass.
    assert sorted(fetched) == ["reports/routine/mh-01.jpg", "reports/routine/mh-02.jpg"]
    assert image_count >= 2
    # Nothing left over for the catch-all section once both manholes'
    # photos render inline with their own checklists.
    assert "PHOTO EVIDENCE" not in extracted


def test_routine_drive_pdf_places_bridge_and_activity_photos_with_their_own_check() -> None:
    """Same fix, extended to Bridge/Culvert and Third-Party Activity checks:
    their photos used to land in the same catch-all "Photo Evidence" section
    as everything else, disconnected from which check they belonged to."""
    service = PDFService()
    report = _sample_routine_drive_report_full_manhole()
    report.data["photos"]["bridge_culvert_checks"][0]["photos"] = [
        {
            "path": "reports/routine/bridge-1.jpg",
            "original_name": "bridge-1.jpg",
            "content_type": "image/jpeg",
        }
    ]
    report.data["photos"]["activity_checks"] = [
        {
            "id": "a1",
            "location": "N6 Toll Gate",
            "coordinates": "-32.40, 27.20",
            "risk_to_network": "No",
            "mitigation": "N/A",
            "photos": [
                {
                    "path": "reports/routine/activity-1.jpg",
                    "original_name": "activity-1.jpg",
                    "content_type": "image/jpeg",
                }
            ],
        }
    ]

    fetched: list[str] = []

    def fetch_image(url: str) -> BytesIO:
        fetched.append(url)
        return BytesIO(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
            )
        )

    service._fetch_image_bytes = fetch_image  # type: ignore[method-assign]
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)
    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

    assert "BRIDGE / CULVERT CHECKS" in extracted
    assert "BRIDGE / CULVERT 1 - ALONG N6" in extracted
    assert "THIRD-PARTY ACTIVITY CHECKS" in extracted
    assert "ACTIVITY CHECK 1 - N6 TOLL GATE" in extracted
    assert sorted(fetched) == ["reports/routine/activity-1.jpg", "reports/routine/bridge-1.jpg"]
    # Nothing left over for the catch-all section — both checks' photos, and
    # the manhole's own (empty here), are fully accounted for inline.
    assert "PHOTO EVIDENCE" not in extracted


# ── Diesel site history ──────────────────────────────────────────────────────


def _history_entry(day: int, gen_no: int, liters: float, amount: float, **overrides):
    from app.models.report_data import DieselHistoryEntry
    from app.utils.funcs import format_iso_week

    fill_date = datetime(2026, 4, day, 8, 30, tzinfo=timezone.utc)
    payload = {
        "report_id": str(uuid4()),
        "fill_date": fill_date,
        "iso_week": format_iso_week(fill_date),
        "gen_no": gen_no,
        "liters_filled": liters,
        "amount_used": amount,
        "fill_reason": "Routine",
        "gen_runtime_hours": "5762H21M",
        "technician_name": "Musa Dlamini",
        "seacom_ref": "SEACOM-350289",
    }
    payload.update(overrides)
    return DieselHistoryEntry(**payload)


def _sample_site_history(*, include_gen2: bool = True):
    from app.models.report_data import DieselGeneratorHistory, DieselSiteHistory

    gen1_entries = [
        _history_entry(2, 1, 22.0, 500.0),
        _history_entry(9, 1, 0.0, 0.0, fill_reason="Not refueled"),
        _history_entry(16, 1, 18.5, 420.25),
    ]
    generators = [
        DieselGeneratorHistory(
            gen_no=1,
            entries=gen1_entries,
            entry_count=len(gen1_entries),
            total_liters=40.5,
            total_amount=920.25,
            highest_runtime_minutes=345741,
        )
    ]
    if include_gen2:
        gen2_entries = [_history_entry(16, 2, 30.0, 700.0)]
        generators.append(
            DieselGeneratorHistory(
                gen_no=2,
                entries=gen2_entries,
                entry_count=1,
                total_liters=30.0,
                total_amount=700.0,
                highest_runtime_minutes=345741,
            )
        )

    entries = [e for gen in generators for e in gen.entries]
    return DieselSiteHistory(
        site_id=str(uuid4()),
        site_name="Harrismith",
        first_fill_date=datetime(2026, 4, 2, 8, 30, tzinfo=timezone.utc),
        last_fill_date=datetime(2026, 4, 16, 8, 30, tzinfo=timezone.utc),
        generators=generators,
        entry_count=len(entries),
        total_liters=sum(e.liters_filled for e in entries),
        total_amount=sum(e.amount_used for e in entries),
    )


def _history_pdf_text(history) -> str:
    service = PDFService()
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]
    pdf_buffer = service.generate_diesel_history_pdf(history)
    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        return " ".join((page.extract_text() or "") for page in pdf.pages).upper()


def test_diesel_history_pdf_renders_a_section_per_generator() -> None:
    extracted = _history_pdf_text(_sample_site_history())

    assert "DIESEL USAGE HISTORY" in extracted
    assert "HARRISMITH" in extracted
    assert "GENERATOR 1 HISTORY" in extracted
    assert "GENERATOR 2 HISTORY" in extracted
    # Per-row date and week: the history spans reports, unlike the single-fill-up
    # report where every row shares one date.
    assert "02/04/2026 - 16/04/2026" in extracted  # overview range, full years
    assert "02/04/26 W14" in extracted
    assert "SUBTOTAL" in extracted


def test_diesel_history_pdf_omits_gen2_section_for_single_generator_site() -> None:
    extracted = _history_pdf_text(_sample_site_history(include_gen2=False))

    assert "GENERATOR 1 HISTORY" in extracted
    assert "GENERATOR 2 HISTORY" not in extracted


def test_diesel_history_pdf_handles_a_site_with_no_fillups() -> None:
    from app.models.report_data import DieselSiteHistory

    empty = DieselSiteHistory(site_id=str(uuid4()), site_name="Estcourt")
    extracted = _history_pdf_text(empty)

    assert "DIESEL USAGE HISTORY" in extracted
    assert "NO DIESEL FILL-UPS RECORDED FOR THIS SITE" in extracted
    assert "GENERATOR 1 HISTORY" not in extracted


def test_diesel_history_pdf_repeats_table_header_across_pages() -> None:
    from app.models.report_data import DieselGeneratorHistory, DieselSiteHistory

    entries = [_history_entry(2, 1, 20.0, 450.0) for _ in range(120)]
    history = DieselSiteHistory(
        site_id=str(uuid4()),
        site_name="Harrismith",
        generators=[
            DieselGeneratorHistory(
                gen_no=1,
                entries=entries,
                entry_count=len(entries),
                total_liters=2400.0,
                total_amount=54000.0,
            )
        ],
        entry_count=len(entries),
        total_liters=2400.0,
        total_amount=54000.0,
    )

    service = PDFService()
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]
    pdf_buffer = service.generate_diesel_history_pdf(history)

    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        pages = [(page.extract_text() or "").upper() for page in pdf.pages]

    assert len(pages) > 2
    # Every page carrying rows must carry the header too.
    row_pages = [p for p in pages if "SEACOM-350289" in p]
    assert len(row_pages) > 1
    assert all("SEACOM REF" in p for p in row_pages)


def test_diesel_history_pdf_survives_malformed_runtime_and_zero_fillups() -> None:
    """
    Runtime reaches the renderer uncoerced (str | float | None), and the real log
    is full of "N/A", lowercase "5770h19m", and blanks. Zero-litre "Not refueled"
    visits are the majority of rows and are deliberately kept — they are the
    compliance record, not noise.
    """
    from app.models.report_data import DieselGeneratorHistory, DieselSiteHistory

    entries = [
        _history_entry(2, 1, 0.0, 0.0, gen_runtime_hours="N/A", fill_reason="Not refueled"),
        _history_entry(9, 1, 0.0, 0.0, gen_runtime_hours="", fill_reason=None),
        _history_entry(16, 1, 22.51, 677.1, gen_runtime_hours="5770h19m"),
        _history_entry(16, 1, 18.0, 400.0, gen_runtime_hours=None, technician_name=None),
        _history_entry(16, 1, 1.0, 1.0, gen_runtime_hours=1234.2, seacom_ref=None),
    ]
    history = DieselSiteHistory(
        site_id=str(uuid4()),
        site_name="Estcourt",
        generators=[
            DieselGeneratorHistory(
                gen_no=1,
                entries=entries,
                entry_count=len(entries),
                total_liters=41.51,
                total_amount=1078.1,
                highest_runtime_minutes=None,
            )
        ],
        entry_count=len(entries),
        total_liters=41.51,
        total_amount=1078.1,
    )

    extracted = _history_pdf_text(history)

    assert "GENERATOR 1 HISTORY" in extracted
    # Zero-litre visits are kept, not filtered out.
    assert "NOT REFUELED" in extracted
    # Lowercase H/M notation still parses into the canonical display form.
    assert "5770H19M" in extracted
    # Missing values degrade to placeholders rather than raising.
    assert "NOT SPECIFIED" in extracted
    assert "N/A" in extracted


def test_repeater_pdf_prefers_the_registered_unit_over_the_typed_serial() -> None:
    """
    A report linked to a registered generator prints that unit's identity.

    The serial typed into the form may be months old or mis-keyed; the asset
    register is authoritative once the section is linked to a unit.
    """
    service = PDFService()
    report = _sample_repeater_report()
    report.gen1_generator = SimpleNamespace(
        name="East yard Cummins",
        model="Cummins C60D5",
        serial_no="REGISTERED-123",
    )

    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
    )
    service._fetch_image_bytes = lambda url: BytesIO(png_bytes)  # type: ignore[method-assign]
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)
    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

    assert "EAST YARD CUMMINS" in extracted
    assert "CUMMINS C60D5" in extracted
    assert "REGISTERED-123" in extracted


def test_repeater_pdf_falls_back_to_the_payload_serial_when_unlinked() -> None:
    """Every report recorded before the asset register existed has no link and
    must still render exactly as it always did."""
    service = PDFService()
    report = _sample_repeater_report()  # carries no gen*_generator attributes

    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
    )
    service._fetch_image_bytes = lambda url: BytesIO(png_bytes)  # type: ignore[method-assign]
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]

    pdf_buffer = service.generate_report_pdf(report)
    with pdfplumber.open(BytesIO(pdf_buffer.getvalue())) as pdf:
        extracted = " ".join((page.extract_text() or "") for page in pdf.pages).upper()

    assert "2. GENERATOR 1 INSPECTION" in extracted
    assert "EAST YARD CUMMINS" not in extracted
