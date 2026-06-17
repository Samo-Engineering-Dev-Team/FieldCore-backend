from typing import Annotated, List
from uuid import UUID

from fastapi import Depends
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.exceptions.http import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import (
    AccessRequest,
    AccessRequestCreate,
    AccessRequestResponse,
    AccessRequestUpdate,
    Site,
    Technician,
    User,
)
from app.models.auth import TokenData
from app.services.authorization import get_technician_id_for_user, is_management
from app.utils.enums import AccessRequestStatus, TaskType, UserRole


class _AccessRequestService:
    def access_request_to_response(
        self, access_request: AccessRequest
    ) -> AccessRequestResponse:
        user = access_request.technician.user
        data = access_request.model_dump(exclude={"report_type"})
        return AccessRequestResponse(
            **data,
            report_type=access_request.report_type or "general",
            technician_name=f"{user.name} {user.surname}",
            technician_id_no=access_request.technician.id_no,
            site_name=access_request.site.name,
        )

    def _assert_can_access_request(
        self,
        access_request: AccessRequest,
        session: Session,
        current_user: TokenData,
        action: str,
    ) -> None:
        if is_management(current_user):
            return

        technician_id = get_technician_id_for_user(current_user.user_id, session)
        if access_request.technician_id != technician_id:
            raise ForbiddenException(
                f"You do not have permission to {action} this access request."
            )

    def create_access_request(
        self,
        data: AccessRequestCreate,
        session: Session,
        current_user: TokenData,
    ) -> AccessRequestResponse:
        technician_id = data.technician_id
        if current_user.role == UserRole.TECHNICIAN:
            technician_id = get_technician_id_for_user(current_user.user_id, session)
            if data.technician_id is not None and data.technician_id != technician_id:
                raise ForbiddenException(
                    "Technicians can only create access requests for themselves."
                )
        elif not is_management(current_user):
            raise ForbiddenException(
                "Only technicians, NOC, managers, or admins can create access requests."
            )
        elif technician_id is None:
            raise BadRequestException("technician_id is required")

        data = data.model_copy(update={"technician_id": technician_id})

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

        access_request: AccessRequest = AccessRequest(
            **data.model_dump(), site=site, technician=technician
        )
        try:
            session.add(access_request)
            session.commit()
            session.refresh(access_request)

            # Notify all NOC operators about new access request
            from app.services.notification import (
                NotificationTemplates,
                _NotificationService,
            )

            notification_service = _NotificationService()

            noc_users = session.exec(
                select(User).where(
                    User.role == UserRole.NOC,
                    User.deleted_at.is_(None),  # type: ignore
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
            raise InternalServerErrorException(
                f"Unexpected error creating access-request: {e}"
            )

    def read_access_request(
        self,
        access_request_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> AccessRequestResponse:
        access_request = self._get_access_request(access_request_id, session)
        self._assert_can_access_request(access_request, session, current_user, "view")
        return self.access_request_to_response(access_request)

    def read_access_requests(
        self,
        session: Session,
        current_user: TokenData,
        status: AccessRequestStatus | None = None,
        technician_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[AccessRequestResponse]:
        statement = select(AccessRequest).where(AccessRequest.deleted_at.is_(None))  # type: ignore

        if current_user.role == UserRole.TECHNICIAN:
            technician_id = get_technician_id_for_user(current_user.user_id, session)

        if status is not None:
            statement = statement.where(AccessRequest.status == status)
        if technician_id is not None:
            statement = statement.where(AccessRequest.technician_id == technician_id)

        statement = statement.offset(offset).limit(limit)
        access_requests = session.exec(statement).all()
        return [
            self.access_request_to_response(access_request)
            for access_request in access_requests
        ]

    def update_access_request(
        self,
        access_request_id: UUID,
        data: AccessRequestUpdate,
        session: Session,
        current_user: TokenData,
    ) -> AccessRequestResponse:
        access_request = self._get_access_request(access_request_id, session)
        self._assert_can_access_request(access_request, session, current_user, "update")
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
            raise InternalServerErrorException(
                f"Unexpected error updating access_request: {e}"
            )

    def delete_access_request(
        self,
        access_request_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> None:
        access_request = self._get_access_request(access_request_id, session)
        self._assert_can_access_request(access_request, session, current_user, "delete")
        access_request.soft_delete()
        session.commit()

    def approve_access_request(
        self,
        access_request_id: UUID,
        seacom_ref: str,
        session: Session,
        current_user: TokenData,
    ) -> AccessRequestResponse:
        """Approve an access request, create (or update) its work task, and notify the technician."""
        from app.models import Task

        access_request = self._get_access_request(access_request_id, session)
        access_request.approve(seacom_ref)
        try:
            session.commit()
            session.refresh(access_request)

            if access_request.task_id:
                # Legacy requests that already carry a task: propagate the seacom_ref
                task = session.exec(
                    select(Task).where(
                        Task.id == access_request.task_id,
                        Task.deleted_at.is_(None),  # type: ignore
                    )
                ).first()
                if task:
                    task.seacom_ref = seacom_ref
                    task.touch()
                    session.commit()
                    session.refresh(task)
            else:
                # Create the work task for the approved request
                assigner = session.get(User, current_user.user_id)
                task = Task(
                    seacom_ref=seacom_ref,
                    description=access_request.description,
                    start_time=access_request.start_time,
                    end_time=access_request.end_time,
                    task_type=TaskType.ROUTINE_MAINTENANCE,
                    report_type=access_request.report_type or "general",
                    site_id=access_request.site_id,
                    technician_id=access_request.technician_id,
                    assigned_by_user_id=assigner.id if assigner else None,
                    assigned_by_name=f"{assigner.name} {assigner.surname}" if assigner else None,
                )
                session.add(task)
                session.commit()
                session.refresh(task)

                access_request.task_id = task.id
                access_request.touch()
                session.commit()
                session.refresh(access_request)

            # Notify the technician that their access request was approved
            from app.services.notification import (
                NotificationTemplates,
                _NotificationService,
            )

            notification_service = _NotificationService()

            site_name = (
                access_request.site.name if access_request.site else "Unknown Site"
            )

            notification_service.create_notification_from_template(
                user_id=access_request.technician.user_id,
                template=NotificationTemplates.access_request_approved(
                    site_name, seacom_ref
                ),
                session=session,
            )

            return self.access_request_to_response(access_request)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error approving access-request: {e}"
            )

    def reject_access_request(
        self, access_request_id: UUID, session: Session
    ) -> AccessRequestResponse:
        """Reject an access request and notify the technician."""
        access_request = self._get_access_request(access_request_id, session)
        access_request.reject()
        try:
            session.commit()
            session.refresh(access_request)

            # Notify the technician that their access request was rejected
            from app.services.notification import (
                NotificationTemplates,
                _NotificationService,
            )

            notification_service = _NotificationService()

            site_name = (
                access_request.site.name if access_request.site else "Unknown Site"
            )

            notification_service.create_notification_from_template(
                user_id=access_request.technician.user_id,
                template=NotificationTemplates.access_request_rejected(site_name),
                session=session,
            )

            return self.access_request_to_response(access_request)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error rejecting access-request: {e}"
            )

    def _get_access_request(
        self, access_request_id: UUID, session: Session
    ) -> AccessRequest:
        statement = select(AccessRequest).where(
            AccessRequest.id == access_request_id,
            AccessRequest.deleted_at.is_(None),  # type: ignore
        )
        access_request: AccessRequest | None = session.exec(statement).first()
        if not access_request:
            raise NotFoundException("access-request not found")
        return access_request


def get_access_request_service() -> _AccessRequestService:
    return _AccessRequestService()


AccessRequestService = Annotated[
    _AccessRequestService, Depends(get_access_request_service)
]
