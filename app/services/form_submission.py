"""Service for creating and reading dynamic form submissions."""

from uuid import UUID
from typing import Annotated, List

from fastapi import Depends
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from app.exceptions.http import (
    BadRequestException,
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
    ForbiddenException,
)
from app.models import (
    FormTemplate,
    FormSubmission,
    FormSubmissionCreate,
    FormSubmissionResponse,
    TemplateCategory,
)
from app.models.incident import Incident
from app.models.task import Task
from app.models.auth import TokenData
from app.services.authorization import is_management
from app.services.form_validation import validate_submission
from app.utils.enums import LinkTarget, UserRole


class _FormSubmissionService:
    def _to_response(self, submission: FormSubmission) -> FormSubmissionResponse:
        return FormSubmissionResponse.model_validate(submission.model_dump())

    def _get_active_template(self, template_id: UUID, session: Session) -> FormTemplate:
        statement = select(FormTemplate).where(
            FormTemplate.id == template_id,
            FormTemplate.deleted_at.is_(None),  # type: ignore
        )
        template: FormTemplate | None = session.exec(statement).first()
        if not template:
            raise NotFoundException("form template not found")
        if not template.is_active:
            raise ConflictException("form template is not active")
        return template

    def _get_submission(self, submission_id: UUID, session: Session) -> FormSubmission:
        statement = select(FormSubmission).where(
            FormSubmission.id == submission_id,
            FormSubmission.deleted_at.is_(None),  # type: ignore
        )
        submission: FormSubmission | None = session.exec(statement).first()
        if not submission:
            raise NotFoundException("form submission not found")
        return submission

    def _resolve_link(
        self,
        template: FormTemplate,
        data: FormSubmissionCreate,
        session: Session,
    ) -> tuple[UUID | None, UUID | None]:
        """
        Enforce the template category's required domain link. Returns the
        (task_id, incident_id) pair to persist. Raises 400 on mismatch and
        404 if the referenced object does not exist.
        """
        category: TemplateCategory | None = session.get(TemplateCategory, template.category_id)
        if not category:
            raise NotFoundException("template category not found")

        requires = category.requires_link

        if requires == LinkTarget.TASK:
            if not data.task_id:
                raise BadRequestException("task_id is required for this template category")
            task = session.get(Task, data.task_id)
            if not task or task.deleted_at is not None:
                raise NotFoundException("task not found")
            return data.task_id, None

        if requires == LinkTarget.INCIDENT:
            if not data.incident_id:
                raise BadRequestException("incident_id is required for this template category")
            incident = session.get(Incident, data.incident_id)
            if not incident or incident.deleted_at is not None:
                raise NotFoundException("incident not found")
            return None, data.incident_id

        # LinkTarget.NONE — links are not accepted for this category.
        if data.task_id or data.incident_id:
            raise BadRequestException("this template category does not accept a domain link")
        return None, None

    def _assert_can_access(self, submission: FormSubmission, current_user: TokenData) -> None:
        if is_management(current_user):
            return
        if submission.submitted_by != current_user.user_id:
            raise ForbiddenException("You can only access your own submissions")

    def create_submission(
        self,
        template_id: UUID,
        data: FormSubmissionCreate,
        session: Session,
        current_user: TokenData,
    ) -> FormSubmissionResponse:
        template = self._get_active_template(template_id, session)
        structure = template.structure_model()

        # Enforce the category's required domain link (task/incident/none).
        task_id, incident_id = self._resolve_link(template, data, session)

        # Raises FormValidationException (422) with a per-field error map.
        coerced_values, coerced_attachments = validate_submission(
            structure, data.values, data.attachments
        )

        submission = FormSubmission(
            template_id=template.id,
            task_id=task_id,
            incident_id=incident_id,
            template_version=template.version,
            template_snapshot=template.structure,
            values=coerced_values,
            attachments=coerced_attachments or None,
            submitted_by=current_user.user_id,
        )
        try:
            session.add(submission)
            session.commit()
            session.refresh(submission)
            return self._to_response(submission)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating form submission: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating form submission: {e}")

    def read_submissions(
        self,
        template_id: UUID,
        session: Session,
        current_user: TokenData,
        offset: int = 0,
        limit: int = 100,
    ) -> List[FormSubmissionResponse]:
        statement = select(FormSubmission).where(
            FormSubmission.template_id == template_id,
            FormSubmission.deleted_at.is_(None),  # type: ignore
        )
        # Technicians are scoped to their own submissions.
        if current_user.role == UserRole.TECHNICIAN:
            statement = statement.where(
                FormSubmission.submitted_by == current_user.user_id
            )
        statement = statement.offset(offset).limit(limit)
        submissions = session.exec(statement).all()
        return [self._to_response(s) for s in submissions]

    def read_submission(
        self,
        template_id: UUID,
        submission_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> FormSubmissionResponse:
        submission = self._get_submission(submission_id, session)
        if submission.template_id != template_id:
            raise NotFoundException("form submission not found")
        self._assert_can_access(submission, current_user)
        return self._to_response(submission)


def get_form_submission_service() -> _FormSubmissionService:
    return _FormSubmissionService()


FormSubmissionService = Annotated[_FormSubmissionService, Depends(get_form_submission_service)]
