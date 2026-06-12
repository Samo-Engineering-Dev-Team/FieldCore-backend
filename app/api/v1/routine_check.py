from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models import RoutineCheckCreate, RoutineCheckUpdate, RoutineCheckResponse
from app.services import RoutineCheckService
from app.database import SessionDep
from app.utils.enums import RoutineCheckStatus

router = APIRouter(prefix="/routine-checks", tags=["Routine Checks"])


@router.post("/", response_model=RoutineCheckResponse, status_code=201)
def create_routine_check(
    payload: RoutineCheckCreate, service: RoutineCheckService, session: SessionDep
) -> RoutineCheckResponse:
    """"""
    return service.create_routine_check(payload, session)


@router.get("/", response_model=List[RoutineCheckResponse], status_code=200)
def read_routine_checks(
    service: RoutineCheckService,
    session: SessionDep,
    status: RoutineCheckStatus | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[RoutineCheckResponse]:
    """"""
    return service.read_routine_checks(session, status, offset, limit)


@router.get("/{routine_check_id}", response_model=RoutineCheckResponse, status_code=200)
def read_routine_check(
    routine_check_id: UUID, service: RoutineCheckService, session: SessionDep
) -> RoutineCheckResponse:
    """"""
    return service.read_routine_check(routine_check_id, session)


@router.patch(
    "/{routine_check_id}", response_model=RoutineCheckResponse, status_code=200
)
def update_routine_check(
    routine_check_id: UUID,
    payload: RoutineCheckUpdate,
    service: RoutineCheckService,
    session: SessionDep,
) -> RoutineCheckResponse:
    """"""
    return service.update_routine_check(routine_check_id, payload, session)


@router.delete("/{routine_check_id}", status_code=204)
def delete_routine_check(
    routine_check_id: UUID, service: RoutineCheckService, session: SessionDep
) -> None:
    """"""
    service.delete_routine_check(routine_check_id, session)
