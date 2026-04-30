from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.database import SessionDep
from app.models import ClientCreate, ClientResponse, ClientUpdate
from app.services.auth import CurrentUser, require_admin
from app.services.authorization import require_management
from app.services.client import ClientServiceDep

router = APIRouter(prefix="/clients", tags=["Clients"])


@router.post(
    "/",
    response_model=ClientResponse,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_client(
    payload: ClientCreate, service: ClientServiceDep, session: SessionDep
) -> ClientResponse:
    """Create a new client. Admin only."""
    return service.create_client(payload, session)


@router.get("/", response_model=List[ClientResponse], status_code=200)
def read_clients(
    service: ClientServiceDep,
    session: SessionDep,
    current_user: CurrentUser,
    active_only: bool = Query(default=True, description="Only return active clients"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[ClientResponse]:
    """Get all clients."""
    require_management(current_user, "Only NOC, managers, or admins can view clients.")
    return service.read_clients(session, active_only, offset, limit)


@router.get(
    "/search/inactive",
    response_model=ClientResponse | None,
    status_code=200,
    dependencies=[Depends(require_admin)],
)
def find_inactive_client(
    service: ClientServiceDep,
    session: SessionDep,
    name: str = Query(..., description="Client name to search for"),
) -> ClientResponse | None:
    """Find an inactive client by name (for reactivation). Admin only."""
    return service.find_inactive_client_by_name(name, session)


@router.get("/{client_id}", response_model=ClientResponse, status_code=200)
def read_client(
    client_id: UUID,
    service: ClientServiceDep,
    session: SessionDep,
    current_user: CurrentUser,
) -> ClientResponse:
    """Get a single client by ID."""
    require_management(current_user, "Only NOC, managers, or admins can view clients.")
    return service.read_client(client_id, session)


@router.patch(
    "/{client_id}",
    response_model=ClientResponse,
    status_code=200,
    dependencies=[Depends(require_admin)],
)
def update_client(
    client_id: UUID,
    payload: ClientUpdate,
    service: ClientServiceDep,
    session: SessionDep,
) -> ClientResponse:
    """Update a client. Admin only."""
    return service.update_client(client_id, payload, session)


@router.delete("/{client_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_client(
    client_id: UUID, service: ClientServiceDep, session: SessionDep
) -> None:
    """Delete (deactivate) a client. Admin only."""
    service.delete_client(client_id, session)


@router.post(
    "/{client_id}/reactivate",
    response_model=ClientResponse,
    status_code=200,
    dependencies=[Depends(require_admin)],
)
def reactivate_client(
    client_id: UUID, service: ClientServiceDep, session: SessionDep
) -> ClientResponse:
    """Reactivate a previously deactivated client. Admin only."""
    return service.reactivate_client(client_id, session)
