"""
RoutePatrol API — CRUD for weekly fibre route surveillance records.

RBAC:
  - Technicians: create own patrols; list is scoped to their own patrols server-side.
  - NOC / Manager / Admin: full access, can filter freely, update attestation, delete.
"""

from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from sqlmodel import select

from app.models.route_patrol import (
    RoutePatrolCreate,
    RoutePatrolUpdate,
    RoutePatrolResponse,
)
from app.services.route_patrol import RoutePatrolService
from app.services import CurrentUser
from app.services.auth import NocOrManagerOrAdminUser
from app.database import Session
from app.utils.enums import UserRole

router = APIRouter(prefix="/route-patrols", tags=["Route Patrols"])


@router.post("/", response_model=RoutePatrolResponse, status_code=201)
def create_patrol(
    payload: RoutePatrolCreate,
    service: RoutePatrolService,
    session: Session,
    current_user: CurrentUser,
) -> RoutePatrolResponse:
    return service.create(payload, session)


@router.get("/", response_model=List[RoutePatrolResponse], status_code=200)
def list_patrols(
    service: RoutePatrolService,
    session: Session,
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
    if current_user.role == UserRole.TECHNICIAN:
        from app.models import Technician
        tech = session.exec(
            select(Technician).where(Technician.user_id == current_user.user_id)
        ).first()
        technician_id = tech.id if tech else None

    return service.list_patrols(session, technician_id, site_id, limit, offset)


@router.get("/{patrol_id}", response_model=RoutePatrolResponse, status_code=200)
def get_patrol(
    patrol_id: UUID,
    service: RoutePatrolService,
    session: Session,
    current_user: CurrentUser,
) -> RoutePatrolResponse:
    return service.get(patrol_id, session)


@router.patch("/{patrol_id}", response_model=RoutePatrolResponse, status_code=200)
def update_patrol(
    patrol_id: UUID,
    payload: RoutePatrolUpdate,
    service: RoutePatrolService,
    session: Session,
    current_user: NocOrManagerOrAdminUser,
) -> RoutePatrolResponse:
    return service.update(patrol_id, payload, session)


@router.delete("/{patrol_id}", status_code=204)
def delete_patrol(
    patrol_id: UUID,
    service: RoutePatrolService,
    session: Session,
    current_user: NocOrManagerOrAdminUser,
) -> None:
    service.delete(patrol_id, session)
