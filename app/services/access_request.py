from uuid import UUID
from fastapi import Depends
from typing import List, Annotated
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_

from app.utils.enums import AccessRequestStatus, UserRole
from app.models import (
    AccessRequest,
    AccessRequestCreate,
    AccessRequestUpdate,
    AccessRequestResponse,
    Site,
    Technician,
    User,
    )
from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
)


class _AccessRequestService:
    def access_request_to_response(self, access_request: AccessRequest) -> AccessRequestResponse:
        user = access_request.technician.user
        data = access_request.model_dump(exclude={'report_type'})
        return AccessRequestResponse(
            **data,
            report_type=access_request.report_type or "general",
            technician_name=f"{user.name} {user.surname}",
            technician_id_no=access_request.technician.id_no,
            site_name=access_request.site.name
            )

    def create_access_request(self, data: AccessRequestCreate, session: Session) -> AccessRequestResponse:
        # Handle site
        statement = select(Site).where(Site.id == data.site_id, Site.deleted_at.is_(None)) # type: ignore
        site: Site | None = session.exec(statement).first()
        if not site:
            raise NotFoundException("site not found")
        
        # Handle technician
        statement = select(Technician).where(Technician.id == data.technician_id, Technician.deleted_at.is_(None)) # type: ignore
        technician: Technician | None = session.exec(statement).first()
        if not technician:
            raise NotFoundException("technician not found")

        access_request: AccessRequest = AccessRequest(**data.model_dump(), site=site, technician=technician)
        try:
            session.add(access_request)
            session.commit()
            session.refresh(access_request)
            
            # Notify all NOC operators about new access request
            from app.services.notification import _NotificationService, NotificationTemplates
            notification_service = _NotificationService()
            
            noc_users = session.exec(
                select(User).where(
                    and_(
                        User.role == UserRole.NOC,
                        User.deleted_at.is_(None)
                    )
                )
            ).all()
            
            tech_name = f"{technician.user.name} {technician.user.surname}"
            notification_service.create_notifications_from_template(
                user_ids=(noc_user.id for noc_user in noc_users),
                template=NotificationTemplates.access_request_created(
                    site_name=site.name,
                    technician_name=tech_name,
                    description=data.description,
                ),
                session=session,
            )
            
            return self.access_request_to_response(access_request)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating access-request: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating access-request: {e}")

    def read_access_request(self, access_request_id: UUID, session: Session) -> AccessRequestResponse:
        access_request = self._get_access_request(access_request_id, session)
        return self.access_request_to_response(access_request)

    def read_access_requests(
        self,
        session: Session,
        status: AccessRequestStatus | None = None,
        technician_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[AccessRequestResponse]:
        statement = select(AccessRequest).where(AccessRequest.deleted_at.is_(None))  # type: ignore

        if status is not None:
            statement = statement.where(AccessRequest.status == status)
        if technician_id is not None:
            statement = statement.where(AccessRequest.technician_id == technician_id)

        statement = statement.offset(offset).limit(limit)
        access_requests = session.exec(statement).all()
        return [self.access_request_to_response(access_request) for access_request in access_requests]

    def update_access_request(
        self, access_request_id: UUID, data: AccessRequestUpdate, session: Session
    ) -> AccessRequestResponse:
        access_request = self._get_access_request(access_request_id, session)
        update_data = data.model_dump(
            exclude_none=True, exclude_defaults=True, exclude_unset=True
        )

        if not update_data:
            return self.access_request_to_response(access_request)

        for k, v in update_data.items():
            setattr(access_request, k, v)

        access_request.touch()

        try:
            session.commit()
            session.refresh(access_request)
            return self.access_request_to_response(access_request)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating access_request: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating access_request: {e}")

    def delete_access_request(self, access_request_id: UUID, session: Session) -> None:
        access_request = self._get_access_request(access_request_id, session)
        access_request.soft_delete()
        session.commit()
    
    def approve_access_request(self, access_request_id: UUID, seacom_ref: str, session: Session) -> AccessRequestResponse:
        """Approve an access request, update related task with seacom_ref, and notify the technician."""
        from app.models import Task
        
        access_request = self._get_access_request(access_request_id, session)
        access_request.approve(seacom_ref)
        try:
            session.commit()
            session.refresh(access_request)
            
            # Update the related task with the seacom_ref if it exists
            if access_request.task_id:
                task = session.exec(
                    select(Task).where(Task.id == access_request.task_id, Task.deleted_at.is_(None))
                ).first()
                if task:
                    task.seacom_ref = seacom_ref
                    task.touch()
                    session.commit()
                    session.refresh(task)
            
            # Notify the technician that their access request was approved
            from app.services.notification import _NotificationService, NotificationTemplates
            notification_service = _NotificationService()
            
            site_name = access_request.site.name if access_request.site else "Unknown Site"
            
            notification_service.create_notification_from_template(
                user_id=access_request.technician.user_id,
                template=NotificationTemplates.access_request_approved(site_name, seacom_ref),
                session=session,
            )
            
            return self.access_request_to_response(access_request)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error approving access-request: {e}")
    
    def reject_access_request(self, access_request_id: UUID, session: Session) -> AccessRequestResponse:
        """Reject an access request and notify the technician."""
        access_request = self._get_access_request(access_request_id, session)
        access_request.reject()
        try:
            session.commit()
            session.refresh(access_request)
            
            # Notify the technician that their access request was rejected
            from app.services.notification import _NotificationService, NotificationTemplates
            notification_service = _NotificationService()
            
            site_name = access_request.site.name if access_request.site else "Unknown Site"
            
            notification_service.create_notification_from_template(
                user_id=access_request.technician.user_id,
                template=NotificationTemplates.access_request_rejected(site_name),
                session=session,
            )
            
            return self.access_request_to_response(access_request)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error rejecting access-request: {e}")

    def _get_access_request(self, access_request_id: UUID, session: Session) -> AccessRequest:
        statement = select(AccessRequest).where(AccessRequest.id == access_request_id, AccessRequest.deleted_at.is_(None))  # type: ignore
        access_request: AccessRequest | None = session.exec(statement).first()
        if not access_request:
            raise NotFoundException("access-request not found")
        return access_request


def get_access_request_service() -> _AccessRequestService:
    return _AccessRequestService()


AccessRequestService = Annotated[_AccessRequestService, Depends(get_access_request_service)]
