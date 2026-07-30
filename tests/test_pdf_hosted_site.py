"""
PDF rendering for the Datacenter/POP hosted-site routine report body
(DC_POP_REPORTS_IMPLEMENTATION_PLAN.md §7).

Both fixtures are real transcriptions of the two source workbooks (see
tests/test_hosted_site_routine_schema.py) — rendering them is closer to the
plan's own acceptance test (§10.4: diff the generated PDF against the
original spreadsheet) than synthetic data would be. Assertion style follows
the existing repeater/diesel PDF layout tests.
"""

import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pdfplumber

from app.services.pdf import PDFService
from app.utils.enums import ReportStatus, ReportType

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def make_report(report_type: ReportType, data: dict):
    site_name = data["header"]["site_name"]
    return SimpleNamespace(
        id=uuid4(),
        report_type=report_type,
        status=ReportStatus.COMPLETED,
        service_provider="SEACOM",
        created_at=None,
        technician=SimpleNamespace(
            user=SimpleNamespace(name="Zola", surname="Momoza"), phone="0123456789"
        ),
        task=SimpleNamespace(
            seacom_ref=data["header"]["snoc_routine_ticket"],
            site_id="site-1",
            site=SimpleNamespace(
                id="site-1", name=site_name, region=SimpleNamespace(value="western-cape")
            ),
        ),
        data=data,
        attachments=None,
    )


def render(report_type: ReportType, data: dict) -> bytes:
    service = PDFService()
    service._fetch_image_bytes = lambda url: BytesIO(_PNG_BYTES)  # type: ignore[method-assign]
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]
    buffer = service.generate_report_pdf(make_report(report_type, data))
    return buffer.getvalue()


def extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return " ".join((page.extract_text() or "") for page in pdf.pages)


def test_dc_fixture_renders_without_exception() -> None:
    data = load_fixture("hosted_site_routine_dc.json")
    pdf_bytes = render(ReportType.DATACENTER, data)
    assert pdf_bytes.startswith(b"%PDF")


def test_pop_fixture_renders_without_exception() -> None:
    data = load_fixture("hosted_site_routine_pop.json")
    pdf_bytes = render(ReportType.POP, data)
    assert pdf_bytes.startswith(b"%PDF")


def test_dc_pdf_shows_datacenter_inspection_title_and_cabinet_count() -> None:
    data = load_fixture("hosted_site_routine_dc.json")
    extracted = extract_text(render(ReportType.DATACENTER, data)).upper()

    assert "DATACENTER INSPECTION" in extracted
    assert "1. DETAILS" in extracted
    assert "2. SITE CHECKLIST" in extracted
    assert "3. POWER READINGS" in extracted
    assert "4. CABINETS" in extracted
    assert "5. EXTRA SECTIONS" in extracted
    assert "6. OTHER ISSUES" in extracted
    assert "PE IS DC" in extracted
    assert "SEACOM-491040" in extracted
    for i in range(1, 7):
        assert f"CABINET {i}" in extracted
    assert "CABINET 7" not in extracted


def test_pop_pdf_shows_pop_inspection_title_and_cabinet_count() -> None:
    data = load_fixture("hosted_site_routine_pop.json")
    extracted = extract_text(render(ReportType.POP, data)).upper()

    assert "POP INSPECTION" in extracted
    assert "MTN TYGERBERG" in extracted
    assert "SEACOM-492717" in extracted
    for i in range(1, 16):
        assert f"CABINET {i}" in extracted
    assert "CABINET 16" not in extracted
    assert "SITE-BACK VIEW" in extracted


def test_cabinet_photo_labels_appear_inside_their_own_cabinet_block() -> None:
    # "Full PDU Image" / "Full Cabinet Image" must appear once per cabinet
    # that has photos, printed under that cabinet's own heading rather than
    # hoisted into a trailing gallery.
    data = load_fixture("hosted_site_routine_dc.json")
    extracted = extract_text(render(ReportType.DATACENTER, data)).upper()

    cabinet_count = len(data["cabinets"])
    assert extracted.count("FULL PDU IMAGE") == cabinet_count
    assert extracted.count("FULL CABINET IMAGE") == cabinet_count


def test_gated_power_block_with_status_na_suppresses_readings() -> None:
    # The DC fixture's UPS block is "n/a" — its reading labels must not appear,
    # while the rectifier block (status "yes") must show its readings.
    data = load_fixture("hosted_site_routine_dc.json")
    extracted = extract_text(render(ReportType.DATACENTER, data)).upper()

    assert "A OUTPUT VOLTAGE" in extracted
    assert "A USAGE/LOAD" not in extracted
    assert "A BATTERY CAPACITY" not in extracted


def test_no_raw_dict_or_list_repr_leaks_into_output() -> None:
    for name, report_type in (
        ("hosted_site_routine_dc.json", ReportType.DATACENTER),
        ("hosted_site_routine_pop.json", ReportType.POP),
    ):
        data = load_fixture(name)
        extracted = extract_text(render(report_type, data))
        assert "{'" not in extracted
        assert "{\"" not in extracted
        assert "[{" not in extracted


def test_alarm_note_appears_on_its_cabinet_check_table() -> None:
    data = load_fixture("hosted_site_routine_dc.json")
    extracted = extract_text(render(ReportType.DATACENTER, data)).upper()
    assert "ALARMS ON CONTROLLER MODULE" in extracted


def test_other_issues_narrative_renders() -> None:
    data = load_fixture("hosted_site_routine_dc.json")
    extracted = extract_text(render(ReportType.DATACENTER, data)).upper()
    assert "BOTH RECCTIFIERS HAS ALARMS" in extracted
