"""
FaultUpdate service — manages the mandatory incident communication log.

Update interval: 30 minutes for all severity levels.
"""

from uuid import UUID
from typing import Annotated
from datetime import timedelta

from fastapi import Depends
from sqlmodel import Session, select

from app.models.fault_update import FaultUpdate, FaultUpdateCreate, FaultUpdateResponse
from app.models import Incident
from app.utils.enums import IncidentStatus, UserRole
from app.utils.funcs import utcnow

# All severity levels require an update every 30 minutes
UPDATE_INTERVAL_MINUTES = 30

UPDATE_INTERVALS: dict[str, int] = {
    "critical": UPDATE_INTERVAL_MINUTES,
    "major":    UPDATE_INTERVAL_MINUTES,
    "minor":    UPDATE_INTERVAL_MINUTES,
    "query":    UPDATE_INTERVAL_MINUTES,
}


def _is_update_overdue(incident: Incident, session: Session) -> bool:
    """
    Return True if the last logged update is older than the required interval
    for the incident's severity, and the incident is not yet restored.
    """
    if incident.temporarily_restored_at:
        return False  # post-restore intervals are less strict

    severity = str(incident.severity) if incident.severity else "minor"
    interval_minutes = UPDATE_INTERVALS.get(severity, 1440)
    cutoff = utcnow() - timedelta(minutes=interval_minutes)

    last_update = session.exec(
        select(FaultUpdate)
        .where(
            FaultUpdate.incident_id == incident.id,
            FaultUpdate.deleted_at.is_(None),
        )
        .order_by(FaultUpdate.created_at.desc())  # type: ignore
        .limit(1)
    ).first()

    if last_update is None:
        start = incident.start_time or incident.created_at
        return start < cutoff

    return last_update.created_at < cutoff


class _FaultUpdateService:
    def create_update(
        self,
        incident_id: UUID,
        data: FaultUpdateCreate,
        sent_by: UUID,
        sent_by_name: str,
        session: Session,
    ) -> FaultUpdateResponse:
        # Verify incident exists
        incident = session.get(Incident, incident_id)
        if not incident or incident.deleted_at:
            from app.exceptions.http import NotFoundException
            raise NotFoundException("incident not found")

        if incident.status == IncidentStatus.RESOLVED:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Cannot add remarks to a resolved incident")

        is_overdue = _is_update_overdue(incident, session)

        update = FaultUpdate(
            incident_id=incident_id,
            update_type=data.update_type,
            message=data.message,
            sent_by=sent_by,
            sent_by_name=sent_by_name,
            is_overdue=is_overdue,
        )
        session.add(update)
        session.commit()
        session.refresh(update)

        # Notify NOC and Managers that a remark was submitted
        try:
            from app.models import User
            from app.services.notification import _NotificationService, NotificationTemplates
            notification_service = _NotificationService()
            site_name = incident.site.name if incident.site else "Unknown Site"
            ref_no = incident.ref_no or getattr(incident, "seacom_ref", None)
            recipients = session.exec(
                select(User).where(
                    User.role.in_([UserRole.NOC, UserRole.MANAGER]),  # type: ignore
                    User.deleted_at.is_(None),
                )
            ).all()
            notification_service.create_notifications_from_template(
                user_ids=(u.id for u in recipients),
                template=NotificationTemplates.incident_update_submitted(
                    sent_by_name, site_name, data.message, ref_no
                ),
                session=session,
            )
        except Exception:
            pass  # Never block the response due to notification failure

        return FaultUpdateResponse.model_validate(update)

    def list_updates(self, incident_id: UUID, session: Session) -> list[FaultUpdateResponse]:
        rows = session.exec(
            select(FaultUpdate)
            .where(
                FaultUpdate.incident_id == incident_id,
                FaultUpdate.deleted_at.is_(None),
            )
            .order_by(FaultUpdate.created_at.asc())  # type: ignore
        ).all()
        return [FaultUpdateResponse.model_validate(r) for r in rows]

    def get_update_due_status(self, incident_id: UUID, session: Session) -> dict:
        """
        Return whether a new update is currently overdue and when the next one is due.
        """
        incident = session.get(Incident, incident_id)
        if not incident or incident.deleted_at:
            from app.exceptions.http import NotFoundException
            raise NotFoundException("incident not found")

        severity = str(incident.severity) if incident.severity else "minor"
        interval = UPDATE_INTERVALS.get(severity, 1440)
        overdue  = _is_update_overdue(incident, session)

        return {
            "is_overdue":        overdue,
            "interval_minutes":  interval,
            "severity":          severity,
        }


def get_fault_update_service() -> "_FaultUpdateService":
    return _FaultUpdateService()


FaultUpdateService = Annotated[_FaultUpdateService, Depends(get_fault_update_service)]
