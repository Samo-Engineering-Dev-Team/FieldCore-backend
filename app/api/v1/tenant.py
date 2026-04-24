from fastapi import APIRouter, Request

from app.database import Session
from app.models import (
    TenantBootstrapRequest,
    TenantBootstrapResponse,
    TenantOffboardRequest,
    TenantOffboardResponse,
    TenantOperationalImportRequest,
    TenantOperationalImportResponse,
    TenantResponse,
)
from app.services.auth import CurrentUser, PlatformAdminUser, require_platform_admin
from app.services.audit import request_id_from_headers
from app.services.tenant_scope import assert_tenant_access
from app.services.tenant import TenantServiceDep


router = APIRouter(
    prefix="/tenants",
    tags=["Tenants"],
)


@router.get("", response_model=list[TenantResponse], status_code=200, include_in_schema=False)
@router.get("/", response_model=list[TenantResponse], status_code=200)
def list_tenants(
    _current_user: PlatformAdminUser,
    service: TenantServiceDep,
    session: Session,
) -> list[TenantResponse]:
    """List tenant directory rows for platform administrators."""
    return service.list_tenants(session)


@router.get("/{tenant_id}", response_model=TenantResponse, status_code=200)
def read_tenant(
    tenant_id: str,
    current_user: CurrentUser,
    service: TenantServiceDep,
    session: Session,
) -> TenantResponse:
    """Read one tenant directory row for platform administrators or same-tenant users."""
    assert_tenant_access(tenant_id, current_user, "Tenant profile is outside current tenant scope")
    return service.read_tenant(tenant_id, session)


@router.post("/bootstrap", response_model=TenantBootstrapResponse, status_code=201)
def bootstrap_tenant(
    payload: TenantBootstrapRequest,
    request: Request,
    current_user: PlatformAdminUser,
    service: TenantServiceDep,
    session: Session,
) -> TenantBootstrapResponse:
    """Create tenant entity, tenant settings, and first tenant-scoped admin user."""
    return service.bootstrap_tenant(
        payload,
        session,
        actor_user_id=current_user.user_id,
        request_id=request_id_from_headers(request),
    )


@router.post(
    "/{tenant_id}/imports/operational-data",
    response_model=TenantOperationalImportResponse,
    status_code=200,
)
def import_tenant_operational_data(
    tenant_id: str,
    payload: TenantOperationalImportRequest,
    request: Request,
    current_user: PlatformAdminUser,
    service: TenantServiceDep,
    session: Session,
) -> TenantOperationalImportResponse:
    """Preview or apply safe tenant onboarding imports for users/settings."""
    return service.import_operational_data(
        tenant_id,
        payload,
        session,
        actor_user_id=current_user.user_id,
        request_id=request_id_from_headers(request),
    )


@router.post(
    "/{tenant_id}/offboard",
    response_model=TenantOffboardResponse,
    status_code=200,
)
def offboard_tenant(
    tenant_id: str,
    payload: TenantOffboardRequest,
    request: Request,
    current_user: PlatformAdminUser,
    service: TenantServiceDep,
    session: Session,
) -> TenantOffboardResponse:
    """Preview or apply tenant archive/delete offboarding with safeguards."""
    return service.offboard_tenant(
        tenant_id,
        payload,
        session,
        actor_user_id=current_user.user_id,
        request_id=request_id_from_headers(request),
    )
