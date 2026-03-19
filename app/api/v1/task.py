from fastapi import APIRouter, Query
from typing import List
from uuid import UUID
from pydantic import BaseModel, Field as PydanticField

from app.models import TaskCreate, TaskUpdate, TaskResponse
from app.services import TaskService, CurrentUser
from app.database import Session
from app.utils.enums import TaskStatus, TaskType


class TaskFeedbackPayload(BaseModel):
    feedback: str = PydanticField(..., min_length=1, max_length=2000)


class TaskHoldPayload(BaseModel):
    reason: str | None = PydanticField(default=None, max_length=500)

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.post("/", response_model=TaskResponse, status_code=201)
def create_task(
    payload: TaskCreate,
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
) -> TaskResponse:
    """"""
    return service.create_task(payload, session, current_user)


@router.get("/", response_model=List[TaskResponse], status_code=200)
def read_tasks(
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
    technician_id: UUID | None = Query(None),
    task_type: TaskType | None = Query(None),
    status: TaskStatus | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000)
) -> List[TaskResponse]:
    """"""
    return service.read_tasks(session, technician_id, task_type, status, offset, limit)


@router.get("/{task_id}", response_model=TaskResponse, status_code=200)
def read_task(
    task_id: UUID,
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
) -> TaskResponse:
    """"""
    return service.read_task(task_id, session)


@router.patch("/{task_id}", response_model=TaskResponse, status_code=200)
def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
) -> TaskResponse:
    """"""
    return service.update_task(task_id, payload, session)


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: UUID,
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
) -> None:
    """"""
    service.delete_task(task_id, session)


@router.patch("/{task_id}/start", response_model=TaskResponse, status_code=200)
def start_task(
    task_id: UUID,
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
) -> TaskResponse:
    """"""
    return service.start_task(task_id, session)


@router.patch("/{task_id}/complete", response_model=TaskResponse, status_code=200)
def complete_task(
    task_id: UUID,
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
) -> TaskResponse:
    """"""
    return service.complete_task(task_id, session)


@router.patch("/{task_id}/fail", response_model=TaskResponse, status_code=200)
def fail_task(
    task_id: UUID,
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
) -> TaskResponse:
    """"""
    return service.fail_task(task_id, session)


@router.post("/{task_id}/feedback", response_model=TaskResponse, status_code=200)
def submit_task_feedback(
    task_id: UUID,
    payload: TaskFeedbackPayload,
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
) -> TaskResponse:
    """
    Submit completion feedback for an RHS task.
    Sets the feedback text and marks the task as COMPLETED.
    RHS tasks do not require a formal report — this feedback is the record.
    """
    return service.submit_feedback(task_id, payload.feedback, session)


@router.patch("/{task_id}/hold", response_model=TaskResponse, status_code=200)
def hold_task(
    task_id: UUID,
    payload: TaskHoldPayload,
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
) -> TaskResponse:
    """Put a started task on hold — technician will continue the next day."""
    return service.hold_task(task_id, payload.reason, session)


@router.patch("/{task_id}/resume", response_model=TaskResponse, status_code=200)
def resume_task(
    task_id: UUID,
    service: TaskService,
    session: Session,
    current_user: CurrentUser,
) -> TaskResponse:
    """Resume an on-hold task, restoring it to started status."""
    return service.resume_task(task_id, session)
