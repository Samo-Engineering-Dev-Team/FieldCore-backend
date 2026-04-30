from fastapi import APIRouter, Query, Body
from typing import List
from uuid import UUID

from app.models import AccessRequestCreate, AccessRequestUpdate, AccessRequestResponse
from app.services import AccessRequestService, CurrentUser
from app.database import SessionDep
from app.utils.enums import AccessRequestStatus
from app.services.authorization import require_management

router = APIRouter(prefix="/access-requests", tags=["Access Requests"])


@router.post("/", response_model=AccessRequestResponse, status_code=201)
def create_access_request(
    payload: AccessRequestCreate,
    service: AccessRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> AccessRequestResponse:
    """"""
    return service.create_access_request(payload, session, current_user)


@router.get("/", response_model=List[AccessRequestResponse], status_code=200)
def read_access_requests(
    service: AccessRequestService,
    session: SessionDep,
    current_user: CurrentUser,
    status: AccessRequestStatus | None = Query(None),
    technician_id: UUID | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000)
) -> List[AccessRequestResponse]:
    """"""
    return service.read_access_requests(
        session,
        current_user,
        status,
        technician_id,
        offset,
        limit,
    )


@router.get("/{access_request_id}", response_model=AccessRequestResponse, status_code=200)
def read_access_request(
    access_request_id: UUID,
    service: AccessRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> AccessRequestResponse:
    """"""
    return service.read_access_request(access_request_id, session, current_user)


@router.patch("/{access_request_id}", response_model=AccessRequestResponse, status_code=200)
def update_access_request(
    access_request_id: UUID,
    payload: AccessRequestUpdate,
    service: AccessRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> AccessRequestResponse:
    """"""
    return service.update_access_request(access_request_id, payload, session, current_user)


@router.delete("/{access_request_id}", status_code=204)
def delete_access_request(
    access_request_id: UUID,
    service: AccessRequestService,
    session: SessionDep,
    current_user: CurrentUser,
) -> None:
    """"""
    service.delete_access_request(access_request_id, session, current_user)


@router.patch("/{access_request_id}/approve", response_model=AccessRequestResponse, status_code=200)
def approve_access_request(
    access_request_id: UUID,
    user: CurrentUser,
    service: AccessRequestService,
    session: SessionDep,
    seacom_ref: str = Body(..., embed=True, description="SEACOM Reference Number from client"),
) -> AccessRequestResponse:
    """
    Approve an access request with SEACOM Reference Number.
    The seacom_ref is provided by SEACOM client and will be propagated to related task.
    """
    require_management(user, "Only NOC, managers, or admins can approve access requests.")
    return service.approve_access_request(access_request_id, seacom_ref, session)


@router.patch("/{access_request_id}/reject", response_model=AccessRequestResponse, status_code=200)
def reject_access_request(
    access_request_id: UUID,
    user: CurrentUser,
    service: AccessRequestService,
    session: SessionDep
) -> AccessRequestResponse:
    """"""
    require_management(user, "Only NOC, managers, or admins can reject access requests.")
    return service.reject_access_request(access_request_id, session)
