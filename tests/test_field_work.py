from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.exceptions.http import ForbiddenException
from app.models import (
    MaintenanceSchedule,
    MaintenanceScheduleCoverage,
    Site,
    Technician,
    TechnicianSite,
)
from app.models.auth import TokenData
from app.services.field_work import _FieldWorkService, create_completed_field_report
from app.services.maintenance_schedule import get_maintenance_schedule_service
from app.utils.enums import Region, ReportStatus, ReportType, SiteType, TaskStatus, UserRole


class QueryResult:
    def __init__(self, first=None, all_items=None) -> None:
        self._first = first
        self._all_items = all_items

    def first(self):
        return self._first

    def all(self):
        if self._all_items is not None:
            return self._all_items
        return [self._first] if self._first is not None else []


class FakeSession:
    def __init__(
        self,
        first=None,
        all_items=None,
        exec_results=None,
        get_results=None,
    ) -> None:
        self.added = []
        self._first = first
        self._all_items = all_items
        self._exec_results = list(exec_results or [])
        self._get_results = get_results or {}

    def exec(self, statement):
        if self._exec_results:
            return self._exec_results.pop(0)
        return QueryResult(first=self._first, all_items=self._all_items)

    def add(self, item) -> None:
        self.added.append(item)

    def flush(self) -> None:
        return None

    def get(self, model, id_):
        return self._get_results.get((model, id_))

    def commit(self) -> None:
        return None


def make_user(role: UserRole) -> TokenData:
    return TokenData(
        user_id=uuid4(),
        role=role,
        name="Test",
        surname="User",
        token_type="access",
    )


def test_create_completed_field_report_creates_task_report_and_marks_schedule() -> None:
    technician_id = uuid4()
    site_id = uuid4()
    completed_at = datetime(2026, 6, 21, 10, 0, tzinfo=timezone.utc)
    technician = Technician(
        id=technician_id,
        phone="0123456789",
        id_no="9001010000000",
        user_id=uuid4(),
    )
    site = Site(id=site_id, name="Repeater A", region=Region.GAUTENG)
    schedule = MaintenanceSchedule(
        site_id=site_id,
        schedule_type="repeater_site_visit",
        frequency="monthly",
        assigned_technician_id=technician_id,
        next_due_at=completed_at,
    )
    session = FakeSession(
        exec_results=[
            QueryResult(all_items=[schedule]),
            QueryResult(first=None),
        ]
    )

    task, report = create_completed_field_report(
        session=session,
        technician=technician,
        site=site,
        report_type=ReportType.REPEATER,
        seacom_ref="SEA-123",
        performed_at=completed_at,
        data={"notes": "Done"},
        attachments={"files": []},
    )

    assert task.status == TaskStatus.COMPLETED
    assert task.report_type == "repeater"
    assert task.seacom_ref == "SEA-123"
    assert report.status == ReportStatus.COMPLETED
    assert report.report_type == ReportType.REPEATER
    assert report.task_id == task.id
    assert schedule.last_run_at == completed_at
    assert schedule.next_due_at > completed_at


def test_field_work_submit_requires_technician_role() -> None:
    service = _FieldWorkService()

    with pytest.raises(ForbiddenException, match="Only technicians"):
        service.submit(
            payload=object(),
            report_type=ReportType.DIESEL,
            session=FakeSession(),
            current_user=make_user(UserRole.NOC),
        )


