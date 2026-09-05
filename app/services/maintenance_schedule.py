"""
MaintenanceSchedule service - CRUD, due-schedule queries, and weekly coverage.
"""

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import or_
from sqlmodel import Session, select

from app.exceptions.http import ConflictException, NotFoundException
from app.models.auth import TokenData
from app.models.maintenance_schedule import (
    MaintenanceSchedule,
    MaintenanceScheduleCoverage,
    MaintenanceScheduleCoverageCreate,
    MaintenanceScheduleCoverageResponse,
    MaintenanceScheduleCreate,
    MaintenanceScheduleResponse,
    MaintenanceScheduleUpdate,
    SCHEDULE_TYPES,
)
from app.services.authorization import require_management
from app.utils.enums import SiteType
from app.utils.funcs import utcnow


SITE_TYPE_SCHEDULE_TYPES: dict[SiteType, tuple[str, ...]] = {
    SiteType.ROUTINE_DRIVE: ("routine_drive",),
    SiteType.REPEATER: ("repeater_site_visit", "generator_diesel_refill"),
    SiteType.POP: ("pop_inspection",),
    SiteType.DATACENTER: ("datacenter_inspection",),
    SiteType.TASK_SITE: (),
}


def _week_bounds(anchor: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the ISO week window in UTC, Monday inclusive to next Monday exclusive."""
    now = anchor or utcnow()
    monday = now - timedelta(days=now.weekday())
    week_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    return week_start, week_end


def _schedule_interval(schedule: MaintenanceSchedule) -> timedelta:
    if schedule.frequency == "monthly":
        return timedelta(days=30)
    if schedule.frequency == "quarterly":
        return timedelta(days=90)
    return timedelta(days=7)


def _tech_name(technician_id: UUID | None, session: Session) -> str:
    if not technician_id:
        return ""

    from app.models import Technician

    tech = session.get(Technician, technician_id)
    if tech and tech.user:
        return f"{tech.user.name} {tech.user.surname}"
    return ""


def _active_coverage_for_schedule(
    schedule_id: UUID,
    session: Session,
    anchor: datetime | None = None,
) -> MaintenanceScheduleCoverage | None:
    week_start, week_end = _week_bounds(anchor)
    return session.exec(
        select(MaintenanceScheduleCoverage).where(
            MaintenanceScheduleCoverage.schedule_id == schedule_id,
            MaintenanceScheduleCoverage.week_start_at == week_start,
            MaintenanceScheduleCoverage.week_end_at == week_end,
            MaintenanceScheduleCoverage.cancelled_at.is_(None),  # type: ignore
            MaintenanceScheduleCoverage.deleted_at.is_(None),  # type: ignore
        )
    ).first()


def _effective_technician_id(
    schedule: MaintenanceSchedule,
    session: Session,
    anchor: datetime | None = None,
) -> UUID | None:
    coverage = _active_coverage_for_schedule(schedule.id, session, anchor)
    return coverage.assigned_technician_id if coverage else schedule.assigned_technician_id


def _enrich(
    schedule: MaintenanceSchedule, session: Session
) -> MaintenanceScheduleResponse:
    from app.models import Site

    site_name = ""
    site_region = None
    site_type = None
    site_geofence_radius = None
    coords = None
    site = session.get(Site, schedule.site_id)
    if site:
        site_name = site.name
        site_region = site.region
        site_type = site.site_type
        site_geofence_radius = site.geofence_radius
        coords = site.get_coordinates()

    coverage = _active_coverage_for_schedule(schedule.id, session)
    default_tech_name = _tech_name(schedule.assigned_technician_id, session)
    effective_technician_id = (
        coverage.assigned_technician_id if coverage else schedule.assigned_technician_id
    )
    effective_tech_name = _tech_name(effective_technician_id, session)
    original_technician_id = coverage.original_technician_id if coverage else None
    original_tech_name = _tech_name(original_technician_id, session)

    now = utcnow()
    is_overdue = schedule.next_due_at < now

    week_start, week_end = _week_bounds()
    completed_this_week = bool(
        schedule.last_run_at and week_start <= schedule.last_run_at < week_end
    )

    resp = MaintenanceScheduleResponse.model_validate(schedule)
    resp.site_name = site_name
    resp.site_region = site_region
    resp.site_type = site_type
    resp.site_geofence_radius = site_geofence_radius
    resp.site_latitude = coords[0] if coords else None
    resp.site_longitude = coords[1] if coords else None
    resp.technician_fullname = default_tech_name
    resp.is_overdue = is_overdue
    resp.completed_this_week = completed_this_week
    resp.effective_technician_id = effective_technician_id
    resp.effective_technician_fullname = effective_tech_name
    resp.original_technician_id = original_technician_id
    resp.original_technician_fullname = original_tech_name
    if coverage:
        resp.coverage_id = coverage.id
        resp.coverage_reason = coverage.reason
        resp.coverage_week_start_at = coverage.week_start_at
        resp.coverage_completed_at = coverage.completed_at
    return resp


def _coverage_to_response(
    coverage: MaintenanceScheduleCoverage, session: Session
) -> MaintenanceScheduleCoverageResponse:
    resp = MaintenanceScheduleCoverageResponse.model_validate(coverage)
    resp.assigned_technician_fullname = _tech_name(
        coverage.assigned_technician_id, session
    )
    resp.original_technician_fullname = _tech_name(
        coverage.original_technician_id, session
    )
    return resp


class _MaintenanceScheduleService:
    def ensure_weekly_due_diligence_schedules(
        self,
        session: Session,
        technician_id: UUID | None = None,
    ) -> None:
        from app.models import Site, TechnicianSite

        week_start, week_end = _week_bounds()
        assignment_stmt = select(TechnicianSite)
        if technician_id:
            assignment_stmt = assignment_stmt.where(
                TechnicianSite.technician_id == technician_id
            )
        assignments = session.exec(assignment_stmt).all()
        if not assignments:
            return

        created_or_updated = False
        for assignment in assignments:
            site = session.get(Site, assignment.site_id)
            if not site or site.deleted_at:
                continue

            schedule_types = SITE_TYPE_SCHEDULE_TYPES.get(site.site_type, ())
            existing_known_schedules = session.exec(
                select(MaintenanceSchedule).where(
                    MaintenanceSchedule.site_id == assignment.site_id,
                    MaintenanceSchedule.assigned_technician_id
                    == assignment.technician_id,
                    MaintenanceSchedule.schedule_type.in_(SCHEDULE_TYPES),  # type: ignore
                    MaintenanceSchedule.deleted_at.is_(None),  # type: ignore
                )
            ).all()
            for schedule in existing_known_schedules:
                should_be_active = schedule.schedule_type in schedule_types
                if schedule.is_active != should_be_active:
                    schedule.is_active = should_be_active
                    schedule.touch()
                    session.add(schedule)
                    created_or_updated = True

            if not schedule_types:
                continue

            for schedule_type in schedule_types:
                schedule = session.exec(
                    select(MaintenanceSchedule).where(
                        MaintenanceSchedule.site_id == assignment.site_id,
                        MaintenanceSchedule.assigned_technician_id
                        == assignment.technician_id,
                        MaintenanceSchedule.schedule_type == schedule_type,
                        MaintenanceSchedule.deleted_at.is_(None),  # type: ignore
                    )
                ).first()

                if schedule:
                    if not schedule.is_active:
                        schedule.is_active = True
                        schedule.touch()
                        session.add(schedule)
                        created_or_updated = True
                    if schedule.frequency != "weekly":
                        schedule.frequency = "weekly"
                        schedule.touch()
                        session.add(schedule)
                        created_or_updated = True
                    if schedule.next_due_at < week_start:
                        schedule.next_due_at = week_end
                        schedule.touch()
                        session.add(schedule)
                        created_or_updated = True
                    continue

                session.add(
                    MaintenanceSchedule(
                        site_id=assignment.site_id,
                        schedule_type=schedule_type,
                        frequency="weekly",
                        assigned_technician_id=assignment.technician_id,
                        is_active=True,
                        next_due_at=week_end,
                    )
                )
                created_or_updated = True

        if created_or_updated:
            session.commit()

    def create(
        self, data: MaintenanceScheduleCreate, session: Session
    ) -> MaintenanceScheduleResponse:
        schedule = MaintenanceSchedule.model_validate(data)
        session.add(schedule)
        session.commit()
        session.refresh(schedule)
        return _enrich(schedule, session)

    def list_all(
        self,
        session: Session,
        site_id: UUID | None = None,
        technician_id: UUID | None = None,
    ) -> list[MaintenanceScheduleResponse]:
        self.ensure_weekly_due_diligence_schedules(session, technician_id)

        stmt = select(MaintenanceSchedule).where(
            MaintenanceSchedule.deleted_at.is_(None)  # type: ignore
        )
        if site_id:
            stmt = stmt.where(MaintenanceSchedule.site_id == site_id)
        if technician_id:
            coverage_schedule_ids = self._active_coverage_schedule_ids(
                session, technician_id
            )
            owner_filter = MaintenanceSchedule.assigned_technician_id == technician_id
            if coverage_schedule_ids:
                stmt = stmt.where(
                    or_(
                        owner_filter,
                        MaintenanceSchedule.id.in_(coverage_schedule_ids),  # type: ignore
                    )
                )
            else:
                stmt = stmt.where(owner_filter)

        schedules = [_enrich(s, session) for s in session.exec(stmt).all()]
        if technician_id:
            schedules = [
                schedule
                for schedule in schedules
                if schedule.effective_technician_id == technician_id
            ]
        return schedules

    def get_due(
        self, session: Session, technician_id: UUID | None = None
    ) -> list[MaintenanceScheduleResponse]:
        """Return all active schedules due within the next 7 days."""
        self.ensure_weekly_due_diligence_schedules(session, technician_id)

        now = utcnow()
        horizon = now + timedelta(days=7)
        stmt = select(MaintenanceSchedule).where(
            MaintenanceSchedule.deleted_at.is_(None),  # type: ignore
            MaintenanceSchedule.is_active == True,  # noqa: E712
            MaintenanceSchedule.next_due_at <= horizon,
        )
        if technician_id:
            coverage_schedule_ids = self._active_coverage_schedule_ids(
                session, technician_id
            )
            owner_filter = MaintenanceSchedule.assigned_technician_id == technician_id
            if coverage_schedule_ids:
                stmt = stmt.where(
                    or_(
                        owner_filter,
                        MaintenanceSchedule.id.in_(coverage_schedule_ids),  # type: ignore
                    )
                )
            else:
                stmt = stmt.where(owner_filter)

        schedules = [_enrich(s, session) for s in session.exec(stmt).all()]
        if technician_id:
            schedules = [
                schedule
                for schedule in schedules
                if schedule.effective_technician_id == technician_id
            ]
        return schedules

    def _active_coverage_schedule_ids(
        self, session: Session, technician_id: UUID
    ) -> list[UUID]:
        week_start, week_end = _week_bounds()
        return list(
            session.exec(
                select(MaintenanceScheduleCoverage.schedule_id).where(
                    MaintenanceScheduleCoverage.assigned_technician_id
                    == technician_id,
                    MaintenanceScheduleCoverage.week_start_at == week_start,
                    MaintenanceScheduleCoverage.week_end_at == week_end,
                    MaintenanceScheduleCoverage.cancelled_at.is_(None),  # type: ignore
                    MaintenanceScheduleCoverage.deleted_at.is_(None),  # type: ignore
                )
            ).all()
        )

    def get(self, schedule_id: UUID, session: Session) -> MaintenanceScheduleResponse:
        schedule = session.get(MaintenanceSchedule, schedule_id)
        if not schedule or schedule.deleted_at:
            raise NotFoundException("maintenance schedule not found")
        return _enrich(schedule, session)

    def update(
        self, schedule_id: UUID, data: MaintenanceScheduleUpdate, session: Session
    ) -> MaintenanceScheduleResponse:
        schedule = session.get(MaintenanceSchedule, schedule_id)
        if not schedule or schedule.deleted_at:
            raise NotFoundException("maintenance schedule not found")
        for key, value in data.model_dump(exclude_none=True).items():
            setattr(schedule, key, value)
        schedule.touch()
        session.commit()
        session.refresh(schedule)
        return _enrich(schedule, session)

    def delete(self, schedule_id: UUID, session: Session) -> None:
        schedule = session.get(MaintenanceSchedule, schedule_id)
        if not schedule or schedule.deleted_at:
            raise NotFoundException("maintenance schedule not found")
        schedule.soft_delete()
        session.commit()

    def create_coverage(
        self,
        schedule_id: UUID,
        data: MaintenanceScheduleCoverageCreate,
        session: Session,
        current_user: TokenData,
    ) -> MaintenanceScheduleCoverageResponse:
        require_management(
            current_user,
            "Only NOC and management users can reassign weekly schedules.",
        )

        schedule = session.get(MaintenanceSchedule, schedule_id)
        if not schedule or schedule.deleted_at:
            raise NotFoundException("maintenance schedule not found")
        if not schedule.is_active:
            raise ConflictException("Cannot reassign an inactive schedule.")
        if schedule.assigned_technician_id == data.assigned_technician_id:
            raise ConflictException("Schedule is already assigned to that technician.")

        from app.models import Technician

        replacement = session.get(Technician, data.assigned_technician_id)
        if not replacement or replacement.deleted_at:
            raise NotFoundException("replacement technician not found")

        existing = _active_coverage_for_schedule(schedule_id, session)
        if existing:
            raise ConflictException(
                "This schedule already has an active reassignment for the week."
            )

        week_start, week_end = _week_bounds()
        coverage = MaintenanceScheduleCoverage(
            schedule_id=schedule_id,
            week_start_at=week_start,
            week_end_at=week_end,
            original_technician_id=schedule.assigned_technician_id,
            assigned_technician_id=data.assigned_technician_id,
            assigned_by_user_id=current_user.user_id,
            reason=data.reason,
        )
        session.add(coverage)
        session.commit()
        session.refresh(coverage)
        return _coverage_to_response(coverage, session)

    def cancel_coverage(
        self,
        schedule_id: UUID,
        coverage_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> None:
        require_management(
            current_user,
            "Only NOC and management users can cancel weekly schedule reassignments.",
        )

        coverage = session.get(MaintenanceScheduleCoverage, coverage_id)
        if (
            not coverage
            or coverage.deleted_at
            or coverage.schedule_id != schedule_id
            or coverage.cancelled_at
        ):
            raise NotFoundException("schedule coverage not found")
        if coverage.completed_at:
            raise ConflictException("Cannot cancel a completed reassignment.")

        coverage.cancelled_at = utcnow()
        coverage.touch()
        session.add(coverage)
        session.commit()

    def mark_schedule_done_for_field_work(
        self,
        *,
        session: Session,
        technician_id: UUID,
        site_id: UUID,
        schedule_type: str,
        completed_at: datetime,
    ) -> MaintenanceSchedule | None:
        schedules = session.exec(
            select(MaintenanceSchedule).where(
                MaintenanceSchedule.site_id == site_id,
                MaintenanceSchedule.schedule_type == schedule_type,
                MaintenanceSchedule.deleted_at.is_(None),  # type: ignore
                MaintenanceSchedule.is_active,  # type: ignore
            )
        ).all()

        matched_schedule = None
        matched_coverage = None
        for schedule in schedules:
            coverage = _active_coverage_for_schedule(schedule.id, session, completed_at)
            effective_technician_id = (
                coverage.assigned_technician_id
                if coverage
                else schedule.assigned_technician_id
            )
            if effective_technician_id == technician_id:
                matched_schedule = schedule
                matched_coverage = coverage
                break

        if not matched_schedule:
            return None

        matched_schedule.last_run_at = completed_at
        matched_schedule.next_due_at = completed_at + _schedule_interval(matched_schedule)
        matched_schedule.touch()
        session.add(matched_schedule)

        if matched_coverage and not matched_coverage.completed_at:
            matched_coverage.completed_at = completed_at
            matched_coverage.touch()
            session.add(matched_coverage)

        return matched_schedule

    def effective_schedule_owner_id(
        self, schedule: MaintenanceSchedule, session: Session
    ) -> UUID | None:
        return _effective_technician_id(schedule, session)


def get_maintenance_schedule_service() -> "_MaintenanceScheduleService":
    return _MaintenanceScheduleService()


MaintenanceScheduleService = Annotated[
    _MaintenanceScheduleService, Depends(get_maintenance_schedule_service)
]
