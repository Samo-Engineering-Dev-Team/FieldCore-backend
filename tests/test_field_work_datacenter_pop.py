"""
Field-work start/submit for the two new hosted-site routine report types.

Datacenter and POP reuse the exact same `_FieldWorkService.start`/`.submit`
and `create_started_field_report`/`create_completed_field_report` helpers as
repeater/diesel (DC_POP_REPORTS_IMPLEMENTATION_PLAN.md §6.1) — only the two
module-level maps (`REPORT_TO_SCHEDULE_TYPE`, `REPORT_TO_DESCRIPTION`) gained
entries. These tests pin that wiring rather than re-testing the shared
helpers (already covered by test_field_work.py).
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.exceptions.http import ForbiddenException
from app.models import MaintenanceSchedule, Site, Technician
from app.models.auth import TokenData
from app.services.field_work import (
    REPORT_TO_DESCRIPTION,
    REPORT_TO_SCHEDULE_TYPE,
    _FieldWorkService,
    create_completed_field_report,
    create_started_field_report,
)
from app.utils.enums import Region, ReportStatus, ReportType, TaskStatus, UserRole


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
    def __init__(self, exec_results=None) -> None:
        self.added = []
        self._exec_results = list(exec_results or [])

    def exec(self, statement):
        if self._exec_results:
            return self._exec_results.pop(0)
        return QueryResult()

    def add(self, item) -> None:
        self.added.append(item)

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        return None


def make_user(role: UserRole) -> TokenData:
    return TokenData(
        user_id=uuid4(), role=role, name="Test", surname="User", token_type="access"
    )


@pytest.mark.parametrize(
    "report_type,schedule_type",
    [
        (ReportType.DATACENTER, "datacenter_inspection"),
        (ReportType.POP, "pop_inspection"),
    ],
)
class TestSubmitCreatesCompletedReportAndMarksSchedule:
    def test_submit_marks_schedule_done(
        self, report_type: ReportType, schedule_type: str
    ) -> None:
        technician_id = uuid4()
        site_id = uuid4()
        completed_at = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
        technician = Technician(
            id=technician_id, phone="0123456789", id_no="9001010000000", user_id=uuid4()
        )
        site = Site(id=site_id, name="Teraco Rondebosch", region=Region.WESTERN_CAPE)
        schedule = MaintenanceSchedule(
            site_id=site_id,
            schedule_type=schedule_type,
            frequency="weekly",
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
            report_type=report_type,
            seacom_ref="SEACOM-492717",
            performed_at=completed_at,
            data={"source": "mobile"},
            attachments={"files": []},
        )

        assert task.status == TaskStatus.COMPLETED
        assert task.report_type == report_type.value
        assert task.description == REPORT_TO_DESCRIPTION[report_type]
        assert report.status == ReportStatus.COMPLETED
        assert report.report_type == report_type
        assert report.task_id == task.id
        assert schedule.last_run_at == completed_at
        assert schedule.next_due_at > completed_at

    def test_report_to_schedule_type_map_matches(
        self, report_type: ReportType, schedule_type: str
    ) -> None:
        assert REPORT_TO_SCHEDULE_TYPE[report_type] == schedule_type


@pytest.mark.parametrize("report_type", [ReportType.DATACENTER, ReportType.POP])
class TestStartCreatesStartedReportOfCorrectType:
    def test_start_report_is_correct_type_and_status(self, report_type: ReportType) -> None:
        technician_id = uuid4()
        site_id = uuid4()
        started_at = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        technician = Technician(
            id=technician_id, phone="0123456789", id_no="9001010000000", user_id=uuid4()
        )
        site = Site(id=site_id, name="MTN Tygerberg", region=Region.WESTERN_CAPE)

        task, report = create_started_field_report(
            session=FakeSession(),
            technician=technician,
            site=site,
            report_type=report_type,
            seacom_ref="SEACOM-492717",
            performed_at=started_at,
            data={"source": "mobile"},
        )

        assert task.status == TaskStatus.STARTED
        assert task.report_type == report_type.value
        assert report.status == ReportStatus.STARTED
        assert report.report_type == report_type
        assert report.attachments == {"files": []}


@pytest.mark.parametrize("report_type", [ReportType.DATACENTER, ReportType.POP])
class TestTechnicianOnlyGuard:
    def test_start_requires_technician_role(self, report_type: ReportType) -> None:
        service = _FieldWorkService()
        with pytest.raises(ForbiddenException, match="Only technicians"):
            service.start(
                payload=object(),
                report_type=report_type,
                session=FakeSession(),
                current_user=make_user(UserRole.NOC),
            )

    def test_submit_requires_technician_role(self, report_type: ReportType) -> None:
        service = _FieldWorkService()
        with pytest.raises(ForbiddenException, match="Only technicians"):
            service.submit(
                payload=object(),
                report_type=report_type,
                session=FakeSession(),
                current_user=make_user(UserRole.MANAGER),
            )
