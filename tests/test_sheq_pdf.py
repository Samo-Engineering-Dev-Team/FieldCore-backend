"""
PDF rendering for the 4 SHEQ safety checklists (SHEQ-CHECKLISTS-PLAN.md §1B).

Assertion style follows the existing DC/POP hosted-site PDF layout tests
(tests/test_pdf_hosted_site.py) — build a fake submission, render, extract
text, assert the verbatim labels and key data points appear.
"""

import base64
from datetime import date, datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pdfplumber

from app.services.pdf import PDFService
from app.utils.enums import SheqChecklistType, SheqStatus

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm7sAAAAASUVORK5CYII="
)


def make_submission(
    checklist_type: SheqChecklistType,
    data: dict,
    attachments: dict | None = None,
    signatures: list[dict] | None = None,
    status: SheqStatus = SheqStatus.SUBMITTED,
):
    return SimpleNamespace(
        id=uuid4(),
        checklist_type=checklist_type,
        status=status,
        performed_on=date(2026, 8, 5),
        created_at=datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc),
        technician=SimpleNamespace(user=SimpleNamespace(name="Zola", surname="Momoza")),
        data=data,
        attachments=attachments,
        signatures=signatures or [],
    )


def render(submission) -> bytes:
    service = PDFService()
    service._fetch_image_bytes = lambda url: BytesIO(_PNG_BYTES)  # type: ignore[method-assign]
    service._resolve_cover_image_path = lambda cover_key: None  # type: ignore[method-assign]
    buffer = service.generate_sheq_checklist_pdf(submission, site_name="ZACPTA004", task_ref="SEACOM-1")
    return buffer.getvalue()


def extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return " ".join((page.extract_text() or "") for page in pdf.pages)


def _drawn_signature(role: str, roster_index: int | None = None) -> dict:
    return {
        "role": role,
        "roster_index": roster_index,
        "method": "drawn",
        "file_ref": {"url": "https://example.com/sig.png"},
        "typed_name": None,
        "signer_user_id": str(uuid4()),
        "signer_name": "Thabo Nkosi",
        "signed_at": "2026-08-05T09:14:22Z",
        "captured_at": "2026-08-05T09:12:10Z",
        "offline_captured": False,
        "device": None,
        "ip_address": "10.0.0.1",
        "user_agent": "test",
        "data_hash": "sha256:abc",
    }


def _typed_signature(role: str, roster_index: int | None = None) -> dict:
    record = _drawn_signature(role, roster_index)
    record.update(method="typed", file_ref=None, typed_name="T. Nkosi")
    return record


# ── vehicle-daily ────────────────────────────────────────────────────────────


def test_vehicle_daily_renders_without_exception() -> None:
    data = {
        "company_name": "Samo Engineering",
        "driver_name": "Thabo Nkosi",
        "vehicle_registration": "CA 123-456",
        "odometer_start": 10000,
        "odometer_end": 10050,
        "pre_trip": {
            "tyres": {"status": "OK", "remarks": None},
            "brakes": {"status": "Fault", "remarks": "Rear pads worn"},
            "vehicle_clean": {"status": "Yes", "remarks": None},
        },
        "post_trip": {
            "warning_lights_during_trip": {"status": "No", "remarks": None},
            "damage_or_fault_noted": {"status": "No", "remarks": None},
            "fuel_used_refilled": {"liters": 20, "remarks": "Filled at depot"},
        },
        "parking": {"vehicle_parked_securely": {"status": "Yes", "remarks": None}},
    }
    submission = make_submission(
        SheqChecklistType.VEHICLE_DAILY,
        data,
        signatures=[_drawn_signature("driver")],
    )
    pdf_bytes = render(submission)
    assert pdf_bytes.startswith(b"%PDF")

    extracted = extract_text(pdf_bytes).upper()
    assert "COMPANY VEHICLE DAILY CHECKLIST" in extracted
    assert "TYRES (PRESSURE, TREAD, DAMAGE)" in extracted
    assert "BRAKES (FOOT & HAND)" in extracted
    assert "REAR PADS WORN" in extracted
    assert "DRIVER SIGNATURE" in extracted
    assert "NOT YET SIGNED" in extracted  # supervisor has not signed


# ── journey-management ───────────────────────────────────────────────────────


def test_journey_management_renders_with_typed_signature() -> None:
    data = {
        "full_name": "Thabo Nkosi",
        "vehicle_registration": "CA 123-456",
        "journey_from": "Cape Town",
        "journey_to": "George",
        "via_locations": "N2",
        "estimated_distance_km": 420,
        "estimated_driving_time_hrs": 4.5,
        "exceeds_9hrs": "N",
        "exceeds_12hrs_combined": "N",
        "security_or_medical_risk": "Y",
        "additional_risk_reduction_measures": "Travel in daylight hours only",
        "routes": [{"primary_route": "N2 via Swellendam", "rest_stops": "Swellendam"}],
        "updated_jmp_required": "No",
    }
    submission = make_submission(
        SheqChecklistType.JOURNEY_MANAGEMENT,
        data,
        signatures=[_typed_signature("supervisor"), _drawn_signature("driver")],
    )
    pdf_bytes = render(submission)
    extracted = extract_text(pdf_bytes).upper()

    assert "JOURNEY MANAGEMENT PLAN" in extracted
    assert "TRAVEL IN DAYLIGHT HOURS ONLY" in extracted
    assert "N2 VIA SWELLENDAM" in extracted
    assert "TYPED SIGNATURE" in extracted
    assert "T. NKOSI" in extracted


