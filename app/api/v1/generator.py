"""Generator asset-register endpoints.

Reads are open to any authenticated user; writes are management-only
(super_admin, admin, manager, noc) and enforced in the service layer, not here.
"""

from uuid import UUID

from fastapi import APIRouter, Query

from app.database import SessionDep
from app.models import (
    GeneratorAssignSite,
    GeneratorCreate,
    GeneratorResponse,
    GeneratorUpdate,
)
from app.services import CurrentUser
from app.services.generator import GeneratorService

router = APIRouter(prefix="/generators", tags=["Generators"])


@router.get("/", response_model=list[GeneratorResponse], status_code=200)
def read_generators(
    service: GeneratorService,
    session: SessionDep,
    current_user: CurrentUser,
    site_id: UUID | None = Query(None, description="Filter to one site"),
    unassigned: bool = Query(
        False, description="Only units not assigned to any site (ignored with site_id)"
    ),
    include_inactive: bool = Query(False, description="Include decommissioned units"),
) -> list[GeneratorResponse]:
    """Readable by any authenticated user — a technician needs this to pick the
    unit they are refuelling."""
    return service.read_generators(session, site_id, unassigned, include_inactive)


@router.post("/", response_model=GeneratorResponse, status_code=201)
def create_generator(
    payload: GeneratorCreate,
    service: GeneratorService,
    session: SessionDep,
    current_user: CurrentUser,
) -> GeneratorResponse:
    """Register a unit. A site is optional — a generator may exist unassigned."""
    return service.create_generator(payload, session, current_user)


@router.get("/{generator_id}", response_model=GeneratorResponse, status_code=200)
def read_generator(
    generator_id: UUID,
    service: GeneratorService,
    session: SessionDep,
    current_user: CurrentUser,
) -> GeneratorResponse:
    return service.read_generator(generator_id, session)


@router.patch("/{generator_id}", response_model=GeneratorResponse, status_code=200)
def update_generator(
    generator_id: UUID,
    payload: GeneratorUpdate,
    service: GeneratorService,
    session: SessionDep,
    current_user: CurrentUser,
) -> GeneratorResponse:
    return service.update_generator(generator_id, payload, session, current_user)


@router.patch(
    "/{generator_id}/assign-site", response_model=GeneratorResponse, status_code=200
)
def assign_generator_site(
    generator_id: UUID,
    payload: GeneratorAssignSite,
    service: GeneratorService,
    session: SessionDep,
    current_user: CurrentUser,
) -> GeneratorResponse:
    """Assign the unit to a site, or unassign it by sending `site_id: null`."""
    return service.assign_site(generator_id, payload.site_id, session, current_user)


@router.delete("/{generator_id}", status_code=204)
def delete_generator(
    generator_id: UUID,
    service: GeneratorService,
    session: SessionDep,
    current_user: CurrentUser,
) -> None:
    """Soft delete. Prefer is_active=false for a decommissioned unit, so historical
    refuels stay attributable."""
    service.delete_generator(generator_id, session, current_user)
