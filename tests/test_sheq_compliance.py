"""Each §8.4 compliance metric against fixture rows — pure functions, no DB."""

from datetime import date, datetime, timezone
from uuid import uuid4

from app.services.sheq_compliance import (
    _missed_vehicle_checks,
    _no_go_stats,
    _overdue_signoffs,
    _section_na_frequency,
    _signature_gaps,
    _submission_volume,
    _top_failing_items,
)
from app.utils.enums import SheqChecklistType, SheqStatus


def _row(**overrides):
    base = {
        "id": uuid4(),
        "checklist_type": SheqChecklistType.VEHICLE_DAILY,
        "status": SheqStatus.SUBMITTED,
        "performed_on": date(2026, 8, 5),
        "technician_id": uuid4(),
        "technician_name": "Thabo Nkosi",
        "submitted_at": None,
        "summary": {},
    }
    base.update(overrides)
    return base


def test_submission_volume_counts_by_type_and_status():
    rows = [
        _row(checklist_type=SheqChecklistType.VEHICLE_DAILY, status=SheqStatus.SUBMITTED),
        _row(checklist_type=SheqChecklistType.VEHICLE_DAILY, status=SheqStatus.SUBMITTED),
        _row(checklist_type=SheqChecklistType.VEHICLE_DAILY, status=SheqStatus.SIGNED_OFF),
        _row(checklist_type=SheqChecklistType.DAILY_RISK_ASSESSMENT, status=SheqStatus.SUBMITTED),
    ]
    volume = _submission_volume(rows)
    counts = {(v.checklist_type, v.status): v.count for v in volume}
    assert counts[(SheqChecklistType.VEHICLE_DAILY, SheqStatus.SUBMITTED)] == 2
    assert counts[(SheqChecklistType.VEHICLE_DAILY, SheqStatus.SIGNED_OFF)] == 1
    assert counts[(SheqChecklistType.DAILY_RISK_ASSESSMENT, SheqStatus.SUBMITTED)] == 1


def test_no_go_rate_only_counts_master_checklist_type():
    rows = [
        _row(checklist_type=SheqChecklistType.TECHNICIAN_MASTER_SAFETY, summary={"overall_decision": "No-Go"}),
        _row(checklist_type=SheqChecklistType.TECHNICIAN_MASTER_SAFETY, summary={"overall_decision": "Go"}),
        _row(checklist_type=SheqChecklistType.VEHICLE_DAILY, summary={"overall_decision": "No-Go"}),  # ignored
    ]
    no_go, total, rate = _no_go_stats(rows)
    assert total == 2
    assert no_go == 1
    assert rate == 0.5


def test_no_go_rate_zero_total_does_not_divide_by_zero():
    no_go, total, rate = _no_go_stats([])
    assert total == 0
    assert no_go == 0
    assert rate == 0.0


def test_top_failing_items_ranked_with_verbatim_labels():
    rows = [
        _row(summary={"failed_item_keys": ["pre_job_safety.1.1", "vehicle_safety.2.4"]}),
        _row(summary={"failed_item_keys": ["pre_job_safety.1.1"]}),
    ]
    top = _top_failing_items(rows)
    assert top[0].key == "pre_job_safety.1.1"
    assert top[0].count == 2
    assert top[0].label == "Valid work permit obtained"


def test_top_failing_items_unknown_key_falls_back_to_key_itself():
    rows = [_row(summary={"failed_item_keys": ["some.unmapped.key"]})]
    top = _top_failing_items(rows)
    assert top[0].label == "some.unmapped.key"


def test_section_na_frequency_only_counts_master_checklist():
    rows = [
        _row(checklist_type=SheqChecklistType.TECHNICIAN_MASTER_SAFETY, summary={"sections_na": ["microwave_rf"]}),
        _row(checklist_type=SheqChecklistType.TECHNICIAN_MASTER_SAFETY, summary={"sections_na": ["microwave_rf", "working_at_heights"]}),
        _row(checklist_type=SheqChecklistType.VEHICLE_DAILY, summary={"sections_na": ["microwave_rf"]}),  # ignored
    ]
    freq = _section_na_frequency(rows)
    by_key = {f.section_key: f.count for f in freq}
    assert by_key["microwave_rf"] == 2
    assert by_key["working_at_heights"] == 1


