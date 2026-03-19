"""
MaintenanceSchedule API — CRUD and due-schedule queries.
"""

from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models.maintenance_schedule import (
    MaintenanceScheduleCreate,
    MaintenanceScheduleUpdate,
    MaintenanceScheduleResponse,
)
from app.services.maintenance_schedule import MaintenanceScheduleService
from app.services import CurrentUser
from app.database import Session

router = APIRouter(prefix="/maintenance-schedules", tags=["Maintenance Schedules"])


@router.post("/", response_model=MaintenanceScheduleResponse, status_code=201)
def create_schedule(
    payload: MaintenanceScheduleCreate,
    service: MaintenanceScheduleService,
    session: Session,
    current_user: CurrentUser,
) -> MaintenanceScheduleResponse:
    """Create a new recurring maintenance schedule."""
    return service.create(payload, session)


@router.get("/due", response_model=List[MaintenanceScheduleResponse], status_code=200)
def get_due_schedules(
    service: MaintenanceScheduleService,
    session: Session,
    current_user: CurrentUser,
    technician_id: UUID | None = Query(None),
) -> List[MaintenanceScheduleResponse]:
    """Return all active schedules due within the next 7 days."""
    return service.get_due(session, technician_id)


@router.get("/", response_model=List[MaintenanceScheduleResponse], status_code=200)
def list_schedules(
    service: MaintenanceScheduleService,
    session: Session,
    current_user: CurrentUser,
    site_id: UUID | None = Query(None),
    technician_id: UUID | None = Query(None),
) -> List[MaintenanceScheduleResponse]:
    """List all maintenance schedules with optional filters."""
    return service.list_all(session, site_id, technician_id)


@router.get("/{schedule_id}", response_model=MaintenanceScheduleResponse, status_code=200)
def get_schedule(
    schedule_id: UUID,
    service: MaintenanceScheduleService,
    session: Session,
    current_user: CurrentUser,
) -> MaintenanceScheduleResponse:
    return service.get(schedule_id, session)


@router.patch("/{schedule_id}", response_model=MaintenanceScheduleResponse, status_code=200)
def update_schedule(
    schedule_id: UUID,
    payload: MaintenanceScheduleUpdate,
    service: MaintenanceScheduleService,
    session: Session,
    current_user: CurrentUser,
) -> MaintenanceScheduleResponse:
    return service.update(schedule_id, payload, session)


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: UUID,
    service: MaintenanceScheduleService,
    session: Session,
    current_user: CurrentUser,
) -> None:
    service.delete(schedule_id, session)


@router.post("/check-weekly", status_code=200)
def check_weekly_tasks(
    session: Session,
    current_user: CurrentUser,
) -> dict:
    """
    Check all active weekly maintenance schedules and fire escalation notifications
    for technicians who have not completed their tasks this week.

    Wednesday: warns NOC.
    Friday: escalates to NOC + Managers.

    Call this from a cron job once per day (e.g., 08:00 SAST).
    """
    from app.services.weekly_checker import check_weekly_scheduled_tasks
    return check_weekly_scheduled_tasks(session)


@router.patch("/{schedule_id}/mark-done", response_model=MaintenanceScheduleResponse, status_code=200)
def mark_schedule_done(
    schedule_id: UUID,
    service: MaintenanceScheduleService,
    session: Session,
    current_user: CurrentUser,
) -> MaintenanceScheduleResponse:
    """
    Mark a maintenance schedule as completed now (sets last_run_at = now and
    advances next_due_at by one frequency period).
    """
    from app.utils.funcs import utcnow
    from datetime import timedelta
    from app.models.maintenance_schedule import MaintenanceSchedule, MaintenanceScheduleUpdate

    sched = session.get(MaintenanceSchedule, schedule_id)
    if not sched or sched.deleted_at:
        from app.exceptions.http import NotFoundException
        raise NotFoundException("maintenance schedule not found")

    now = utcnow()
    freq_days = {"weekly": 7, "monthly": 30, "quarterly": 91}.get(sched.frequency, 7)
    update = MaintenanceScheduleUpdate(
        last_run_at=now,
        next_due_at=now + timedelta(days=freq_days),
    )
    return service.update(schedule_id, update, session)