def test_covered_schedule_moves_from_original_to_replacement() -> None:
    original_tech_id = uuid4()
    replacement_tech_id = uuid4()
    site_id = uuid4()
    assigned_by = uuid4()
    now = datetime(2026, 6, 21, 10, 0, tzinfo=timezone.utc)
    schedule = MaintenanceSchedule(
        site_id=site_id,
        schedule_type="generator_diesel_refill",
        frequency="weekly",
        assigned_technician_id=original_tech_id,
        next_due_at=now,
    )
    coverage = MaintenanceScheduleCoverage(
        schedule_id=schedule.id,
        week_start_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        week_end_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
        original_technician_id=original_tech_id,
        assigned_technician_id=replacement_tech_id,
        assigned_by_user_id=assigned_by,
    )
    service = get_maintenance_schedule_service()

    original_view = service.list_all(
        FakeSession(
            exec_results=[
                QueryResult(all_items=[]),
                QueryResult(all_items=[schedule]),
                QueryResult(first=coverage),
            ]
        ),
        technician_id=original_tech_id,
    )
    replacement_view = service.list_all(
        FakeSession(
            exec_results=[
                QueryResult(all_items=[]),
                QueryResult(all_items=[schedule]),
                QueryResult(first=coverage),
            ]
        ),
        technician_id=replacement_tech_id,
    )

    assert original_view == []
    assert len(replacement_view) == 1
    assert replacement_view[0].effective_technician_id == replacement_tech_id
    assert replacement_view[0].coverage_id == coverage.id


def test_assigned_site_creates_weekly_due_diligence_schedules() -> None:
    technician_id = uuid4()
    site_id = uuid4()
    assignment = TechnicianSite(technician_id=technician_id, site_id=site_id)
    site = Site(
        id=site_id,
        name="Repeater A",
        region=Region.GAUTENG,
        site_type=SiteType.REPEATER,
    )
    session = FakeSession(
        exec_results=[
            QueryResult(all_items=[assignment]),
            QueryResult(all_items=[]),
            QueryResult(first=None),
            QueryResult(first=None),
        ],
        get_results={(Site, site_id): site},
    )

    get_maintenance_schedule_service().ensure_weekly_due_diligence_schedules(
        session, technician_id
    )

    created = [item for item in session.added if isinstance(item, MaintenanceSchedule)]
    assert {schedule.schedule_type for schedule in created} == {
        "repeater_site_visit",
        "generator_diesel_refill",
    }
    assert all(schedule.frequency == "weekly" for schedule in created)
    assert all(schedule.assigned_technician_id == technician_id for schedule in created)


def test_task_site_does_not_create_weekly_due_diligence_schedules() -> None:
    technician_id = uuid4()
    site_id = uuid4()
    assignment = TechnicianSite(technician_id=technician_id, site_id=site_id)
    site = Site(
        id=site_id,
        name="RHS Client Site",
        region=Region.GAUTENG,
        site_type=SiteType.TASK_SITE,
    )
    session = FakeSession(
        exec_results=[
            QueryResult(all_items=[assignment]),
            QueryResult(all_items=[]),
        ],
        get_results={(Site, site_id): site},
    )

    get_maintenance_schedule_service().ensure_weekly_due_diligence_schedules(
        session, technician_id
    )

    assert not any(isinstance(item, MaintenanceSchedule) for item in session.added)


def test_replacement_completion_marks_schedule_and_coverage_done() -> None:
    original_tech_id = uuid4()
    replacement_tech_id = uuid4()
    site_id = uuid4()
    assigned_by = uuid4()
    completed_at = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    schedule = MaintenanceSchedule(
        site_id=site_id,
        schedule_type="repeater_site_visit",
        frequency="weekly",
        assigned_technician_id=original_tech_id,
        next_due_at=completed_at,
    )
    coverage = MaintenanceScheduleCoverage(
        schedule_id=schedule.id,
        week_start_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        week_end_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
        original_technician_id=original_tech_id,
        assigned_technician_id=replacement_tech_id,
        assigned_by_user_id=assigned_by,
    )
    session = FakeSession(
        exec_results=[
            QueryResult(all_items=[schedule]),
            QueryResult(first=coverage),
        ]
    )

    matched = get_maintenance_schedule_service().mark_schedule_done_for_field_work(
        session=session,
        technician_id=replacement_tech_id,
        site_id=site_id,
        schedule_type="repeater_site_visit",
        completed_at=completed_at,
    )

    assert matched == schedule
    assert schedule.last_run_at == completed_at
    assert schedule.next_due_at > completed_at
    assert coverage.completed_at == completed_at
