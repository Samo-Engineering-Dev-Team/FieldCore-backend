from fastapi import APIRouter, BackgroundTasks, Query
from typing import List
from uuid import UUID

from app.models import IncidentCreate, IncidentUpdate, IncidentResponse
from app.models.fault_update import FaultUpdateCreate, FaultUpdateResponse
from app.services import IncidentService, CurrentUser
from app.services.fault_update import _FaultUpdateService, get_fault_update_service
from app.services.incident import _bg_notify_incident_created, _bg_notify_incident_started, _bg_notify_incident_resolved
from app.database import Session
from app.utils.enums import IncidentStatus

router = APIRouter(prefix="/incidents", tags=["Incidents"])


@router.post("/", response_model=IncidentResponse, status_code=201)
def create_incident(
    payload: IncidentCreate,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
) -> IncidentResponse:
    """"""
    response = service.create_incident(payload, session, current_user)
    background_tasks.add_task(
        _bg_notify_incident_created,
        technician_id=response.technician_id,
        site_name=response.site_name,
        tech_name=response.technician_fullname,
        description=payload.description,
        assigning_user_id=current_user.user_id,
    )
    return response


@router.get("/", response_model=List[IncidentResponse], status_code=200)
def read_incidents(
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
    technician_id: UUID | None = Query(None),
    status: IncidentStatus | None = Query(None),
    client_id: UUID | None = Query(None, description="Filter by client ID"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000)
) -> List[IncidentResponse]:
    """"""
    return service.read_incidents(session, technician_id, status, client_id, offset, limit)


@router.get("/penalty-summary", status_code=200)
def get_penalty_summary(
    session: Session,
    current_user: CurrentUser,
) -> dict:
    """
    Return the current quarter's penalty exposure summary per Annexure H.
    Flags termination risk when 3+ SLA faults have been breached this quarter.
    """
    from app.services.penalty_calculator import get_quarter_penalty_summary
    from app.utils.funcs import utcnow
    from sqlmodel import select
    from app.models import Incident
    from sqlalchemy import and_

    now = utcnow()
    q = (now.month - 1) // 3
    quarter_start = now.replace(month=q * 3 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)

    incidents = session.exec(
        select(Incident).where(
            and_(
                Incident.deleted_at.is_(None),
                Incident.start_time >= quarter_start,
            )
        )
    ).all()

    return get_quarter_penalty_summary(incidents)


@router.get("/{incident_id}", response_model=IncidentResponse, status_code=200)
def read_incident(
    incident_id: UUID,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
) -> IncidentResponse:
    """"""
    return service.read_incident(incident_id, session)


@router.patch("/{incident_id}", response_model=IncidentResponse, status_code=200)
def update_incident(
    incident_id: UUID,
    payload: IncidentUpdate,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
) -> IncidentResponse:
    """"""
    return service.update_incident(incident_id, payload, session)


@router.delete("/{incident_id}", status_code=204)
def delete_incident(
    incident_id: UUID,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
) -> None:
    """"""
    service.delete_incident(incident_id, session)


@router.patch("/{incident_id}/start", response_model=IncidentResponse, status_code=200)
def start_incident(
    incident_id: UUID,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
) -> IncidentResponse:
    """"""
    response = service.start_incident(incident_id, session)
    background_tasks.add_task(
        _bg_notify_incident_started,
        site_name=response.site_name,
        tech_name=response.technician_fullname,
    )
    return response


@router.patch("/{incident_id}/resolve", response_model=IncidentResponse, status_code=200)
def resolve_incident(
    incident_id: UUID,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
) -> IncidentResponse:
    """"""
    response = service.resolve_incident(incident_id, session)
    background_tasks.add_task(
        _bg_notify_incident_resolved,
        site_name=response.site_name,
        tech_name=response.technician_fullname,
        ref_no=response.ref_no or response.seacom_ref,
        severity=str(response.severity) if response.severity else "minor",
        description=response.description or "",
    )
    return response


# ── SLA Milestone endpoints ────────────────────────────────────────────────────

@router.post("/{incident_id}/respond", response_model=IncidentResponse, status_code=200)
def mark_responded(
    incident_id: UUID,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
) -> IncidentResponse:
    """Record that the technician has acknowledged/responded to the fault."""
    return service.mark_responded(incident_id, session)


@router.post("/{incident_id}/arrive", response_model=IncidentResponse, status_code=200)
def mark_arrived_on_site(
    incident_id: UUID,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
) -> IncidentResponse:
    """Record that the technician has arrived on site (SLA milestone 2)."""
    return service.mark_arrived_on_site(incident_id, session)


