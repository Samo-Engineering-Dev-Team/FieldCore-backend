"""Reconciliation endpoints (spec §3.1.5–7).

Lines are managed as sub-resources rather than by replacing the whole
reconciliation, so a technician on a poor connection adds one expense at a time
and the running balance is recomputed server-side on every change. Every
line-mutating call returns the full reconciliation, including refreshed totals,
so the client never derives the balance itself.
"""

from typing import List
from uuid import UUID

from fastapi import APIRouter, Query

from app.database import SessionDep
from app.models import (
    ReconciliationCreate,
    ReconciliationLineCreate,
    ReconciliationLineUpdate,
    ReconciliationRejection,
    ReconciliationResponse,
)
from app.services import CurrentUser
from app.services.reconciliation import ReconciliationService
from app.utils.enums import ReconciliationStatus

router = APIRouter(prefix="/reconciliations", tags=["Reconciliations"])


@router.post("/", response_model=ReconciliationResponse, status_code=201)
def create_reconciliation(
    payload: ReconciliationCreate,
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReconciliationResponse:
    """Open a draft, either against a released disbursement (the normal flow —
    nothing can be reconciled before release, since approval and loading are not
    disbursement) or standalone via `declared_amount`, for funds a technician
    already holds but that were never re-requested."""
    return service.create_reconciliation(payload, session, current_user)


@router.get("/", response_model=List[ReconciliationResponse], status_code=200)
def read_reconciliations(
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
    status: ReconciliationStatus | None = Query(None),
    technician_id: UUID | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[ReconciliationResponse]:
    """Oldest submission first. A technician sees only their own."""
    return service.read_reconciliations(
        session, current_user, status, technician_id, offset, limit
    )


@router.get("/outstanding", status_code=200)
def read_outstanding(
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
) -> List[dict]:
    """Released disbursements with no approved reconciliation — what is still
    owed. Declared before /{reconciliation_id} so the path is not read as a UUID.
    """
    return service.read_outstanding(session, current_user)


@router.get("/for-disbursement/{disbursement_id}", status_code=200)
def read_for_disbursement(
    disbursement_id: UUID,
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReconciliationResponse | None:
    """The reconciliation for one disbursement, or null — so a client can choose
    between "start" and "continue" without treating a 404 as control flow."""
    return service.read_for_disbursement(disbursement_id, session, current_user)


@router.get(
    "/{reconciliation_id}", response_model=ReconciliationResponse, status_code=200
)
def read_reconciliation(
    reconciliation_id: UUID,
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReconciliationResponse:
    return service.read_reconciliation(reconciliation_id, session, current_user)


@router.post(
    "/{reconciliation_id}/lines",
    response_model=ReconciliationResponse,
    status_code=201,
)
def add_line(
    reconciliation_id: UUID,
    payload: ReconciliationLineCreate,
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReconciliationResponse:
    """Add one expense. The slip may be attached now or later, but every line
    needs one before the reconciliation can be submitted."""
    return service.add_line(reconciliation_id, payload, session, current_user)


@router.patch(
    "/{reconciliation_id}/lines/{line_id}",
    response_model=ReconciliationResponse,
    status_code=200,
)
def update_line(
    reconciliation_id: UUID,
    line_id: UUID,
    payload: ReconciliationLineUpdate,
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReconciliationResponse:
    return service.update_line(
        reconciliation_id, line_id, payload, session, current_user
    )


@router.delete(
    "/{reconciliation_id}/lines/{line_id}",
    response_model=ReconciliationResponse,
    status_code=200,
)
def remove_line(
    reconciliation_id: UUID,
    line_id: UUID,
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReconciliationResponse:
    """Soft delete, returning refreshed totals. Returns 200 with the updated
    reconciliation rather than 204, since removing a line changes the balance the
    client is displaying."""
    return service.remove_line(reconciliation_id, line_id, session, current_user)


@router.post(
    "/{reconciliation_id}/submit",
    response_model=ReconciliationResponse,
    status_code=200,
)
def submit_reconciliation(
    reconciliation_id: UUID,
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReconciliationResponse:
    """Hand to Finance. Requires at least one line, each with a slip attached."""
    return service.submit(reconciliation_id, session, current_user)


@router.post(
    "/{reconciliation_id}/approve",
    response_model=ReconciliationResponse,
    status_code=200,
)
def approve_reconciliation(
    reconciliation_id: UUID,
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReconciliationResponse:
    """Requires the 'finance_lead' capability. Approval is what clears the
    technician for their next funds request."""
    return service.approve(reconciliation_id, session, current_user)


@router.post(
    "/{reconciliation_id}/reject",
    response_model=ReconciliationResponse,
    status_code=200,
)
def reject_reconciliation(
    reconciliation_id: UUID,
    payload: ReconciliationRejection,
    service: ReconciliationService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReconciliationResponse:
    """Send back to the technician with a reason. Requires 'finance_lead'."""
    return service.reject(
        reconciliation_id, payload.reason, session, current_user
    )
