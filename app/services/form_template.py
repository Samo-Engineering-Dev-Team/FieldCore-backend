"""Service for managing dynamic form templates."""

from uuid import UUID
from typing import Annotated, List

from fastapi import Depends
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import (
    FormTemplate,
    FormTemplateCreate,
    FormTemplateUpdate,
    FormTemplateResponse,
    TemplateCategory,
)
from app.models.auth import TokenData
from app.services.authorization import require_management


class _FormTemplateService:
    def _to_response(self, template: FormTemplate) -> FormTemplateResponse:
        return FormTemplateResponse.model_validate(template.model_dump())

    def _get_template(self, template_id: UUID, session: Session) -> FormTemplate:
        statement = select(FormTemplate).where(
            FormTemplate.id == template_id,
            FormTemplate.deleted_at.is_(None),  # type: ignore
        )
        template: FormTemplate | None = session.exec(statement).first()
        if not template:
            raise NotFoundException("form template not found")
        return template

    def create_template(
        self,
        data: FormTemplateCreate,
        session: Session,
        current_user: TokenData,
    ) -> FormTemplateResponse:
        require_management(current_user, "Only management can create form templates")

        # Category must exist and be active before a template can reference it.
        category = session.exec(
            select(TemplateCategory).where(
                TemplateCategory.id == data.category_id,
                TemplateCategory.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if not category:
            raise NotFoundException("template category not found")
        if not category.is_active:
            raise ConflictException("template category is not active")

        template = FormTemplate(
            category_id=data.category_id,
            key=data.key,
            name=data.name,
            description=data.description,
            is_active=data.is_active,
            version=1,
            structure=data.structure.model_dump(),
        )
        try:
            session.add(template)
            session.commit()
            session.refresh(template)
            return self._to_response(template)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating form template: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating form template: {e}")

    def read_templates(
        self,
        session: Session,
        current_user: TokenData,
        active_only: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> List[FormTemplateResponse]:
        statement = select(FormTemplate).where(
            FormTemplate.deleted_at.is_(None),  # type: ignore
        )
        if active_only:
            statement = statement.where(FormTemplate.is_active.is_(True))  # type: ignore
        statement = statement.offset(offset).limit(limit)
        templates = session.exec(statement).all()
        return [self._to_response(t) for t in templates]

    def read_template(
        self,
        template_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> FormTemplateResponse:
        return self._to_response(self._get_template(template_id, session))

    def update_template(
        self,
        template_id: UUID,
        data: FormTemplateUpdate,
        session: Session,
        current_user: TokenData,
    ) -> FormTemplateResponse:
        require_management(current_user, "Only management can update form templates")
        template = self._get_template(template_id, session)

        if data.name is not None:
            template.name = data.name
        if data.description is not None:
            template.description = data.description
        if data.is_active is not None:
            template.is_active = data.is_active
        if data.structure is not None:
            # Structural change -> snapshot a new version.
            template.structure = data.structure.model_dump()
            template.version += 1

        template.touch()
        try:
            session.add(template)
            session.commit()
            session.refresh(template)
            return self._to_response(template)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating form template: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating form template: {e}")

    def delete_template(
        self,
        template_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> None:
        require_management(current_user, "Only management can delete form templates")
        template = self._get_template(template_id, session)
        template.soft_delete()
        try:
            session.add(template)
            session.commit()
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error deleting form template: {e}")


def get_form_template_service() -> _FormTemplateService:
    return _FormTemplateService()


FormTemplateService = Annotated[_FormTemplateService, Depends(get_form_template_service)]
