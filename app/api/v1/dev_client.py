from fastapi import APIRouter

from app.models import ClientCreate, ClientResponse
from app.services.client import ClientServiceDep
from app.database import SessionDep

router = APIRouter(prefix="/dev/clients", tags=["Dev Clients"])


@router.post("/", response_model=ClientResponse, status_code=201)
def dev_create_client(
    payload: ClientCreate, service: ClientServiceDep, session: SessionDep
) -> ClientResponse:
    """Dev-only: create a client without admin auth. Enable by setting ALLOW_DEV_ENDPOINTS=true."""
    return service.create_client(payload, session)
