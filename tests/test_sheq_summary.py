"""`summary` (§8.4) must never drift from `data`/`attachments`/`signatures` —
it is recomputed fresh every time, not incrementally patched."""

from app.services.sheq_summary import compute_sheq_summary
from app.utils.enums import SheqChecklistType


def test_vehicle_daily_counts_faults_and_photos():
    data = {
        "pre_trip": {
            "tyres": {"status": "Fault", "remarks": "x"},
            "brakes": {"status": "OK", "remarks": None},
            "vehicle_clean": {"status": "No", "remarks": "x"},
        },
        "post_trip": {
            "warning_lights_during_trip": {"status": "Yes", "remarks": "x"},
            "damage_or_fault_noted": {"status": "No", "remarks": None},
        },
    }
    attachments = {"damage_evidence": [{"url": "a"}, {"url": "b"}]}
    summary = compute_sheq_summary(SheqChecklistType.VEHICLE_DAILY, data, attachments, [])
    assert summary["negative_count"] == 3
    assert set(summary["failed_item_keys"]) == {
        "pre_trip.tyres", "pre_trip.vehicle_clean", "post_trip.warning_lights_during_trip",
    }
    assert summary["photo_count"] == 2
    assert summary["overall_decision"] is None


def test_daily_risk_assessment_counts_no_answers():
    data = {
        "checklist_matrix": {
            "hand_tools": {
                "tools_good_condition": {"answer": "No", "comments": "x"},
                "employees_knowledgeable": {"answer": "Yes", "comments": None},
            }
        }
    }
    summary = compute_sheq_summary(SheqChecklistType.DAILY_RISK_ASSESSMENT, data, {}, [])
    assert summary["negative_count"] == 1
    assert summary["failed_item_keys"] == ["hand_tools.tools_good_condition"]


def test_master_safety_tracks_no_go_and_na_sections():
    data = {
        "sections": {
            "pre_job_safety": {
                "not_applicable": False,
                "rows": {"1.1": {"decision": "No-Go", "comments": "x"}, "1.2": {"decision": "Go"}},
            },
            "microwave_rf": {"not_applicable": True, "rows": {}},
        },
        "overall_decision": "No-Go",
    }
    summary = compute_sheq_summary(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data, {}, [])
    assert summary["no_go_count"] == 1
    assert summary["negative_count"] == 1
    assert summary["failed_item_keys"] == ["pre_job_safety.1.1"]
    assert summary["sections_na"] == ["microwave_rf"]
    assert summary["overall_decision"] == "No-Go"


def test_master_safety_na_section_rows_never_counted_even_if_no_go():
    """A row inside an N/A section is irrelevant scaffolding, not a real
    failure — the summary must not count it even if it happens to carry a
    stale/leftover 'No-Go' value."""
    data = {
        "sections": {
            "microwave_rf": {
                "not_applicable": True,
                "rows": {"5.1": {"decision": "No-Go", "comments": "stale"}},
            },
        },
        "overall_decision": "Go",
    }
    summary = compute_sheq_summary(SheqChecklistType.TECHNICIAN_MASTER_SAFETY, data, {}, [])
    assert summary["no_go_count"] == 0
    assert summary["failed_item_keys"] == []


def test_signature_roles_deduplicated_and_sorted():
    signatures = [
        {"role": "technician"}, {"role": "employee"}, {"role": "employee"}, {"role": "supervisor"},
    ]
    summary = compute_sheq_summary(SheqChecklistType.VEHICLE_DAILY, {}, {}, signatures)
    assert summary["signature_roles"] == ["employee", "supervisor", "technician"]


def test_summary_recomputed_fresh_never_incremental():
    """Calling compute_sheq_summary twice with different data must not leak
    state between calls — each call is a pure function of its inputs."""
    data_a = {"pre_trip": {"tyres": {"status": "Fault", "remarks": "x"}}}
    data_b = {"pre_trip": {"tyres": {"status": "OK", "remarks": None}}}
    summary_a = compute_sheq_summary(SheqChecklistType.VEHICLE_DAILY, data_a, {}, [])
    summary_b = compute_sheq_summary(SheqChecklistType.VEHICLE_DAILY, data_b, {}, [])
    assert summary_a["negative_count"] == 1
    assert summary_b["negative_count"] == 0


def test_empty_data_produces_zeroed_summary():
    summary = compute_sheq_summary(SheqChecklistType.JOURNEY_MANAGEMENT, {}, {}, [])
    assert summary["negative_count"] == 0
    assert summary["failed_item_keys"] == []
    assert summary["photo_count"] == 0
    assert summary["signature_roles"] == []
