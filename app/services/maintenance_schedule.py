"""
MaintenanceSchedule service — CRUD and due-schedule queries.
"""

from uuid import UUID
from typing import Annotated
from datetime import timedelta

from fastapi import Depends
from sqlmodel import Session, select, and_

from app.models.maintenance_schedule import (
    MaintenanceSchedule,
    MaintenanceScheduleCreate,
    MaintenanceScheduleUpdate,
    MaintenanceScheduleResponse,
)
from app.utils.funcs import utcnow


def _week_bounds():
    """Return (start_of_week, end_of_week) in UTC for the current ISO week (Mon–Sun)."""
    now = utcnow()
    monday = now - timedelta(days=now.weekday())
    week_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end   = week_start + timedelta(days=7)
    return week_start, week_end


def _enrich(schedule: MaintenanceSchedule, session: Session) -> MaintenanceScheduleResponse:
    from app.models import Site, Technician
    site_name = ""
    tech_name = ""
    site = session.get(Site, schedule.site_id)
    if site:
        site_name = site.name

    if schedule.assigned_technician_id:
        tech = session.get(Technician, schedule.assigned_technician_id)
        if tech and tech.user:
            tech_name = f"{tech.user.name} {tech.user.surname}"

    now = utcnow()
    is_overdue = schedule.next_due_at < now

    # Completed this week if last_run_at falls within the current Mon–Sun window
    week_start, week_end = _week_bounds()
    completed_this_week = bool(
        schedule.last_run_at
        and week_start <= schedule.last_run_at < week_end
    )

    resp = MaintenanceScheduleResponse.model_validate(schedule)
    resp.site_name            = site_name
    resp.technician_fullname  = tech_name
    resp.is_overdue           = is_overdue
    resp.completed_this_week  = completed_this_week
    return resp


class _MaintenanceScheduleService:
    def create(self, data: MaintenanceScheduleCreate, session: Session) -> MaintenanceScheduleResponse:
        schedule = MaintenanceSchedule.model_validate(data)
        session.add(schedule)
        session.commit()
        session.refresh(schedule)
        return _enrich(schedule, session)

    def list_all(self, session: Session, site_id: UUID | None = None, technician_id: UUID | None = None) -> list[MaintenanceScheduleResponse]:
        stmt = select(MaintenanceSchedule).where(MaintenanceSchedule.deleted_at.is_(None))
        if site_id:
            stmt = stmt.where(MaintenanceSchedule.site_id == site_id)
        if technician_id:
            stmt = stmt.where(MaintenanceSchedule.assigned_technician_id == technician_id)
        return [_enrich(s, session) for s in session.exec(stmt).all()]

    def get_due(self, session: Session, technician_id: UUID | None = None) -> list[MaintenanceScheduleResponse]:
        """Return all active schedules due within the next 7 days."""
        now     = utcnow()
        horizon = now + timedelta(days=7)
        stmt = select(MaintenanceSchedule).where(
            MaintenanceSchedule.deleted_at.is_(None),
            MaintenanceSchedule.is_active == True,  # noqa: E712
            MaintenanceSchedule.next_due_at <= horizon,
        )
        if technician_id:
            stmt = stmt.where(MaintenanceSchedule.assigned_technician_id == technician_id)
        return [_enrich(s, session) for s in session.exec(stmt).all()]

    def get(self, schedule_id: UUID, session: Session) -> MaintenanceScheduleResponse:
        s = session.get(MaintenanceSchedule, schedule_id)
        if not s or s.deleted_at:
            from app.exceptions.http import NotFoundException
            raise NotFoundException("maintenance schedule not found")
        return _enrich(s, session)

    def update(self, schedule_id: UUID, data: MaintenanceScheduleUpdate, session: Session) -> MaintenanceScheduleResponse:
        s = session.get(MaintenanceSchedule, schedule_id)
        if not s or s.deleted_at:
            from app.exceptions.http import NotFoundException
            raise NotFoundException("maintenance schedule not found")
        for k, v in data.model_dump(exclude_none=True).items():
            setattr(s, k, v)
        s.touch()
        session.commit()
        session.refresh(s)
        return _enrich(s, session)

    def delete(self, schedule_id: UUID, session: Session) -> None:
        s = session.get(MaintenanceSchedule, schedule_id)
        if not s or s.deleted_at:
            from app.exceptions.http import NotFoundException
            raise NotFoundException("maintenance schedule not found")
        s.soft_delete()
        session.commit()


def get_maintenance_schedule_service() -> "_MaintenanceScheduleService":
    return _MaintenanceScheduleService()


MaintenanceScheduleService = Annotated[_MaintenanceScheduleService, Depends(get_maintenance_schedule_service)]