@router.post("/{incident_id}/temp-restore", response_model=IncidentResponse, status_code=200)
def mark_temporarily_restored(
    incident_id: UUID,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
) -> IncidentResponse:
    """Record that service has been temporarily restored (SLA milestone 3)."""
    return service.mark_temporarily_restored(incident_id, session)


@router.post("/{incident_id}/perm-restore", response_model=IncidentResponse, status_code=200)
def mark_permanently_restored(
    incident_id: UUID,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
) -> IncidentResponse:
    """Record that service has been permanently restored."""
    return service.mark_permanently_restored(incident_id, session)


# ── Fault Update (communication log) sub-endpoints ────────────────────────────

@router.get("/{incident_id}/updates", response_model=List[FaultUpdateResponse], status_code=200)
def list_fault_updates(
    incident_id: UUID,
    session: Session,
    current_user: CurrentUser,
) -> List[FaultUpdateResponse]:
    """List all logged updates for an incident (Annexure H communication log)."""
    svc = get_fault_update_service()
    return svc.list_updates(incident_id, session)


@router.post("/{incident_id}/updates", response_model=FaultUpdateResponse, status_code=201)
def create_fault_update(
    incident_id: UUID,
    payload: FaultUpdateCreate,
    session: Session,
    current_user: CurrentUser,
) -> FaultUpdateResponse:
    """Log a fault update (phone call, email, or app update)."""
    from app.models import User
    user = session.get(User, current_user.user_id)
    sent_by_name = f"{user.name} {user.surname}" if user else str(current_user.user_id)
    svc = get_fault_update_service()
    return svc.create_update(incident_id, payload, current_user.user_id, sent_by_name, session)


@router.get("/{incident_id}/updates/due-status", status_code=200)
def get_update_due_status(
    incident_id: UUID,
    session: Session,
    current_user: CurrentUser,
) -> dict:
    """Return whether a new fault update is currently overdue."""
    svc = get_fault_update_service()
    return svc.get_update_due_status(incident_id, session)


# ── SLA / checker endpoints ────────────────────────────────────────────────────

@router.post("/check-sla", status_code=200)
def check_sla_breaches_endpoint(
    session: Session,
    current_user: CurrentUser,
) -> dict:
    """
    Check all active incidents for SLA warnings/breaches and fire notifications.
    Intended to be called periodically (e.g., via a cron job every 15 minutes).
    """
    from app.services.sla_checker import check_sla_breaches

    warnings, breaches = check_sla_breaches(session)

    return {
        "checked_at": __import__("datetime").datetime.utcnow().isoformat(),
        "warnings_sent": len(warnings),
        "breaches_found": len(breaches),
        "warnings": warnings,
        "breaches": breaches
    }


@router.get("/{incident_id}/sla-status", status_code=200)
def get_incident_sla_status(
    incident_id: UUID,
    service: IncidentService,
    session: Session,
    current_user: CurrentUser,
) -> dict:
    """Get the three-milestone SLA status for a specific incident."""
    from app.services.sla_checker import get_sla_status_for_incident
    from sqlmodel import select
    from app.models import Incident

    incident = session.exec(
        select(Incident).where(Incident.id == incident_id, Incident.deleted_at.is_(None))
    ).first()

    if not incident:
        from app.exceptions.http import NotFoundException
        raise NotFoundException("incident not found")

    return get_sla_status_for_incident(incident)


# ── Technician Help Alert ──────────────────────────────────────────────────────

from pydantic import BaseModel
from typing import Literal


class HelpAlertRequest(BaseModel):
    priority: Literal["low", "medium", "high", "critical"]
    message: str


@router.post("/help-alert", status_code=204)
def send_help_alert(
    payload: HelpAlertRequest,
    session: Session,
    current_user: CurrentUser,
) -> None:
    """
    Send an urgent help alert from a technician to all NOC operators and managers.
    Creates a dedicated alert notification entry for recipients.
    """
    from sqlmodel import select
    from app.models import User
    from app.utils.enums import UserRole
    from app.services.notification import _NotificationService, NotificationTemplates

    # Resolve sender name
    sender = session.get(User, current_user.user_id)
    if sender:
        tech_name = f"{sender.name} {sender.surname}"
    else:
        tech_name = "Unknown Technician"

    # Notify all NOC operators and managers
    recipients = session.exec(
        select(User).where(
            User.role.in_([UserRole.NOC, UserRole.MANAGER]),
            User.deleted_at.is_(None),
        )
    ).all()

    notification_service = _NotificationService()
    notification_service.create_notifications_from_template(
        user_ids=(u.id for u in recipients),
        template=NotificationTemplates.technician_help_alert(
            technician_name=tech_name,
            priority=payload.priority,
            reason=payload.message,
        ),
        session=session,
    )
