from typing import Annotated, List
from uuid import UUID

from fastapi import Depends
from loguru import logger as LOG

# from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.exceptions.http import (
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import (
    Incident,
    IncidentCreate,
    IncidentResponse,
    IncidentUpdate,
    Notification,
    Site,
    Technician,
    User,
)
from app.models.auth import TokenData
from app.services.authorization import get_technician_id_for_user, is_management
from app.services.notification import NotificationTemplates
from app.utils.enums import IncidentStatus, UserRole
from app.utils.funcs import utcnow

# ── Background notification helpers ───────────────────────────────────────────
# These run AFTER the HTTP response is sent (via FastAPI BackgroundTasks) so
# slow DB round-trips never block or time-out the client.


def _bg_notify_incident_created(
    technician_id: UUID,
    site_name: str,
    tech_name: str,
    description: str,
    assigning_user_id: UUID | None,
) -> None:
    """Background task: notify technician + NOC when a new incident is assigned."""
    try:
        from app.database import Database

        with Database.session() as session:
            technician = session.get(Technician, technician_id)
            if not technician:
                return
            is_self_assigned = (
                assigning_user_id == technician.user_id if assigning_user_id else False
            )
            if not is_self_assigned:
                template = NotificationTemplates.incident_assigned_to_technician(
                    site_name=site_name,
                    description=description,
                )
                session.add(
                    Notification(
                        user_id=technician.user_id,
                        title=template.title,
                        message=template.message,
                        priority=template.priority,
                    )
                )
            noc_users = session.exec(
                select(User).where(User.role == UserRole.NOC, User.deleted_at.is_(None))  # type: ignore
            ).all()
            noc_template = NotificationTemplates.incident_created_for_noc(
                site_name=site_name,
                technician_name=tech_name,
                description=description,
            )
            for user in noc_users:
                session.add(
                    Notification(
                        user_id=user.id,
                        title=noc_template.title,
                        message=noc_template.message,
                        priority=noc_template.priority,
                    )
                )
            session.commit()
    except Exception as e:
        LOG.warning("Background incident-created notifications failed: {}", e)


def _bg_notify_incident_started(site_name: str, tech_name: str) -> None:
    """Background task: notify NOC when a technician starts working on an incident."""
    try:
        from app.database import Database

        with Database.session() as session:
            noc_users = session.exec(
                select(User).where(User.role == UserRole.NOC, User.deleted_at.is_(None))  # type: ignore
            ).all()
            template = NotificationTemplates.incident_in_progress(tech_name, site_name)
            for user in noc_users:
                session.add(
                    Notification(
                        user_id=user.id,
                        title=template.title,
                        message=template.message,
                        priority=template.priority,
                    )
                )
            session.commit()
    except Exception as e:
        LOG.warning("Background incident-started notifications failed: {}", e)


def _bg_notify_incident_resolved(
    site_name: str,
    tech_name: str,
    ref_no: str | None,
    severity: str,
    description: str,
) -> None:
    """Background task: notify NOC + send email when an incident is resolved."""
    try:
        from app.database import Database

        with Database.session() as session:
            noc_users = session.exec(
                select(User).where(
                    User.role == UserRole.NOC,
                    User.deleted_at.is_(None),  # type: ignore
                )
            ).all()
            template = NotificationTemplates.incident_resolved(
                tech_name, site_name, ref_no=ref_no
            )
            for user in noc_users:
                session.add(
                    Notification(
                        user_id=user.id,
                        title=template.title,
                        message=template.message,
                        priority=template.priority,
                    )
                )
            session.commit()
    except Exception as e:
        LOG.warning("Background incident-resolved notifications failed: {}", e)

    try:
        from app.services.email import EmailService
        from app.utils.funcs import utcnow as _utcnow

        EmailService.send_incident_resolved(
            ref_no=ref_no or "N/A",
            site_name=site_name,
            technician_name=tech_name,
            severity=severity,
            resolved_at=_utcnow().strftime("%d %b %Y %H:%M UTC"),
            description=description,
        )
    except Exception as e:
        LOG.warning("Background incident-resolved email failed: {}", e)


