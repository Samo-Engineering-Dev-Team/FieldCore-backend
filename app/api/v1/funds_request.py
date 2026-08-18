"""Funds request endpoints — the release chain (spec §2, §3).

Each stage is its own POST rather than a generic PATCH on `status`. The stages
are distinct acts by distinct capability holders, and a single status-setting
endpoint would make "approval alone never releases funds" (spec §6) a convention
rather than something the API shape enforces.
"""

from typing import List
from uuid import UUID

from fastapi import APIRouter, Query

from app.database import SessionDep
from app.models import (
    DisbursementLoad,
    FundsRequestCreate,
    FundsRequestRejection,
    FundsRequestResponse,
    FundsRequestUpdate,
)
from app.services import CurrentUser
from app.services.funds_request import FundsRequestService
from app.utils.enums import FundsRequestStatus, FundsRequestType

router = APIRouter(prefix="/funds-requests", tags=["Funds Requests"])


@router.post("/", response_model=FundsRequestResponse, status_code=201)
def create_funds_request(
    payload: FundsRequestCreate,
    service: FundsRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FundsRequestResponse:
    """Raise a funds request. Generator refuels are forced to high priority and
    are never blocked by an outstanding reconciliation."""
    return service.create_funds_request(payload, session, current_user)


@router.get("/", response_model=List[FundsRequestResponse], status_code=200)
def read_funds_requests(
    service: FundsRequestService,
    session: SessionDep,
    current_user: CurrentUser,
    status: FundsRequestStatus | None = Query(None),
    type: FundsRequestType | None = Query(None),
    technician_id: UUID | None = Query(None),
    site_id: UUID | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[FundsRequestResponse]:
    """High priority first, then oldest first, so refuels surface above stale
    trip requests in an approver's queue. A technician sees only their own rows."""
    return service.read_funds_requests(
        session,
        current_user,
        status,
        type,
        technician_id,
        site_id,
        offset,
        limit,
    )


@router.get("/eligibility", status_code=200)
def read_my_eligibility(
    service: FundsRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> dict:
    """Whether the caller is clear to request more funds, and what is outstanding.

    Declared before /{funds_request_id} so "eligibility" is not parsed as a UUID.
    """
    return service.read_my_eligibility(session, current_user)


@router.get("/{funds_request_id}", response_model=FundsRequestResponse, status_code=200)
def read_funds_request(
    funds_request_id: UUID,
    service: FundsRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FundsRequestResponse:
    return service.read_funds_request(funds_request_id, session, current_user)


@router.patch("/{funds_request_id}", response_model=FundsRequestResponse, status_code=200)
def update_funds_request(
    funds_request_id: UUID,
    payload: FundsRequestUpdate,
    service: FundsRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FundsRequestResponse:
    """Editable only while pending — an approved figure must not move afterwards."""
    return service.update_funds_request(
        funds_request_id, payload, session, current_user
    )


@router.post(
    "/{funds_request_id}/approve", response_model=FundsRequestResponse, status_code=200
)
def approve_funds_request(
    funds_request_id: UUID,
    service: FundsRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FundsRequestResponse:
    """Stage 1 of 3. Requires the 'approve' capability. Creates the disbursement
    record but moves no money."""
    return service.approve(funds_request_id, session, current_user)


@router.post(
    "/{funds_request_id}/load", response_model=FundsRequestResponse, status_code=200
)
def load_funds_request(
    funds_request_id: UUID,
    payload: DisbursementLoad,
    service: FundsRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FundsRequestResponse:
    """Stage 2 of 3. Requires the 'load' capability. The loader may state the
    amount actually loaded; omitting it accepts the approved amount."""
    return service.load(
        funds_request_id, payload.amount_issued, session, current_user
    )


@router.post(
    "/{funds_request_id}/release", response_model=FundsRequestResponse, status_code=200
)
def release_funds_request(
    funds_request_id: UUID,
    service: FundsRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FundsRequestResponse:
    """Stage 3 of 3. Requires the 'release' capability. The technician may now act
    on the funds."""
    return service.release(funds_request_id, session, current_user)


@router.post(
    "/{funds_request_id}/reject", response_model=FundsRequestResponse, status_code=200
)
def reject_funds_request(
    funds_request_id: UUID,
    payload: FundsRequestRejection,
    service: FundsRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FundsRequestResponse:
    """Refuse at whichever stage the request currently sits, by the holder of that
    stage."""
    return service.reject(funds_request_id, payload.reason, session, current_user)


@router.post(
    "/{funds_request_id}/cancel", response_model=FundsRequestResponse, status_code=200
)
def cancel_funds_request(
    funds_request_id: UUID,
    service: FundsRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> FundsRequestResponse:
    """Withdraw an unapproved request. Only the requesting technician or management."""
    return service.cancel_funds_request(funds_request_id, session, current_user)
