"""
Server-side validation for SHEQ checklist submissions
(SHEQ-CHECKLISTS-PLAN.md §6). One pure function per checklist type, each
enforcing exactly the conditional rules documented in the plan — no more, no
less, so `test_sheq_validation.py` maps one test per bullet.

Pure and dependency-free (no DB, no FastAPI request object), mirroring
`form_validation.py`. All errors are collected (not first-fail) and raised as
a `FormValidationException` keyed by a dotted field path.
"""

from typing import Any

from app.exceptions.http import FormValidationException
from app.models.sheq_submission import (
    DAILY_RISK_MATRIX,
    MASTER_SAFETY_PHOTO_SLOTS,
    MASTER_SAFETY_SECTIONS,
    VEHICLE_PRE_TRIP_LABELS,
)
from app.utils.enums import SheqChecklistType, SheqSignatureRole


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _row(data: dict[str, Any], *path: str) -> Any:
    """Walk a dotted path through nested dicts; returns None if any hop is absent."""
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


# ── vehicle-daily (§6.1) ─────────────────────────────────────────────────────

def _validate_vehicle_daily(
    data: dict[str, Any],
    attachments: dict[str, Any],
    signatures: list[dict[str, Any]],
    errors: dict[str, list[str]],
) -> None:
    for row_key in VEHICLE_PRE_TRIP_LABELS:
        status = _row(data, "pre_trip", row_key, "status")
        remarks = _row(data, "pre_trip", row_key, "remarks")
        if status == "Fault" and _is_empty(remarks):
            errors.setdefault(f"pre_trip.{row_key}.remarks", []).append(
                "remarks are required when this item is marked Fault"
            )

    if _row(data, "pre_trip", "vehicle_clean", "status") == "No" and _is_empty(
        _row(data, "pre_trip", "vehicle_clean", "remarks")
    ):
        errors.setdefault("pre_trip.vehicle_clean.remarks", []).append(
            "remarks are required when the vehicle is not clean"
        )

    if _row(data, "post_trip", "warning_lights_during_trip", "status") == "Yes" and _is_empty(
        _row(data, "post_trip", "warning_lights_during_trip", "remarks")
    ):
        errors.setdefault("post_trip.warning_lights_during_trip.remarks", []).append(
            "remarks are required when a warning light was on during the trip"
        )

    if _row(data, "post_trip", "damage_or_fault_noted", "status") == "Yes":
        if _is_empty(_row(data, "post_trip", "damage_or_fault_noted", "remarks")):
            errors.setdefault("post_trip.damage_or_fault_noted.remarks", []).append(
                "remarks are required when damage or a fault was noted"
            )
        if not attachments.get("damage_evidence"):
            errors.setdefault("attachments.damage_evidence", []).append(
                "at least one photo is required when damage or a fault was noted"
            )

    odo_start = data.get("odometer_start")
    odo_end = data.get("odometer_end")
    if odo_start is not None and odo_end is not None:
        try:
            if float(odo_end) < float(odo_start):
                errors.setdefault("odometer_end", []).append(
                    "must be greater than or equal to odometer_start"
                )
        except (TypeError, ValueError):
            errors.setdefault("odometer_end", []).append("must be numeric")


# ── journey-management (§6.2) ────────────────────────────────────────────────


def _validate_journey_management(
    data: dict[str, Any],
    attachments: dict[str, Any],
    signatures: list[dict[str, Any]],
    errors: dict[str, list[str]],
) -> None:
    if data.get("exceeds_9hrs") == "Y" or data.get("exceeds_12hrs_combined") == "Y":
        if _is_empty(data.get("alternative_arrangements")):
            errors.setdefault("alternative_arrangements", []).append(
                "alternative travel arrangements or an overnight rest location "
                "are required when total hours or combined driving/working time "
                "exceed the threshold"
            )

    if data.get("security_or_medical_risk") == "Y" and _is_empty(
        data.get("additional_risk_reduction_measures")
    ):
        errors.setdefault("additional_risk_reduction_measures", []).append(
            "additional risk reduction measures are required when the journey "
            "involves significant security or medical-response risk"
        )

    supervisor_signed = any(
        s.get("role") == SheqSignatureRole.SUPERVISOR for s in signatures
    )
    email_ack = bool(data.get("email_ack_reference")) and bool(data.get("email_ack_at"))
    if supervisor_signed == email_ack:
        # Both present or both absent — exactly one is required.
        errors.setdefault("supervisor_authorisation", []).append(
            "supervisor authorisation requires exactly one of a drawn/typed "
            "signature or an email acknowledgement, not both and not neither"
        )


