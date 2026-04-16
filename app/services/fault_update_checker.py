"""
Fault Update Overdue Checker.

Scans all in-progress incidents and fires notifications when a technician
has not submitted a remark within the required 30-minute window.

Call via POST /incidents/check-overdue-updates (cron every 30 minutes).
"""

from sqlalchemy import and_
from sqlmodel import Session, select

from app.models import Incident, User
from app.models.fault_update import FaultUpdate
from app.utils.enums import IncidentStatus, UserRole
from app.utils.funcs import utcnow
from app.services.fault_update import UPDATE_INTERVAL_MINUTES, _is_update_overdue


def check_overdue_incident_updates(session: Session) -> dict:
    """
    For every in-progress incident, check whether the assigned technician
    has submitted a remark within the last 30 minutes.

    When overdue:
      - Technician receives a reminder to log an update.
      - All NOC and Manager users are alerted.

    Returns a summary dict for the cron caller.
    """
    from app.services.notification import _NotificationService, NotificationTemplates

    notification_service = _NotificationService()
    now = utcnow()

    # Pre-fetch NOC + Manager user IDs
    noc_manager_user_ids = [
        u.id for u in session.exec(
            select(User).where(
                User.role.in_([UserRole.NOC, UserRole.MANAGER]),  # type: ignore
                User.deleted_at.is_(None),
            )
        ).all()
    ]

    active_incidents = session.exec(
        select(Incident).where(
            and_(
                Incident.status == IncidentStatus.IN_PROGRESS,
                Incident.deleted_at.is_(None),
            )
        )
    ).all()

    alerted: list[dict] = []

    for incident in active_incidents:
        # Skip incidents that are already temporarily restored
        if incident.temporarily_restored_at:
            continue

        if not _is_update_overdue(incident, session):
            continue

        # Calculate how many minutes overdue
        last_update = session.exec(
            select(FaultUpdate)
            .where(
                FaultUpdate.incident_id == incident.id,
                FaultUpdate.deleted_at.is_(None),
            )
            .order_by(FaultUpdate.created_at.desc())  # type: ignore
            .limit(1)
        ).first()

        reference_time = (
            last_update.created_at if last_update
            else (incident.start_time or incident.created_at)
        )
        elapsed_minutes = int((now - reference_time).total_seconds() / 60)
        minutes_overdue = max(0, elapsed_minutes - UPDATE_INTERVAL_MINUTES)

        site_name = incident.site.name if incident.site else "Unknown Site"
        ref_no = incident.ref_no or getattr(incident, "seacom_ref", None)
        tech_name = (
            f"{incident.technician.user.name} {incident.technician.user.surname}"
            if incident.technician and incident.technician.user
            else "Unknown Technician"
        )
        tech_user_id = incident.technician.user_id if incident.technician else None

        # Notify the technician
        if tech_user_id:
            notification_service.create_notification_from_template(
                user_id=tech_user_id,
                template=NotificationTemplates.incident_update_overdue_tech(
                    site_name, minutes_overdue, ref_no
                ),
                session=session,
            )

        # Notify NOC + Managers
        notification_service.create_notifications_from_template(
            user_ids=noc_manager_user_ids,
            template=NotificationTemplates.incident_update_overdue(
                site_name, tech_name, minutes_overdue, ref_no
            ),
            session=session,
        )

        alerted.append({
            "incident_id":    str(incident.id),
            "ref_no":         ref_no,
            "site_name":      site_name,
            "technician":     tech_name,
            "minutes_overdue": minutes_overdue,
        })

    return {
        "checked_at":       now.isoformat(),
        "active_incidents": len(active_incidents),
        "overdue_count":    len(alerted),
        "alerted":          alerted,
    }
