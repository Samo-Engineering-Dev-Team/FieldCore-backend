"""Service for creating, reading, updating and signing SHEQ checklist
submissions (SHEQ-CHECKLISTS-PLAN.md §5, §7, §8.2)."""

from datetime import date
from typing import Annotated, List
from uuid import UUID

from fastapi import Depends
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from app.exceptions.http import (
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import Site, Task, Technician, User
from app.models.auth import TokenData
from app.models.sheq_submission import (
    SheqSubmission,
    SheqSubmissionCreate,
    SheqSubmissionUpdate,
    SheqSubmissionResponse,
    SheqSignatureCreate,
)
from app.services.authorization import ADMIN_MANAGER_ROLES, require_management, require_sheq_read
from app.services.sheq_signature import build_signature_record, compute_data_hash
from app.services.sheq_summary import compute_sheq_summary
from app.services.sheq_validation import validate_sheq_submission
from app.utils.enums import SheqSignatureRole, SheqStatus, UserRole
from app.utils.funcs import utcnow


class _SheqSubmissionService:
    def _to_response(self, submission: SheqSubmission) -> SheqSubmissionResponse:
        technician_name = "Unknown Technician"
        if submission.technician and submission.technician.user:
            technician_name = (
                f"{submission.technician.user.name} {submission.technician.user.surname}"
            )
        num_attachments = sum(
            len(v) for v in (submission.attachments or {}).values() if isinstance(v, list)
        )
        return SheqSubmissionResponse(
            **submission.model_dump(),
            technician_fullname=technician_name,
            num_attachments=num_attachments,
            is_signed_off=submission.status == SheqStatus.SIGNED_OFF,
        )

    def _get_technician_by_user(self, user_id: UUID, session: Session) -> Technician:
        statement = select(Technician).where(
            Technician.user_id == user_id,
            Technician.deleted_at.is_(None),  # type: ignore
        )
        technician: Technician | None = session.exec(statement).first()
        if not technician:
            raise NotFoundException("technician profile not found for current user")
        return technician

    def _get_submission(self, submission_id: UUID, session: Session) -> SheqSubmission:
        statement = select(SheqSubmission).where(
            SheqSubmission.id == submission_id,
            SheqSubmission.deleted_at.is_(None),  # type: ignore
        )
        submission: SheqSubmission | None = session.exec(statement).first()
        if not submission:
            raise NotFoundException("SHEQ checklist submission not found")
        return submission

    def _assert_can_read(self, submission: SheqSubmission, current_user: TokenData, session: Session) -> None:
        if current_user.role == UserRole.TECHNICIAN:
            technician = self._get_technician_by_user(current_user.user_id, session)
            if submission.technician_id != technician.id:
                raise ForbiddenException("Technicians can only view their own SHEQ checklists")
            return
        require_sheq_read(current_user, "You do not have permission to view SHEQ checklists.")

    def _assert_can_write(
        self, submission: SheqSubmission, current_user: TokenData, session: Session, action: str
    ) -> None:
        if current_user.role in ADMIN_MANAGER_ROLES:
            return
        if current_user.role == UserRole.TECHNICIAN:
            technician = self._get_technician_by_user(current_user.user_id, session)
            if submission.technician_id != technician.id:
                raise ForbiddenException(f"Technicians can only {action} their own SHEQ checklists")
            return
        raise ForbiddenException(f"You do not have permission to {action} SHEQ checklists.")

    def create_submission(
        self,
        data: SheqSubmissionCreate,
        session: Session,
        current_user: TokenData,
    ) -> SheqSubmissionResponse:
        if current_user.role == UserRole.TECHNICIAN:
            technician = self._get_technician_by_user(current_user.user_id, session)
            technician_id = technician.id
        elif current_user.role in ADMIN_MANAGER_ROLES:
            if not data.technician_id:
                raise ForbiddenException("technician_id is required when creating a SHEQ checklist as management")
            technician_id = data.technician_id
        else:
            raise ForbiddenException("You do not have permission to create SHEQ checklists.")

        if data.performed_on > date.today():
            from app.exceptions.http import BadRequestException

            raise BadRequestException("performed_on cannot be in the future")

        # Drafts are an explicit escape hatch for a genuinely partial save;
        # the normal path (both clients POST once, fully filled — mobile
        # keeps drafts client-side per the DC/POP precedent) validates fully.
        if data.status != SheqStatus.DRAFT:
            validate_sheq_submission(data.checklist_type, data.data, data.attachments, signatures=[])

        summary = compute_sheq_summary(data.checklist_type, data.data, data.attachments, [])
        submitted_at = utcnow() if data.status != SheqStatus.DRAFT else None

        submission = SheqSubmission(
            checklist_type=data.checklist_type,
            performed_on=data.performed_on,
            technician_id=technician_id,
            task_id=data.task_id,
            site_id=data.site_id,
            data=data.data,
            attachments=data.attachments,
            summary=summary,
            signatures=[],
            status=data.status,
            submitted_at=submitted_at,
        )
        try:
            session.add(submission)
            session.commit()
            session.refresh(submission)

            if submission.status == SheqStatus.SUBMITTED:
                self._notify_submitted(submission, session)

            return self._to_response(submission)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating SHEQ checklist submission: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating SHEQ checklist submission: {e}")

    def _notify_submitted(self, submission: SheqSubmission, session: Session) -> None:
        """Notify management on submission for the two checklists that require
        sign-off (§7.4). No `supervisor` assignment relationship exists on
        Technician, so this broadcasts to management the same way
        `create_noc_notifications` broadcasts to NOC — see
        NotificationTemplates.sheq_checklist_submitted's docstring."""
        from app.models import Notification
        from app.services.notification import NotificationTemplates
        from app.utils.enums import SheqChecklistType

        if submission.checklist_type not in (
            SheqChecklistType.VEHICLE_DAILY,
            SheqChecklistType.DAILY_RISK_ASSESSMENT,
        ):
            return

        technician = session.get(Technician, submission.technician_id)
        technician_name = "Unknown Technician"
        if technician and technician.user:
            technician_name = f"{technician.user.name} {technician.user.surname}"

        template = NotificationTemplates.sheq_checklist_submitted(
            technician_name=technician_name,
            checklist_label=submission.checklist_type.value.replace("-", " ").title(),
        )
        management_users = session.exec(
            select(User).where(
                User.role.in_(ADMIN_MANAGER_ROLES),  # type: ignore[attr-defined]
                User.deleted_at.is_(None),  # type: ignore
            )
        ).all()
        for user in management_users:
            session.add(
                Notification(
                    user_id=user.id,
                    title=template.title,
                    message=template.message,
                    priority=template.priority,
                )
            )
        session.commit()

    def read_submission(
        self, submission_id: UUID, session: Session, current_user: TokenData
    ) -> SheqSubmissionResponse:
        submission = self._get_submission(submission_id, session)
        self._assert_can_read(submission, current_user, session)
        return self._to_response(submission)

    def get_submission_for_export(
        self, submission_id: UUID, session: Session, current_user: TokenData
    ) -> tuple[SheqSubmission, str, str]:
        submission = self._get_submission(submission_id, session)
        self._assert_can_read(submission, current_user, session)

        site_name = "N/A"
        if submission.site_id:
            site = session.get(Site, submission.site_id)
            if site:
                site_name = site.name

        task_ref = "N/A"
        if submission.task_id:
            task = session.get(Task, submission.task_id)
            if task and task.seacom_ref:
                task_ref = task.seacom_ref

        return submission, site_name, task_ref

    def read_submissions(
        self,
        session: Session,
        current_user: TokenData,
        checklist_type: str | None = None,
        status: SheqStatus | None = None,
        technician_id: UUID | None = None,
        site_id: UUID | None = None,
        task_id: UUID | None = None,
        performed_from: date | None = None,
        performed_to: date | None = None,
        offset: int = 0,
        limit: int = 1000,
    ) -> List[SheqSubmissionResponse]:
        statement = select(SheqSubmission).where(SheqSubmission.deleted_at.is_(None))  # type: ignore

        if current_user.role == UserRole.TECHNICIAN:
            technician = self._get_technician_by_user(current_user.user_id, session)
            statement = statement.where(SheqSubmission.technician_id == technician.id)
        else:
            require_sheq_read(current_user, "You do not have permission to view SHEQ checklists.")
            if technician_id is not None:
                statement = statement.where(SheqSubmission.technician_id == technician_id)

        if checklist_type is not None:
            statement = statement.where(SheqSubmission.checklist_type == checklist_type)
        if status is not None:
            statement = statement.where(SheqSubmission.status == status)
        if site_id is not None:
            statement = statement.where(SheqSubmission.site_id == site_id)
        if task_id is not None:
            statement = statement.where(SheqSubmission.task_id == task_id)
        if performed_from is not None:
            statement = statement.where(SheqSubmission.performed_on >= performed_from)
        if performed_to is not None:
            statement = statement.where(SheqSubmission.performed_on <= performed_to)

        statement = (
            statement.order_by(SheqSubmission.performed_on.desc())  # type: ignore[union-attr]
            .offset(offset)
            .limit(limit)
        )
        submissions = session.exec(statement).all()
        return [self._to_response(s) for s in submissions]

    def update_submission(
        self,
        submission_id: UUID,
        data: SheqSubmissionUpdate,
        session: Session,
        current_user: TokenData,
    ) -> SheqSubmissionResponse:
        submission = self._get_submission(submission_id, session)
        self._assert_can_write(submission, current_user, session, "update")

        if submission.status == SheqStatus.SIGNED_OFF:
            raise ConflictException("This SHEQ checklist has been signed off and can no longer be edited.")

        update_data = data.model_dump(exclude_unset=True, exclude_none=True)
        if not update_data:
            return self._to_response(submission)

        new_data = update_data.get("data", submission.data)
        new_attachments = update_data.get("attachments", submission.attachments)
        new_status = update_data.get("status", submission.status)

        if new_status == SheqStatus.SUBMITTED and submission.status != SheqStatus.SUBMITTED:
            validate_sheq_submission(
                submission.checklist_type, new_data, new_attachments, signatures=submission.signatures
            )
            update_data["submitted_at"] = utcnow()
        elif submission.status != SheqStatus.DRAFT or new_status != SheqStatus.DRAFT:
            # Any edit to an already-submitted checklist is re-validated too —
            # a PATCH must never leave a submitted row failing its own rules.
            validate_sheq_submission(
                submission.checklist_type, new_data, new_attachments, signatures=submission.signatures
            )

        for key in ("performed_on", "task_id", "site_id", "data", "attachments", "status"):
            if key in update_data:
                setattr(submission, key, update_data[key])
        if "submitted_at" in update_data:
            submission.submitted_at = update_data["submitted_at"]

        submission.summary = compute_sheq_summary(
            submission.checklist_type, submission.data, submission.attachments, submission.signatures
        )
        submission.touch()

        try:
            session.add(submission)
            session.commit()
            session.refresh(submission)

            if new_status == SheqStatus.SUBMITTED and update_data.get("submitted_at"):
                self._notify_submitted(submission, session)

            return self._to_response(submission)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating SHEQ checklist submission: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating SHEQ checklist submission: {e}")

    def delete_submission(
        self, submission_id: UUID, session: Session, current_user: TokenData
    ) -> None:
        submission = self._get_submission(submission_id, session)
        self._assert_can_write(submission, current_user, session, "delete")
        if submission.status == SheqStatus.SIGNED_OFF:
            raise ConflictException("This SHEQ checklist has been signed off and can no longer be deleted.")
        submission.soft_delete()
        session.add(submission)
        session.commit()

    def add_signature(
        self,
        submission_id: UUID,
        payload: SheqSignatureCreate,
        session: Session,
        current_user: TokenData,
        ip_address: str | None,
        user_agent: str | None,
    ) -> SheqSubmissionResponse:
        submission = self._get_submission(submission_id, session)

        if submission.status == SheqStatus.SIGNED_OFF:
            raise ConflictException("This SHEQ checklist has already been signed off.")

        if payload.role == SheqSignatureRole.SUPERVISOR:
            require_management(current_user, "Only management can countersign a SHEQ checklist.")
            signer_user_id = str(current_user.user_id)
            signer_name = f"{current_user.name or ''} {current_user.surname or ''}".strip()
        else:
            # technician / driver / employee — the submitting technician (or
            # management, on their behalf) signs; roster "employee" rows are
            # typed by the supervisor conducting the toolbox talk (§7.5).
            self._assert_can_write(submission, current_user, session, "sign")
            if payload.role == SheqSignatureRole.EMPLOYEE:
                signer_user_id = None
                signer_name = payload.typed_name or ""
            else:
                signer_user_id = str(current_user.user_id)
                signer_name = f"{current_user.name or ''} {current_user.surname or ''}".strip()

        now = utcnow()
        record = build_signature_record(
            role=payload.role,
            method=payload.method,
            captured_at=payload.captured_at,
            signed_at=now,
            data_hash=compute_data_hash(submission.data),
            roster_index=payload.roster_index,
            file_ref=payload.file_ref,
            typed_name=payload.typed_name,
            signer_user_id=signer_user_id,
            signer_name=signer_name,
            offline_captured=payload.offline_captured,
            device=payload.device,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        # Reassign (not mutate in place) so SQLAlchemy's JSONB change
        # tracking picks up the new list on commit.
        submission.signatures = [*submission.signatures, record]

        if payload.role == SheqSignatureRole.SUPERVISOR:
            submission.sign_off(current_user.user_id, now)

        submission.summary = compute_sheq_summary(
            submission.checklist_type, submission.data, submission.attachments, submission.signatures
        )
        submission.touch()

        try:
            session.add(submission)
            session.commit()
            session.refresh(submission)
            return self._to_response(submission)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error recording signature: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error recording signature: {e}")


def get_sheq_submission_service() -> _SheqSubmissionService:
    return _SheqSubmissionService()


SheqSubmissionService = Annotated[_SheqSubmissionService, Depends(get_sheq_submission_service)]
