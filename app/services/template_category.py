"""Service for managing dynamic template categories (the form "type IDs")."""

from uuid import UUID
from typing import Annotated, List

from fastapi import Depends
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from app.exceptions.http import (
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import (
    TemplateCategory,
    TemplateCategoryCreate,
    TemplateCategoryUpdate,
    TemplateCategoryResponse,
)
from app.models.auth import TokenData
from app.services.authorization import require_management


class _TemplateCategoryService:
    def _to_response(self, category: TemplateCategory) -> TemplateCategoryResponse:
        return TemplateCategoryResponse.model_validate(category.model_dump())

    def _get_category(self, category_id: UUID, session: Session) -> TemplateCategory:
        statement = select(TemplateCategory).where(
            TemplateCategory.id == category_id,
            TemplateCategory.deleted_at.is_(None),  # type: ignore
        )
        category: TemplateCategory | None = session.exec(statement).first()
        if not category:
            raise NotFoundException("template category not found")
        return category

    def create_category(
        self,
        data: TemplateCategoryCreate,
        session: Session,
        current_user: TokenData,
    ) -> TemplateCategoryResponse:
        require_management(current_user, "Only management can create template categories")

        category = TemplateCategory(
            code=data.code,
            name=data.name,
            description=data.description,
            requires_link=data.requires_link,
            is_active=data.is_active,
            is_system=False,
        )
        try:
            session.add(category)
            session.commit()
            session.refresh(category)
            return self._to_response(category)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating template category: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating template category: {e}")

    def read_categories(
        self,
        session: Session,
        current_user: TokenData,
        active_only: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> List[TemplateCategoryResponse]:
        statement = select(TemplateCategory).where(
            TemplateCategory.deleted_at.is_(None),  # type: ignore
        )
        if active_only:
            statement = statement.where(TemplateCategory.is_active.is_(True))  # type: ignore
        statement = statement.offset(offset).limit(limit)
        categories = session.exec(statement).all()
        return [self._to_response(c) for c in categories]

    def read_category(
        self,
        category_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> TemplateCategoryResponse:
        return self._to_response(self._get_category(category_id, session))

    def update_category(
        self,
        category_id: UUID,
        data: TemplateCategoryUpdate,
        session: Session,
        current_user: TokenData,
    ) -> TemplateCategoryResponse:
        require_management(current_user, "Only management can update template categories")
        category = self._get_category(category_id, session)

        if data.name is not None:
            category.name = data.name
        if data.description is not None:
            category.description = data.description
        if data.requires_link is not None:
            category.requires_link = data.requires_link
        if data.is_active is not None:
            category.is_active = data.is_active

        category.touch()
        try:
            session.add(category)
            session.commit()
            session.refresh(category)
            return self._to_response(category)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating template category: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating template category: {e}")

    def delete_category(
        self,
        category_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> None:
        require_management(current_user, "Only management can delete template categories")
        category = self._get_category(category_id, session)
        if category.is_system:
            raise ForbiddenException("Built-in categories cannot be deleted")
        category.soft_delete()
        try:
            session.add(category)
            session.commit()
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error deleting template category: {e}")


def get_template_category_service() -> _TemplateCategoryService:
    return _TemplateCategoryService()


TemplateCategoryService = Annotated[_TemplateCategoryService, Depends(get_template_category_service)]
