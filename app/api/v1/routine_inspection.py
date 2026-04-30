from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models import (
    RoutineInspectionCreate,
    RoutineInspectionUpdate,
    RoutineInspectionResponse,
)
from app.services import RoutineInspectionService, CurrentUser
from app.database import SessionDep
from app.utils.enums import UserRole
from app.exceptions.http import ForbiddenException

router = APIRouter(prefix="/routine-inspections", tags=["Routine Inspections"])


@router.post("/", response_model=RoutineInspectionResponse, status_code=201)
def create_routine_inspection(
    payload: RoutineInspectionCreate,
    service: RoutineInspectionService,
    session: SessionDep,
) -> RoutineInspectionResponse:
    """Create a new routine generator inspection"""
    return service.create_inspection(payload, session)


@router.get("/", response_model=List[RoutineInspectionResponse], status_code=200)
def read_routine_inspections(
    service: RoutineInspectionService,
    session: SessionDep,
    status: str | None = Query(None),
    technician_id: UUID | None = Query(None),
    site_id: UUID | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[RoutineInspectionResponse]:
    """Read routine generator inspections with optional filters"""
    return service.read_inspections(
        session, status, technician_id, site_id, offset, limit
    )


@router.get(
    "/{inspection_id}", response_model=RoutineInspectionResponse, status_code=200
)
def read_routine_inspection(
    inspection_id: UUID, service: RoutineInspectionService, session: SessionDep
) -> RoutineInspectionResponse:
    """Read a specific routine generator inspection"""
    return service.read_inspection(inspection_id, session)


@router.patch(
    "/{inspection_id}", response_model=RoutineInspectionResponse, status_code=200
)
def update_routine_inspection(
    inspection_id: UUID,
    payload: RoutineInspectionUpdate,
    user: CurrentUser,
    service: RoutineInspectionService,
    session: SessionDep,
) -> RoutineInspectionResponse:
    """Update a routine generator inspection (only allowed for draft inspections)"""
    if user.role == UserRole.NOC:
        raise ForbiddenException(
            "NOC users are not allowed to edit routine inspections"
        )
    return service.update_inspection(inspection_id, payload, session)


@router.delete("/{inspection_id}", status_code=204)
def delete_routine_inspection(
    inspection_id: UUID,
    user: CurrentUser,
    service: RoutineInspectionService,
    session: SessionDep,
) -> None:
    """Delete a routine generator inspection"""
    if user.role == UserRole.NOC:
        raise ForbiddenException(
            "NOC users are not allowed to delete routine inspections"
        )
    service.delete_inspection(inspection_id, session)


@router.patch(
    "/{inspection_id}/submit", response_model=RoutineInspectionResponse, status_code=200
)
def submit_routine_inspection(
    inspection_id: UUID, service: RoutineInspectionService, session: SessionDep
) -> RoutineInspectionResponse:
    """Submit a routine generator inspection (mark as completed)"""
    return service.submit_inspection(inspection_id, session)