def test_overdue_signoffs_flags_only_past_sla():
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    rows = [
        _row(
            checklist_type=SheqChecklistType.VEHICLE_DAILY,
            status=SheqStatus.SUBMITTED,
            submitted_at=datetime(2026, 8, 5, tzinfo=timezone.utc),  # 5 days ago, sla=3 -> overdue
        ),
        _row(
            checklist_type=SheqChecklistType.VEHICLE_DAILY,
            status=SheqStatus.SUBMITTED,
            submitted_at=datetime(2026, 8, 9, tzinfo=timezone.utc),  # 1 day ago -> not overdue
        ),
        _row(
            checklist_type=SheqChecklistType.VEHICLE_DAILY,
            status=SheqStatus.SIGNED_OFF,
            submitted_at=datetime(2026, 8, 1, tzinfo=timezone.utc),  # already signed off -> excluded
        ),
        _row(
            checklist_type=SheqChecklistType.JOURNEY_MANAGEMENT,  # not a sign-off-required type
            status=SheqStatus.SUBMITTED,
            submitted_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        ),
    ]
    overdue = _overdue_signoffs(rows, sla_days=3, now=now)
    assert len(overdue) == 1
    assert overdue[0].days_overdue == 2


def test_signature_gaps_flags_submitted_without_supervisor_role():
    rows = [
        _row(checklist_type=SheqChecklistType.VEHICLE_DAILY, status=SheqStatus.SUBMITTED, summary={"signature_roles": ["driver"]}),
        _row(checklist_type=SheqChecklistType.VEHICLE_DAILY, status=SheqStatus.SUBMITTED, summary={"signature_roles": ["driver", "supervisor"]}),
        _row(checklist_type=SheqChecklistType.VEHICLE_DAILY, status=SheqStatus.SIGNED_OFF, summary={"signature_roles": ["driver"]}),
        _row(checklist_type=SheqChecklistType.TECHNICIAN_MASTER_SAFETY, status=SheqStatus.SUBMITTED, summary={"signature_roles": ["technician"]}),  # not sign-off-required
    ]
    gaps = _signature_gaps(rows)
    assert len(gaps) == 1
    assert gaps[0].checklist_type == SheqChecklistType.VEHICLE_DAILY


# ── missed vehicle checks: the one metric flagged as a business-rule guess ──


def test_missed_vehicle_check_flags_a_task_day_with_no_submission():
    technician_id = uuid4()
    technicians = [{"id": technician_id, "name": "Thabo Nkosi"}]
    tasks = [{"technician_id": technician_id, "start_time": datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)}]
    missed = _missed_vehicle_checks(technicians, tasks, {}, date(2026, 8, 1), date(2026, 8, 7))
    assert len(missed) == 1
    assert missed[0].date == date(2026, 8, 5)


def test_missed_vehicle_check_not_flagged_when_submission_exists_that_day():
    technician_id = uuid4()
    technicians = [{"id": technician_id, "name": "Thabo Nkosi"}]
    tasks = [{"technician_id": technician_id, "start_time": datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)}]
    submitted = {technician_id: {date(2026, 8, 5)}}
    missed = _missed_vehicle_checks(technicians, tasks, submitted, date(2026, 8, 1), date(2026, 8, 7))
    assert missed == []


def test_missed_vehicle_check_weekend_boundary():
    """A task scheduled on a weekend still counts as an expected day — the
    default rule is task-driven, not weekday-driven, so a technician working
    Saturday who skips the check is still flagged."""
    technician_id = uuid4()
    technicians = [{"id": technician_id, "name": "Thabo Nkosi"}]
    saturday = date(2026, 8, 8)
    assert saturday.weekday() == 5  # Saturday
    tasks = [{"technician_id": technician_id, "start_time": datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)}]
    missed = _missed_vehicle_checks(technicians, tasks, {}, date(2026, 8, 1), date(2026, 8, 9))
    assert missed == [m for m in missed if m.date == saturday]
    assert len(missed) == 1


def test_technician_with_no_tasks_in_range_is_never_flagged():
    """No task in range means no expected day at all — the technician must
    not appear in the missed-checks list, not even with zero missed days."""
    technician_id = uuid4()
    technicians = [{"id": technician_id, "name": "Thabo Nkosi"}]
    missed = _missed_vehicle_checks(technicians, [], {}, date(2026, 8, 1), date(2026, 8, 7))
    assert missed == []


def test_task_outside_date_range_is_not_an_expected_day():
    technician_id = uuid4()
    technicians = [{"id": technician_id, "name": "Thabo Nkosi"}]
    tasks = [{"technician_id": technician_id, "start_time": datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)}]
    missed = _missed_vehicle_checks(technicians, tasks, {}, date(2026, 8, 1), date(2026, 8, 7))
    assert missed == []


def test_multiple_missed_days_for_same_technician_all_listed():
    technician_id = uuid4()
    technicians = [{"id": technician_id, "name": "Thabo Nkosi"}]
    tasks = [
        {"technician_id": technician_id, "start_time": datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)},
        {"technician_id": technician_id, "start_time": datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc)},
    ]
    missed = _missed_vehicle_checks(technicians, tasks, {}, date(2026, 8, 1), date(2026, 8, 7))
    assert sorted(m.date for m in missed) == [date(2026, 8, 3), date(2026, 8, 4)]
