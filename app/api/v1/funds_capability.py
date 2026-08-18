"""Funds capability assignment endpoints (spec §2).

Who may approve, load, release, or sign off reconciliations. The spec names
individuals; these endpoints are how those names become reassignable data rather
than constants in the codebase.
"""

from typing import List
from uuid import UUID

from fastapi import APIRouter, Query

from app.database import SessionDep
from app.models import (
    FundsCapabilityAssignmentCreate,
    FundsCapabilityAssignmentResponse,
    FundsCapabilityAssignmentUpdate,
)
from app.services import CurrentUser
from app.services.funds_capability import FundsCapabilityService
from app.utils.enums import FundsCapability

router = APIRouter(prefix="/funds-capabilities", tags=["Funds Capabilities"])


@router.get("/me", response_model=List[FundsCapability], status_code=200)
def read_my_capabilities(
    service: FundsCapabilityService,
    session: SessionDep,
    current_user: CurrentUser,
) -> List[FundsCapability]:
    """Which stages the caller may act on. Drives which chain actions the UI
    offers; every action is re-checked server-side regardless.

    Declared before /{assignment_id} so "me" is not parsed as a UUID.
    """
    return service.read_my_capabilities(session, current_user)


@router.get("/", response_model=List[FundsCapabilityAssignmentResponse], status_code=200)
def read_assignments(
    service: FundsCapabilityService,
    session: SessionDep,
    current_user: CurrentUser,
    capability: FundsCapability | None = Query(None),
    include_inactive: bool = Query(False),
) -> List[FundsCapabilityAssignmentResponse]:
    return service.read_assignments(
        session, current_user, capability, include_inactive
    )


@router.post("/", response_model=FundsCapabilityAssignmentResponse, status_code=201)
def assign_capability(
    payload: FundsCapabilityAssignmentCreate,
    service: FundsCapabilityService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FundsCapabilityAssignmentResponse:
    return service.assign(payload, session, current_user)


@router.patch(
    "/{assignment_id}", response_model=FundsCapabilityAssignmentResponse, status_code=200
)
def update_assignment(
    assignment_id: UUID,
    payload: FundsCapabilityAssignmentUpdate,
    service: FundsCapabilityService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FundsCapabilityAssignmentResponse:
    return service.update_assignment(
        assignment_id, payload, session, current_user
    )


@router.delete("/{assignment_id}", status_code=204)
def revoke_assignment(
    assignment_id: UUID,
    service: FundsCapabilityService,
    session: SessionDep,
    current_user: CurrentUser,
) -> None:
    """Soft delete, so a past approver stays resolvable on old disbursements."""
    service.revoke(assignment_id, session, current_user)
