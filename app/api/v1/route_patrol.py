"""
RoutePatrol API — CRUD for weekly fibre route surveillance records.

RBAC:
  - Technicians: create own patrols; list is scoped to their own patrols server-side.
  - NOC / Manager / Admin: full access, can filter freely, update attestation, delete.
"""

from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models.route_patrol import (
    RoutePatrolCreate,
    RoutePatrolUpdate,
    RoutePatrolResponse,
)
from app.services.route_patrol import RoutePatrolService
from app.services import CurrentUser
from app.services.auth import NocOrManagerOrAdminUser
from app.database import SessionDep

router = APIRouter(prefix="/route-patrols", tags=["Route Patrols"])


@router.post("/", response_model=RoutePatrolResponse, status_code=201)
def create_patrol(
    payload: RoutePatrolCreate,
    service: RoutePatrolService,
    session: SessionDep,
    current_user: CurrentUser,
) -> RoutePatrolResponse:
    return service.create(payload, session, current_user)


@router.get("/", response_model=List[RoutePatrolResponse], status_code=200)
def list_patrols(
    service: RoutePatrolService,
    session: SessionDep,
    current_user: CurrentUser,
    technician_id: UUID | None = Query(None),
    site_id: UUID | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=500),
) -> List[RoutePatrolResponse]:
    """
    Technicians are automatically scoped to their own patrols.
    NOC / Manager / Admin can filter freely or omit filters to see all.
    """
    return service.list_patrols(session, current_user, technician_id, site_id, limit, offset)


@router.get("/{patrol_id}", response_model=RoutePatrolResponse, status_code=200)
def get_patrol(
    patrol_id: UUID,
    service: RoutePatrolService,
    session: SessionDep,
    current_user: CurrentUser,
) -> RoutePatrolResponse:
    return service.get(patrol_id, session, current_user)


@router.patch("/{patrol_id}", response_model=RoutePatrolResponse, status_code=200)
def update_patrol(
    patrol_id: UUID,
    payload: RoutePatrolUpdate,
    service: RoutePatrolService,
    session: SessionDep,
    current_user: NocOrManagerOrAdminUser,
) -> RoutePatrolResponse:
    return service.update(patrol_id, payload, session)


@router.delete("/{patrol_id}", status_code=204)
def delete_patrol(
    patrol_id: UUID,
    service: RoutePatrolService,
    session: SessionDep,
    current_user: NocOrManagerOrAdminUser,
) -> None:
    service.delete(patrol_id, session)
