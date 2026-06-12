from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models import RoutineIssueCreate, RoutineIssueUpdate, RoutineIssueResponse
from app.services import RoutineIssueService
from app.database import SessionDep
from app.utils.enums import RoutineIssueSeverity

router = APIRouter(prefix="/routine-issues", tags=["Routine Issues"])


@router.post("/", response_model=RoutineIssueResponse, status_code=201)
def create_routine_issue(
    payload: RoutineIssueCreate, service: RoutineIssueService, session: SessionDep
) -> RoutineIssueResponse:
    """"""
    return service.create_routine_issue(payload, session)


@router.get("/", response_model=List[RoutineIssueResponse], status_code=200)
def read_routine_issues(
    service: RoutineIssueService,
    session: SessionDep,
    region: RoutineIssueSeverity | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[RoutineIssueResponse]:
    """"""
    return service.read_routine_issues(session, region, offset, limit)


@router.get("/{routine_issue_id}", response_model=RoutineIssueResponse, status_code=200)
def read_routine_issue(
    routine_issue_id: UUID, service: RoutineIssueService, session: SessionDep
) -> RoutineIssueResponse:
    """"""
    return service.read_routine_issue(routine_issue_id, session)


@router.patch(
    "/{routine_issue_id}", response_model=RoutineIssueResponse, status_code=200
)
def update_routine_issue(
    routine_issue_id: UUID,
    payload: RoutineIssueUpdate,
    service: RoutineIssueService,
    session: SessionDep,
) -> RoutineIssueResponse:
    """"""
    return service.update_routine_issue(routine_issue_id, payload, session)


@router.delete("/{routine_issue_id}", status_code=204)
def delete_routine_issue(
    routine_issue_id: UUID, service: RoutineIssueService, session: SessionDep
) -> None:
    """"""
    service.delete_routine_issue(routine_issue_id, session)