def test_journey_management_email_acknowledgement_path() -> None:
    data = {
        "full_name": "Thabo Nkosi",
        "exceeds_9hrs": "N",
        "exceeds_12hrs_combined": "N",
        "security_or_medical_risk": "N",
        "email_ack_reference": "EMAIL-REF-001",
        "email_ack_at": "2026-08-04T08:00:00Z",
    }
    submission = make_submission(SheqChecklistType.JOURNEY_MANAGEMENT, data)
    extracted = extract_text(render(submission)).upper()
    assert "EMAIL ACKNOWLEDGEMENT" in extracted
    assert "EMAIL-REF-001" in extracted


# ── daily-risk-assessment ────────────────────────────────────────────────────


def test_daily_risk_assessment_renders_matrix_and_roster() -> None:
    data = {
        "supervisor_name": "Sipho Dlamini",
        "site": "ZACPTA004",
        "task_to_be_done": "Fibre splicing",
        "hazards": [
            {"hazard": "Excavation nearby", "action_taken": "Barricaded", "toolbox_talk_discussed": True}
        ],
        "checklist_matrix": {
            "site_establishment": {
                "employees_on_site": {"answer": "Yes", "comments": None},
                "equipment_on_site": {"answer": "No", "comments": "Missing ladder"},
                "pre_inspection_for_safety": {"answer": "Yes", "comments": None},
            },
        },
        "roster": [{"employee_name": "Thabo Nkosi", "ppe": {"reflector_vest": True, "safety_shoes": True, "gloves": False}}],
    }
    submission = make_submission(
        SheqChecklistType.DAILY_RISK_ASSESSMENT,
        data,
        signatures=[_drawn_signature("employee", roster_index=0), _drawn_signature("supervisor")],
    )
    extracted = extract_text(render(submission)).upper()

    assert "DAILY RISK ASSESSMENT" in extracted
    assert "IS ALL EQUIPMENT ON SITE" in extracted
    assert "MISSING LADDER" in extracted
    assert "EXCAVATION NEARBY" in extracted
    assert "SUPERVISOR SIGNATURE" in extracted
    assert "I HEREBY ACKNOWLEDGE" in extracted


# ── technician-master-safety ─────────────────────────────────────────────────


def _master_safety_data(**overrides) -> dict:
    base = {
        "sections": {
            "pre_job_safety": {
                "not_applicable": False,
                "rows": {
                    "1.1": {"decision": "Go", "comments": None},
                    "1.2": {"decision": "Go", "comments": None},
                    "1.3": {"decision": "Go", "comments": None},
                    "1.4": {"decision": "Go", "comments": None},
                    "1.5": {"decision": "Go", "comments": None},
                    "1.6": {"decision": "Go", "comments": None},
                },
            },
        },
        "overall_decision": "Go",
    }
    base.update(overrides)
    return base


def test_master_safety_all_go_renders_photo_grid() -> None:
    data = _master_safety_data()
    attachments = {
        "pre_job_safety.work_area_setup": [{"url": "https://example.com/1.png"}],
        "pre_job_safety.equipment_condition": [{"url": "https://example.com/2.png"}],
        "pre_job_safety.hazards_identified": [{"url": "https://example.com/3.png"}],
        "pre_job_safety.controls_implemented": [{"url": "https://example.com/4.png"}],
    }
    submission = make_submission(
        SheqChecklistType.TECHNICIAN_MASTER_SAFETY,
        data,
        attachments=attachments,
        signatures=[_drawn_signature("technician")],
    )
    extracted = extract_text(render(submission)).upper()

    assert "TECHNICIAN MASTER SAFETY" in extracted
    assert "PRE-JOB SAFETY & ADMINISTRATION" in extracted
    assert "VALID WORK PERMIT OBTAINED" in extracted
    assert "FINAL GO / NO-GO DECISION" in extracted


def test_master_safety_no_go_row_shows_comment() -> None:
    data = _master_safety_data(
        sections={
            "pre_job_safety": {
                "not_applicable": False,
                "rows": {
                    "1.1": {"decision": "No-Go", "comments": "Permit expired"},
                    "1.2": {"decision": "Go", "comments": None},
                    "1.3": {"decision": "Go", "comments": None},
                    "1.4": {"decision": "Go", "comments": None},
                    "1.5": {"decision": "Go", "comments": None},
                    "1.6": {"decision": "Go", "comments": None},
                },
            }
        },
        overall_decision="No-Go",
        no_go_reason="Work permit expired on arrival",
    )
    submission = make_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data)
    extracted = extract_text(render(submission)).upper()
    assert "PERMIT EXPIRED" in extracted
    assert "WORK PERMIT EXPIRED ON ARRIVAL" in extracted


def test_master_safety_all_sections_na_renders_stamp_for_every_section() -> None:
    sections = {
        key: {"not_applicable": True, "rows": {}}
        for key in (
            "pre_job_safety", "vehicle_safety", "confined_space_pest", "working_at_heights",
            "microwave_rf", "generator_servicing", "air_conditioning", "fibre_internet",
        )
    }
    data = {"sections": sections, "overall_decision": "Go"}
    submission = make_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data)
    extracted = extract_text(render(submission)).upper()

    assert extracted.count("N/A — NOT APPLICABLE") == 8


def test_no_raw_dict_or_list_repr_leaks_into_output() -> None:
    data = _master_safety_data()
    submission = make_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data)
    extracted = extract_text(render(submission))
    assert "{'" not in extracted
    assert "{\"" not in extracted
    assert "[{" not in extracted
