"""Every conditional rule in SHEQ-CHECKLISTS-PLAN.md §6, positive and negative."""

import pytest

from app.exceptions.http import FormValidationException
from app.services.sheq_validation import validate_sheq_submission
from app.utils.enums import SheqChecklistType, SheqSignatureRole


# ── vehicle-daily (§6.1) ─────────────────────────────────────────────────────


def test_vehicle_daily_fault_without_remarks_rejected():
    data = {"pre_trip": {"brakes": {"status": "Fault", "remarks": None}}}
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.VEHICLE_DAILY, data)
    assert "pre_trip.brakes.remarks" in exc.value.errors


def test_vehicle_daily_fault_with_remarks_accepted():
    data = {"pre_trip": {"brakes": {"status": "Fault", "remarks": "Worn pads"}}}
    validate_sheq_submission(SheqChecklistType.VEHICLE_DAILY, data)


def test_vehicle_daily_ok_row_never_requires_remarks():
    data = {"pre_trip": {"brakes": {"status": "OK", "remarks": None}}}
    validate_sheq_submission(SheqChecklistType.VEHICLE_DAILY, data)


def test_vehicle_clean_no_without_remarks_rejected():
    data = {"pre_trip": {"vehicle_clean": {"status": "No", "remarks": None}}}
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.VEHICLE_DAILY, data)
    assert "pre_trip.vehicle_clean.remarks" in exc.value.errors


def test_warning_lights_yes_without_remarks_rejected():
    data = {"post_trip": {"warning_lights_during_trip": {"status": "Yes", "remarks": None}}}
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.VEHICLE_DAILY, data)
    assert "post_trip.warning_lights_during_trip.remarks" in exc.value.errors


def test_damage_noted_requires_remarks_and_photo():
    data = {"post_trip": {"damage_or_fault_noted": {"status": "Yes", "remarks": None}}}
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.VEHICLE_DAILY, data, attachments={})
    assert "post_trip.damage_or_fault_noted.remarks" in exc.value.errors
    assert "attachments.damage_evidence" in exc.value.errors


def test_damage_noted_with_remarks_and_photo_accepted():
    data = {"post_trip": {"damage_or_fault_noted": {"status": "Yes", "remarks": "Dented bumper"}}}
    attachments = {"damage_evidence": [{"url": "https://example.com/x.png"}]}
    validate_sheq_submission(SheqChecklistType.VEHICLE_DAILY, data, attachments=attachments)


def test_odometer_end_below_start_rejected():
    data = {"odometer_start": 1000, "odometer_end": 900}
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.VEHICLE_DAILY, data)
    assert "odometer_end" in exc.value.errors


def test_odometer_end_at_or_above_start_accepted():
    validate_sheq_submission(
        SheqChecklistType.VEHICLE_DAILY, {"odometer_start": 1000, "odometer_end": 1000}
    )


# ── journey-management (§6.2) ────────────────────────────────────────────────


def test_exceeds_9hrs_requires_alternative_arrangements():
    data = {"exceeds_9hrs": "Y", "exceeds_12hrs_combined": "N", "security_or_medical_risk": "N"}
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.JOURNEY_MANAGEMENT, data)
    assert "alternative_arrangements" in exc.value.errors


def test_exceeds_12hrs_combined_requires_alternative_arrangements():
    data = {"exceeds_9hrs": "N", "exceeds_12hrs_combined": "Y", "security_or_medical_risk": "N"}
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.JOURNEY_MANAGEMENT, data)
    assert "alternative_arrangements" in exc.value.errors


def test_neither_exceeds_flag_skips_alternative_arrangements_requirement():
    data = {"exceeds_9hrs": "N", "exceeds_12hrs_combined": "N", "security_or_medical_risk": "N"}
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.JOURNEY_MANAGEMENT, data)
    assert "alternative_arrangements" not in exc.value.errors


def test_security_risk_requires_additional_measures():
    data = {
        "exceeds_9hrs": "N", "exceeds_12hrs_combined": "N", "security_or_medical_risk": "Y",
    }
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.JOURNEY_MANAGEMENT, data)
    assert "additional_risk_reduction_measures" in exc.value.errors


def test_supervisor_authorisation_neither_signature_nor_email_rejected():
    data = {"exceeds_9hrs": "N", "exceeds_12hrs_combined": "N", "security_or_medical_risk": "N"}
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.JOURNEY_MANAGEMENT, data, signatures=[])
    assert "supervisor_authorisation" in exc.value.errors


