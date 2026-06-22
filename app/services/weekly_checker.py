"""
Weekly Scheduled Task Checker.

Runs daily (call from a cron job or scheduled endpoint).

Logic per Annexure A of the SAMO/SEACOM agreement:
  - Technicians must complete all three scheduled task types every week:
      routine_drive, repeater_site_visit, generator_diesel_refill
  - Wednesday (day 2): if a tech has NOT completed a type this week → warn NOC
  - Friday (day 4):    if still not completed → escalate to NOC + Managers

"Completed this week" = maintenance_schedule.last_run_at falls within
the current ISO week (Monday 00:00 UTC → Sunday 23:59 UTC).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlmodel import Session, select

from app.models import Notification, User
from app.models.maintenance_schedule import MaintenanceSchedule
from app.services.maintenance_schedule import get_maintenance_schedule_service
from app.models.technician import Technician
from app.utils.enums import NotificationPriority, UserRole
from app.utils.funcs import utcnow


def _week_bounds():
    now = utcnow()
    monday = now - timedelta(days=now.weekday())
    week_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    return week_start, week_end


def check_weekly_scheduled_tasks(session: Session) -> dict:
    """
    Inspect all active weekly maintenance schedules and fire notifications
    when techs are overdue.

    Returns a summary dict describing what alerts were sent.
    """
    now = utcnow()
    weekday = now.weekday()  # 0=Mon … 6=Sun

    # Only act on Wednesday (2) and Friday (4)
    if weekday not in (2, 4):
        return {
            "skipped": True,
            "reason": f"Checker only fires on Wednesday and Friday (today is weekday {weekday})",
        }

    is_friday = weekday == 4
    week_start, week_end = _week_bounds()
    schedule_service = get_maintenance_schedule_service()
    schedule_service.ensure_weekly_due_diligence_schedules(session)

    # Fetch NOC + Manager user IDs for notifications
    noc_user_ids: list = [
        u.id
        for u in session.exec(
            select(User).where(
                User.role.in_([UserRole.NOC, UserRole.MANAGER]),  # type: ignore
                User.deleted_at.is_(None),
            )
        ).all()
    ]
    _manager_user_ids: list = [
        u.id
        for u in session.exec(
            select(User).where(User.role == UserRole.MANAGER, User.deleted_at.is_(None))
        ).all()
    ]

    # Load all active weekly schedules grouped by effective technician for this week.
    weekly_schedules = session.exec(
        select(MaintenanceSchedule).where(
            MaintenanceSchedule.deleted_at.is_(None),
            MaintenanceSchedule.is_active == True,  # noqa: E712
            MaintenanceSchedule.frequency == "weekly",
        )
    ).all()

    # Group by technician
    by_tech: dict[str, list[MaintenanceSchedule]] = {}
    for sched in weekly_schedules:
        owner_id = schedule_service.effective_schedule_owner_id(sched, session)
        if owner_id is None:
            continue
        key = str(owner_id)
        by_tech.setdefault(key, []).append(sched)

    alerts_sent: list[dict] = []

    for tech_id_str, schedules in by_tech.items():
        tech_id = UUID(tech_id_str)
        tech = session.get(Technician, tech_id)
        if not tech or not tech.user:
            continue

        tech_name = f"{tech.user.name} {tech.user.surname}"
        tech_user_id = tech.user_id

        # Find which schedule types are NOT completed this week
        overdue_types: list[str] = []
        for sched in schedules:
            completed = (
                sched.last_run_at is not None
                and week_start <= sched.last_run_at < week_end
            )
            if not completed:
                overdue_types.append(sched.schedule_type)

        if not overdue_types:
            continue  # Tech is on track — nothing to do

        _SCHEDULE_LABELS = {
            "routine_drive": "Routine Drive",
            "repeater_site_visit": "Repeater Site Visit",
            "generator_diesel_refill": "Generator Diesel Refill",
            "pop_site": "POP Site",
        }
        overdue_labels = ", ".join(_SCHEDULE_LABELS.get(t, t) for t in overdue_types)

        if is_friday:
            # Friday escalation — NOC + Managers
            title = f"ESCALATION — {tech_name} has NOT completed scheduled tasks"
            message = (
                f"{tech_name} has not completed the following mandatory tasks this week: "
                f"{overdue_labels}. Immediate follow-up required."
            )
            priority = NotificationPriority.CRITICAL

            # Notify the technician directly and all NOC + Managers
            recipient_ids = [tech_user_id, *noc_user_ids]
        else:
            # Wednesday warning — NOC only
            title = f"Warning — {tech_name} scheduled tasks not yet done"
            message = (
                f"{tech_name} has not yet completed: {overdue_labels}. "
                f"If not done by Friday, managers will be alerted."
            )
            priority = NotificationPriority.HIGH

            recipient_ids = [tech_user_id, *(u for u in noc_user_ids if u != tech_user_id)]

        for uid in recipient_ids:
            session.add(
                Notification(
                    user_id=uid,
                    title=title,
                    message=message,
                    priority=priority,
                )
            )
        session.commit()

        alerts_sent.append(
            {
                "technician": tech_name,
                "overdue_types": overdue_types,
                "level": "escalation" if is_friday else "warning",
            }
        )

    return {
        "checked_at": now.isoformat(),
        "weekday": weekday,
        "level": "escalation" if is_friday else "warning",
        "alerts_sent": len(alerts_sent),
        "details": alerts_sent,
    }
