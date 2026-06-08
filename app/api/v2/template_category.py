from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models import (
    TemplateCategoryCreate,
    TemplateCategoryUpdate,
    TemplateCategoryResponse,
)
from app.services import TemplateCategoryService, CurrentUser
from app.database import Session

router = APIRouter(prefix="/template-categories", tags=["Template Categories"])


@router.post("/", response_model=TemplateCategoryResponse, status_code=201)
def create_template_category(
    payload: TemplateCategoryCreate,
    service: TemplateCategoryService,
    session: Session,
    current_user: CurrentUser,
) -> TemplateCategoryResponse:
    """Create a new dynamic template category (the form 'type ID')."""
    return service.create_category(payload, session, current_user)


@router.get("/", response_model=List[TemplateCategoryResponse], status_code=200)
def read_template_categories(
    service: TemplateCategoryService,
    session: Session,
    current_user: CurrentUser,
    active_only: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[TemplateCategoryResponse]:
    """List template categories so the frontend can pick one when creating a template."""
    return service.read_categories(session, current_user, active_only, offset, limit)


@router.get("/{category_id}", response_model=TemplateCategoryResponse, status_code=200)
def read_template_category(
    category_id: UUID,
    service: TemplateCategoryService,
    session: Session,
    current_user: CurrentUser,
) -> TemplateCategoryResponse:
    """Retrieve a single template category."""
    return service.read_category(category_id, session, current_user)


@router.patch("/{category_id}", response_model=TemplateCategoryResponse, status_code=200)
def update_template_category(
    category_id: UUID,
    payload: TemplateCategoryUpdate,
    service: TemplateCategoryService,
    session: Session,
    current_user: CurrentUser,
) -> TemplateCategoryResponse:
    """Update a template category. `code` is immutable."""
    return service.update_category(category_id, payload, session, current_user)


@router.delete("/{category_id}", status_code=204)
def delete_template_category(
    category_id: UUID,
    service: TemplateCategoryService,
    session: Session,
    current_user: CurrentUser,
) -> None:
    """Soft-delete a template category. Built-in (is_system) categories cannot be deleted."""
    service.delete_category(category_id, session, current_user)