def test_supervisor_authorisation_both_signature_and_email_rejected():
    data = {
        "exceeds_9hrs": "N", "exceeds_12hrs_combined": "N", "security_or_medical_risk": "N",
        "email_ack_reference": "REF-1", "email_ack_at": "2026-08-04T08:00:00Z",
    }
    signatures = [{"role": SheqSignatureRole.SUPERVISOR}]
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.JOURNEY_MANAGEMENT, data, signatures=signatures)
    assert "supervisor_authorisation" in exc.value.errors


def test_supervisor_authorisation_signature_only_accepted():
    data = {"exceeds_9hrs": "N", "exceeds_12hrs_combined": "N", "security_or_medical_risk": "N"}
    signatures = [{"role": SheqSignatureRole.SUPERVISOR}]
    validate_sheq_submission(SheqChecklistType.JOURNEY_MANAGEMENT, data, signatures=signatures)


def test_supervisor_authorisation_email_only_accepted():
    data = {
        "exceeds_9hrs": "N", "exceeds_12hrs_combined": "N", "security_or_medical_risk": "N",
        "email_ack_reference": "REF-1", "email_ack_at": "2026-08-04T08:00:00Z",
    }
    validate_sheq_submission(SheqChecklistType.JOURNEY_MANAGEMENT, data, signatures=[])


def test_updated_jmp_required_has_no_validation_effect():
    data = {
        "exceeds_9hrs": "N", "exceeds_12hrs_combined": "N", "security_or_medical_risk": "N",
        "updated_jmp_required": "Yes",
    }
    signatures = [{"role": SheqSignatureRole.SUPERVISOR}]
    validate_sheq_submission(SheqChecklistType.JOURNEY_MANAGEMENT, data, signatures=signatures)


# ── daily-risk-assessment (§6.3) ─────────────────────────────────────────────


def test_matrix_no_answer_without_comments_rejected():
    data = {
        "hazards": [{"hazard": "x", "action_taken": "y", "toolbox_talk_discussed": True}],
        "checklist_matrix": {"hand_tools": {"tools_good_condition": {"answer": "No", "comments": None}}},
        "roster": [{"employee_name": "Thabo"}],
    }
    signatures = [{"role": "employee", "roster_index": 0}]
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.DAILY_RISK_ASSESSMENT, data, signatures=signatures)
    assert "checklist_matrix.hand_tools.tools_good_condition.comments" in exc.value.errors


def test_matrix_no_answer_with_comments_accepted():
    data = {
        "hazards": [{"hazard": "x", "action_taken": "y", "toolbox_talk_discussed": True}],
        "checklist_matrix": {
            "hand_tools": {"tools_good_condition": {"answer": "No", "comments": "Replaced tools"}}
        },
        "roster": [{"employee_name": "Thabo"}],
    }
    signatures = [{"role": "employee", "roster_index": 0}]
    validate_sheq_submission(SheqChecklistType.DAILY_RISK_ASSESSMENT, data, signatures=signatures)


def test_matrix_yes_answer_never_requires_comments():
    data = {
        "hazards": [{"hazard": "x", "action_taken": "y", "toolbox_talk_discussed": True}],
        "checklist_matrix": {"hand_tools": {"tools_good_condition": {"answer": "Yes", "comments": None}}},
        "roster": [{"employee_name": "Thabo"}],
    }
    signatures = [{"role": "employee", "roster_index": 0}]
    validate_sheq_submission(SheqChecklistType.DAILY_RISK_ASSESSMENT, data, signatures=signatures)


def test_named_roster_row_without_signature_rejected():
    data = {
        "hazards": [{"hazard": "x", "action_taken": "y", "toolbox_talk_discussed": True}],
        "checklist_matrix": {},
        "roster": [{"employee_name": "Thabo"}],
    }
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.DAILY_RISK_ASSESSMENT, data, signatures=[])
    assert "roster.0.signature" in exc.value.errors


def test_named_roster_row_with_signature_accepted():
    data = {
        "hazards": [{"hazard": "x", "action_taken": "y", "toolbox_talk_discussed": True}],
        "checklist_matrix": {},
        "roster": [{"employee_name": "Thabo"}],
    }
    signatures = [{"role": "employee", "roster_index": 0}]
    validate_sheq_submission(SheqChecklistType.DAILY_RISK_ASSESSMENT, data, signatures=signatures)


