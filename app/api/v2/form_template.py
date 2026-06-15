from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models import (
    FormTemplateCreate,
    FormTemplateUpdate,
    FormTemplateResponse,
)
from app.services import FormTemplateService, CurrentUser
from app.database import SessionDep as Session

router = APIRouter(prefix="/form-templates", tags=["Form Templates"])


@router.post("/", response_model=FormTemplateResponse, status_code=201)
def create_form_template(
    payload: FormTemplateCreate,
    service: FormTemplateService,
    session: Session,
    current_user: CurrentUser,
) -> FormTemplateResponse:
    """Create a form template. Requires `category_id` (see /template-categories)."""
    return service.create_template(payload, session, current_user)


@router.get("/", response_model=List[FormTemplateResponse], status_code=200)
def read_form_templates(
    service: FormTemplateService,
    session: Session,
    current_user: CurrentUser,
    active_only: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[FormTemplateResponse]:
    """List form templates so the frontend can render a form to fill out."""
    return service.read_templates(session, current_user, active_only, offset, limit)


@router.get("/{template_id}", response_model=FormTemplateResponse, status_code=200)
def read_form_template(
    template_id: UUID,
    service: FormTemplateService,
    session: Session,
    current_user: CurrentUser,
) -> FormTemplateResponse:
    """Retrieve a single form template."""
    return service.read_template(template_id, session, current_user)


@router.patch("/{template_id}", response_model=FormTemplateResponse, status_code=200)
def update_form_template(
    template_id: UUID,
    payload: FormTemplateUpdate,
    service: FormTemplateService,
    session: Session,
    current_user: CurrentUser,
) -> FormTemplateResponse:
    """Update a form template. Supplying a new structure bumps its version."""
    return service.update_template(template_id, payload, session, current_user)


@router.delete("/{template_id}", status_code=204)
def delete_form_template(
    template_id: UUID,
    service: FormTemplateService,
    session: Session,
    current_user: CurrentUser,
) -> None:
    """Soft-delete a form template."""
    service.delete_template(template_id, session, current_user)
