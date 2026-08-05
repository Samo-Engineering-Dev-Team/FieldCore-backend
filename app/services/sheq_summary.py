"""
Denormalised compliance counters for a SHEQ submission (SHEQ-CHECKLISTS-PLAN.md
§8.4). Computed here, once, on every create/update in `sheq_submission.py`, and
stored on `SheqSubmission.summary` — so `sheq_compliance.py` reads indexed
columns and a shallow JSONB dict instead of scanning `data` across every row.

Pure and dependency-free, like `sheq_validation.py`.
"""

from typing import Any

from app.models.sheq_submission import (
    DAILY_RISK_MATRIX,
    MASTER_SAFETY_SECTIONS,
    VEHICLE_PRE_TRIP_LABELS,
)
from app.utils.enums import SheqChecklistType


def _row(data: dict[str, Any], *path: str) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _photo_count(attachments: dict[str, Any]) -> int:
    return sum(len(v) for v in attachments.values() if isinstance(v, list))


def _summary_vehicle_daily(data: dict[str, Any], attachments: dict[str, Any]) -> dict[str, Any]:
    failed: list[str] = []
    for row_key in VEHICLE_PRE_TRIP_LABELS:
        if _row(data, "pre_trip", row_key, "status") == "Fault":
            failed.append(f"pre_trip.{row_key}")
    if _row(data, "pre_trip", "vehicle_clean", "status") == "No":
        failed.append("pre_trip.vehicle_clean")
    if _row(data, "post_trip", "warning_lights_during_trip", "status") == "Yes":
        failed.append("post_trip.warning_lights_during_trip")
    if _row(data, "post_trip", "damage_or_fault_noted", "status") == "Yes":
        failed.append("post_trip.damage_or_fault_noted")

    return {
        "negative_count": len(failed),
        "no_go_count": 0,
        "failed_item_keys": failed,
        "sections_na": [],
        "photo_count": _photo_count(attachments),
        "overall_decision": None,
    }


def _summary_journey_management(data: dict[str, Any], attachments: dict[str, Any]) -> dict[str, Any]:
    failed = [
        key
        for key in ("exceeds_9hrs", "exceeds_12hrs_combined", "security_or_medical_risk")
        if data.get(key) == "Y"
    ]
    return {
        "negative_count": len(failed),
        "no_go_count": 0,
        "failed_item_keys": failed,
        "sections_na": [],
        "photo_count": _photo_count(attachments),
        "overall_decision": None,
    }


def _summary_daily_risk_assessment(data: dict[str, Any], attachments: dict[str, Any]) -> dict[str, Any]:
    matrix = data.get("checklist_matrix") or {}
    failed: list[str] = []
    for group_key, items in DAILY_RISK_MATRIX.items():
        group = matrix.get(group_key) or {}
        for item_key in items:
            entry = group.get(item_key) or {}
            if entry.get("answer") == "No":
                failed.append(f"{group_key}.{item_key}")

    return {
        "negative_count": len(failed),
        "no_go_count": 0,
        "failed_item_keys": failed,
        "sections_na": [],
        "photo_count": _photo_count(attachments),
        "overall_decision": None,
    }


def _summary_technician_master_safety(data: dict[str, Any], attachments: dict[str, Any]) -> dict[str, Any]:
    sections = data.get("sections") or {}
    failed: list[str] = []
    sections_na: list[str] = []

    for section_key, rows in MASTER_SAFETY_SECTIONS.items():
        section = sections.get(section_key) or {}
        if section.get("not_applicable"):
            sections_na.append(section_key)
            continue
        section_rows = section.get("rows") or {}
        for row_id in rows:
            row = section_rows.get(row_id) or {}
            if row.get("decision") == "No-Go":
                failed.append(f"{section_key}.{row_id}")

    return {
        "negative_count": len(failed),
        "no_go_count": len(failed),
        "failed_item_keys": failed,
        "sections_na": sections_na,
        "photo_count": _photo_count(attachments),
        "overall_decision": data.get("overall_decision"),
    }


_SUMMARIZERS = {
    SheqChecklistType.VEHICLE_DAILY: _summary_vehicle_daily,
    SheqChecklistType.JOURNEY_MANAGEMENT: _summary_journey_management,
    SheqChecklistType.DAILY_RISK_ASSESSMENT: _summary_daily_risk_assessment,
    SheqChecklistType.TECHNICIAN_MASTER_SAFETY: _summary_technician_master_safety,
}


def compute_sheq_summary(
    checklist_type: SheqChecklistType,
    data: dict[str, Any] | None,
    attachments: dict[str, Any] | None,
    signatures: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Recompute `SheqSubmission.summary` from `data`/`attachments`/`signatures`.

    Called on every create and update — never trust a stale stored summary.
    """
    summarizer = _SUMMARIZERS.get(checklist_type)
    base = (
        summarizer(data or {}, attachments or {})
        if summarizer
        else {
            "negative_count": 0,
            "no_go_count": 0,
            "failed_item_keys": [],
            "sections_na": [],
            "photo_count": _photo_count(attachments or {}),
            "overall_decision": None,
        }
    )
    roles = {str(role) for s in (signatures or []) if (role := s.get("role"))}
    base["signature_roles"] = sorted(roles)
    return base