class _IncidentService:
    def _assert_incident_owner_or_management(
        self,
        incident: Incident,
        session: Session,
        current_user: TokenData,
        action: str,
    ) -> None:
        if is_management(current_user):
            return

        technician_id = get_technician_id_for_user(current_user.user_id, session)
        if incident.technician_id != technician_id:
            raise ForbiddenException(
                f"You do not have permission to {action} this incident."
            )

    def incident_to_response(self, incident: Incident) -> IncidentResponse:
        user = incident.technician.user
        # Calculate num_attachments - attachments can be {files: [...]} or {}
        attachments = incident.attachments or {}
        num_attachments = (
            len(attachments.get("files", [])) if isinstance(attachments, dict) else 0
        )

        # Get client info
        client_name = ""
        if incident.client:
            client_name = incident.client.name

        incident_data = incident.model_dump()
        # Coerce None → "" for str fields that may be NULL for older rows
        incident_data["assigned_by_name"] = incident_data.get("assigned_by_name") or ""
        # Extract site GPS coordinates for map links (may be None if site has no location)
        coords = incident.site.get_coordinates() if incident.site else None

        return IncidentResponse(
            **incident_data,
            site_name=incident.site.name,
            site_region=incident.site.region if incident.site else None,
            site_type=incident.site.site_type if incident.site else None,
            site_geofence_radius=(
                incident.site.geofence_radius if incident.site else None
            ),
            site_latitude=coords[0] if coords else None,
            site_longitude=coords[1] if coords else None,
            technician_fullname=f"{user.name} {user.surname}",
            client_name=client_name,
            num_attachments=num_attachments,
        )

    def create_incident(
        self, data: IncidentCreate, session: Session, current_user: TokenData
    ) -> IncidentResponse:
        if not is_management(current_user):
            raise ForbiddenException(
                "Only NOC, managers, or admins can create incidents."
            )

        # Handle site
        statement = select(Site).where(
            Site.id == data.site_id,
            Site.deleted_at.is_(None),  # type: ignore
        )
        site: Site | None = session.exec(statement).first()
        if not site:
            raise NotFoundException("site not found")

        # Handle technician
        statement = select(Technician).where(
            Technician.id == data.technician_id,
            Technician.deleted_at.is_(None),  # type: ignore
        )
        technician: Technician | None = session.exec(statement).first()
        if not technician:
            raise NotFoundException("technician not found")

        # Look up assigning user name
        assigned_by_user_id = None
        assigned_by_name = None
        if current_user:
            assigner = session.get(User, current_user.user_id)
            if assigner:
                assigned_by_user_id = assigner.id
                assigned_by_name = f"{assigner.name} {assigner.surname}"

        # Auto-set start_time to now if not provided
        incident_data = data.model_dump()
        if not incident_data.get("start_time"):
            incident_data["start_time"] = utcnow()

        # Capture names before commit while relationships are loaded in the current transaction
        _tech_name = f"{technician.user.name} {technician.user.surname}"
        _site_name = site.name

        incident: Incident = Incident(
            **incident_data,
            site=site,
            technician=technician,
            assigned_by_user_id=assigned_by_user_id,
            assigned_by_name=assigned_by_name,
        )
        try:
            session.add(incident)
            session.commit()
            session.refresh(incident)
            return self.incident_to_response(incident)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating incident: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error creating incident: {e}"
            )

    def read_incident(
        self, incident_id: UUID, session: Session, current_user: TokenData
    ) -> IncidentResponse:
        incident = self._get_incident(incident_id, session)
        self._assert_incident_owner_or_management(
            incident, session, current_user, "view"
        )
        return self.incident_to_response(incident)

    def read_incidents(
        self,
        session: Session,
        current_user: TokenData,
        technician_id: UUID | None = None,
        status: IncidentStatus | None = None,
        client_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[IncidentResponse]:
        statement = (
            select(Incident)
            .options(
                selectinload(Incident.technician).selectinload(Technician.user),  # type: ignore
                selectinload(Incident.client),  # type: ignore
                selectinload(Incident.site),  # type: ignore
            )
            .where(Incident.deleted_at.is_(None))  # type: ignore
        )

        if is_management(current_user):
            if technician_id is not None:
                statement = statement.where(Incident.technician_id == technician_id)
        else:
            technician_id = get_technician_id_for_user(current_user.user_id, session)
            statement = statement.where(Incident.technician_id == technician_id)
        if status is not None:
            statement = statement.where(Incident.status == status)
        if client_id is not None:
            statement = statement.where(Incident.client_id == client_id)

        statement = statement.offset(offset).limit(limit)
        incidents = session.exec(statement).all()
        return [self.incident_to_response(incident) for incident in incidents]

    def update_incident(
        self,
        incident_id: UUID,
        data: IncidentUpdate,
        session: Session,
        current_user: TokenData,
    ) -> IncidentResponse:
        if not is_management(current_user):
            raise ForbiddenException(
                "Only NOC, managers, or admins can update incidents."
            )

        incident = self._get_incident(incident_id, session)
        update_data = data.model_dump(
            exclude_none=True, exclude_defaults=True, exclude_unset=True
        )

        if not update_data:
            return self.incident_to_response(incident)

        for k, v in update_data.items():
            setattr(incident, k, v)

        incident.touch()

        try:
            session.commit()
            session.refresh(incident)
            return self.incident_to_response(incident)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating incident: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error updating incident: {e}"
            )

    def delete_incident(
        self, incident_id: UUID, session: Session, current_user: TokenData
    ) -> None:
        if not is_management(current_user):
            raise ForbiddenException(
                "Only NOC, managers, or admins can delete incidents."
            )

        incident = self._get_incident(incident_id, session)
        incident.soft_delete()
        session.commit()

    def start_incident(
        self, incident_id: UUID, session: Session, current_user: TokenData
    ) -> IncidentResponse:
        """Start working on an incident."""
        incident = self._get_incident(incident_id, session)
        self._assert_incident_owner_or_management(
            incident, session, current_user, "start"
        )
        incident.start()
        try:
            session.commit()
            session.refresh(incident)
            return self.incident_to_response(incident)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error starting incident: {e}"
            )

    def resolve_incident(
        self, incident_id: UUID, session: Session, current_user: TokenData
    ) -> IncidentResponse:
        """Resolve an incident."""
        incident = self._get_incident(incident_id, session)
        self._assert_incident_owner_or_management(
            incident, session, current_user, "resolve"
        )
        incident.resolve()
        try:
            session.commit()
            session.refresh(incident)
            return self.incident_to_response(incident)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error resolving incident: {e}"
            )

    def mark_responded(
        self, incident_id: UUID, session: Session, current_user: TokenData
    ) -> IncidentResponse:
        incident = self._get_incident(incident_id, session)
        self._assert_incident_owner_or_management(
            incident, session, current_user, "update"
        )
        incident.mark_responded()
        session.commit()
        session.refresh(incident)
        return self.incident_to_response(incident)

    def mark_arrived_on_site(
        self, incident_id: UUID, session: Session, current_user: TokenData
    ) -> IncidentResponse:
        incident = self._get_incident(incident_id, session)
        self._assert_incident_owner_or_management(
            incident, session, current_user, "update"
        )
        incident.mark_arrived_on_site()
        session.commit()
        session.refresh(incident)
        return self.incident_to_response(incident)

    def mark_temporarily_restored(
        self, incident_id: UUID, session: Session, current_user: TokenData
    ) -> IncidentResponse:
        incident = self._get_incident(incident_id, session)
        self._assert_incident_owner_or_management(
            incident, session, current_user, "update"
        )
        incident.mark_temporarily_restored()
        session.commit()
        session.refresh(incident)
        return self.incident_to_response(incident)

    def mark_permanently_restored(
        self, incident_id: UUID, session: Session, current_user: TokenData
    ) -> IncidentResponse:
        incident = self._get_incident(incident_id, session)
        self._assert_incident_owner_or_management(
            incident, session, current_user, "update"
        )
        incident.mark_permanently_restored()
        session.commit()
        session.refresh(incident)
        return self.incident_to_response(incident)

    def _get_incident(self, incident_id: UUID, session: Session) -> Incident:
        statement = select(Incident).where(
            Incident.id == incident_id,
            Incident.deleted_at.is_(None),  # type: ignore
        )
        incident: Incident | None = session.exec(statement).first()
        if not incident:
            raise NotFoundException("incident not found")
        return incident


def get_incident_service() -> _IncidentService:
    return _IncidentService()


IncidentService = Annotated[_IncidentService, Depends(get_incident_service)]