# ── daily-risk-assessment (§6.3) ─────────────────────────────────────────────


def _validate_daily_risk_assessment(
    data: dict[str, Any],
    attachments: dict[str, Any],
    signatures: list[dict[str, Any]],
    errors: dict[str, list[str]],
) -> None:
    hazards = data.get("hazards")
    if not isinstance(hazards, list) or len(hazards) < 1:
        errors.setdefault("hazards", []).append(
            "at least one on-site risk assessment row is required"
        )

    matrix = data.get("checklist_matrix") or {}
    for group_key, items in DAILY_RISK_MATRIX.items():
        group = matrix.get(group_key) or {}
        for item_key in items:
            entry = group.get(item_key) or {}
            if entry.get("answer") == "No" and _is_empty(entry.get("comments")):
                errors.setdefault(
                    f"checklist_matrix.{group_key}.{item_key}.comments", []
                ).append("comments are required when the answer is No")

    roster = data.get("roster")
    if not isinstance(roster, list) or len(roster) < 1:
        errors.setdefault("roster", []).append(
            "at least one personnel roster row is required"
        )
    else:
        signed_roster_indices = {
            s.get("roster_index")
            for s in signatures
            if s.get("role") == SheqSignatureRole.EMPLOYEE
        }
        for index, row in enumerate(roster):
            if not isinstance(row, dict) or _is_empty(row.get("employee_name")):
                continue
            if index not in signed_roster_indices:
                errors.setdefault(f"roster.{index}.signature", []).append(
                    "a signature is required for every named roster row"
                )


# ── technician-master-safety (§6.4) ──────────────────────────────────────────


def _validate_technician_master_safety(
    data: dict[str, Any],
    attachments: dict[str, Any],
    signatures: list[dict[str, Any]],
    errors: dict[str, list[str]],
) -> None:
    sections = data.get("sections") or {}
    any_no_go = False

    for section_key, rows in MASTER_SAFETY_SECTIONS.items():
        section = sections.get(section_key) or {}
        if section.get("not_applicable"):
            continue

        section_rows = section.get("rows") or {}
        for row_id in rows:
            row = section_rows.get(row_id) or {}
            decision = row.get("decision")
            if decision == "No-Go":
                any_no_go = True
                if _is_empty(row.get("comments")):
                    errors.setdefault(
                        f"sections.{section_key}.rows.{row_id}.comments", []
                    ).append("comments are required when this item is marked No-Go")

        photos = section.get("photos") or {}
        for slot_key in MASTER_SAFETY_PHOTO_SLOTS:
            slot_attachment_key = f"{section_key}.{slot_key}"
            has_photo = bool(attachments.get(slot_attachment_key)) or bool(
                photos.get(slot_key)
            )
            if not has_photo:
                errors.setdefault(
                    f"sections.{section_key}.photos.{slot_key}", []
                ).append("at least one photo is required for this applicable section")

    overall_decision = data.get("overall_decision")
    if overall_decision == "No-Go" and _is_empty(data.get("no_go_reason")):
        errors.setdefault("no_go_reason", []).append(
            "a reason is required when the overall decision is No-Go"
        )

    if any_no_go and overall_decision == "Go":
        errors.setdefault("overall_decision", []).append(
            "overall decision cannot be Go while at least one row is marked No-Go"
        )


_VALIDATORS = {
    SheqChecklistType.VEHICLE_DAILY: _validate_vehicle_daily,
    SheqChecklistType.JOURNEY_MANAGEMENT: _validate_journey_management,
    SheqChecklistType.DAILY_RISK_ASSESSMENT: _validate_daily_risk_assessment,
    SheqChecklistType.TECHNICIAN_MASTER_SAFETY: _validate_technician_master_safety,
}


def validate_sheq_submission(
    checklist_type: SheqChecklistType,
    data: dict[str, Any] | None,
    attachments: dict[str, Any] | None = None,
    signatures: list[dict[str, Any]] | None = None,
) -> None:
    """
    Validate a SHEQ submission's `data` against its checklist type's
    conditional rules (§6). Raises `FormValidationException` (HTTP 422) with a
    per-field error map on failure; returns None on success.
    """
    validator = _VALIDATORS.get(checklist_type)
    if validator is None:
        return

    errors: dict[str, list[str]] = {}
    validator(data or {}, attachments or {}, signatures or [], errors)

    if errors:
        raise FormValidationException(errors=errors)