def test_empty_hazards_rejected():
    data = {"hazards": [], "checklist_matrix": {}, "roster": [{"employee_name": "Thabo"}]}
    signatures = [{"role": "employee", "roster_index": 0}]
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.DAILY_RISK_ASSESSMENT, data, signatures=signatures)
    assert "hazards" in exc.value.errors


def test_empty_roster_rejected():
    data = {
        "hazards": [{"hazard": "x", "action_taken": "y", "toolbox_talk_discussed": True}],
        "checklist_matrix": {},
        "roster": [],
    }
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.DAILY_RISK_ASSESSMENT, data)
    assert "roster" in exc.value.errors


# ── technician-master-safety (§6.4) ──────────────────────────────────────────


_OTHER_MASTER_SECTIONS = (
    "vehicle_safety", "confined_space_pest", "working_at_heights", "microwave_rf",
    "generator_servicing", "air_conditioning", "fibre_internet",
)


def _full_master_data(rows_override: dict | None = None, **overrides) -> dict:
    """`pre_job_safety` fully applicable and answered; every other section
    marked not_applicable — isolates assertions to the one section under test
    without needing photos/answers for all 53 rows across all 8 sections."""
    rows = {
        "1.1": {"decision": "Go", "comments": None},
        "1.2": {"decision": "Go", "comments": None},
        "1.3": {"decision": "Go", "comments": None},
        "1.4": {"decision": "Go", "comments": None},
        "1.5": {"decision": "Go", "comments": None},
        "1.6": {"decision": "Go", "comments": None},
    }
    if rows_override:
        rows.update(rows_override)
    data = {
        "sections": {
            "pre_job_safety": {"not_applicable": False, "rows": rows},
            **{key: {"not_applicable": True, "rows": {}} for key in _OTHER_MASTER_SECTIONS},
        },
        "overall_decision": "Go",
    }
    data.update(overrides)
    return data


_FULL_PHOTOS = {
    "pre_job_safety.work_area_setup": [{"url": "x"}],
    "pre_job_safety.equipment_condition": [{"url": "x"}],
    "pre_job_safety.hazards_identified": [{"url": "x"}],
    "pre_job_safety.controls_implemented": [{"url": "x"}],
}


def test_master_no_go_row_without_comments_rejected():
    data = _full_master_data({"1.1": {"decision": "No-Go", "comments": None}}, overall_decision="No-Go", no_go_reason="x")
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data, attachments=_FULL_PHOTOS)
    assert "sections.pre_job_safety.rows.1.1.comments" in exc.value.errors


def test_master_no_go_row_with_comments_accepted():
    data = _full_master_data(
        {"1.1": {"decision": "No-Go", "comments": "Permit missing"}},
        overall_decision="No-Go",
        no_go_reason="Permit missing",
    )
    validate_sheq_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data, attachments=_FULL_PHOTOS)


def test_master_overall_no_go_without_reason_rejected():
    data = _full_master_data(overall_decision="No-Go", no_go_reason=None)
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data, attachments=_FULL_PHOTOS)
    assert "no_go_reason" in exc.value.errors


def test_master_not_applicable_section_skips_rows_and_photos():
    data = {
        "sections": {
            "pre_job_safety": {"not_applicable": True, "rows": {}},
            **{key: {"not_applicable": True, "rows": {}} for key in _OTHER_MASTER_SECTIONS},
        },
        "overall_decision": "Go",
    }
    validate_sheq_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data, attachments={})


def test_master_applicable_section_missing_photo_rejected():
    data = _full_master_data()
    incomplete_photos = dict(_FULL_PHOTOS)
    del incomplete_photos["pre_job_safety.controls_implemented"]
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data, attachments=incomplete_photos)
    assert "sections.pre_job_safety.photos.controls_implemented" in exc.value.errors


def test_master_applicable_section_all_photos_present_accepted():
    data = _full_master_data()
    validate_sheq_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data, attachments=_FULL_PHOTOS)


def test_master_no_go_row_with_overall_go_hard_blocked():
    data = _full_master_data(
        {"1.1": {"decision": "No-Go", "comments": "Permit missing"}}, overall_decision="Go"
    )
    with pytest.raises(FormValidationException) as exc:
        validate_sheq_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data, attachments=_FULL_PHOTOS)
    assert "overall_decision" in exc.value.errors


def test_master_all_go_with_overall_go_accepted():
    data = _full_master_data()
    validate_sheq_submission(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data, attachments=_FULL_PHOTOS)
