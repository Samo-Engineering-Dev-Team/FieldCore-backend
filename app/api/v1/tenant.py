from fastapi import APIRouter, Depends

from app.database import Session
from app.models import (
    TenantBootstrapRequest,
    TenantBootstrapResponse,
    TenantOffboardRequest,
    TenantOffboardResponse,
    TenantOperationalImportRequest,
    TenantOperationalImportResponse,
)
from app.services.auth import AdminOrSuperAdminUser, require_admin_or_super_admin
from app.services.tenant import TenantServiceDep


router = APIRouter(
    prefix="/tenants",
    tags=["Tenants"],
    dependencies=[Depends(require_admin_or_super_admin)],
)


@router.post("/bootstrap", response_model=TenantBootstrapResponse, status_code=201)
def bootstrap_tenant(
    payload: TenantBootstrapRequest,
    current_user: AdminOrSuperAdminUser,
    service: TenantServiceDep,
    session: Session,
) -> TenantBootstrapResponse:
    """Create tenant entity, tenant settings, and first tenant-scoped admin user."""
    return service.bootstrap_tenant(payload, session, actor_user_id=current_user.user_id)


@router.post(
    "/{tenant_id}/imports/operational-data",
    response_model=TenantOperationalImportResponse,
    status_code=200,
)
def import_tenant_operational_data(
    tenant_id: str,
    payload: TenantOperationalImportRequest,
    current_user: AdminOrSuperAdminUser,
    service: TenantServiceDep,
    session: Session,
) -> TenantOperationalImportResponse:
    """Preview or apply safe tenant onboarding imports for users/settings."""
    return service.import_operational_data(
        tenant_id,
        payload,
        session,
        actor_user_id=current_user.user_id,
    )


@router.post(
    "/{tenant_id}/offboard",
    response_model=TenantOffboardResponse,
    status_code=200,
)
def offboard_tenant(
    tenant_id: str,
    payload: TenantOffboardRequest,
    current_user: AdminOrSuperAdminUser,
    service: TenantServiceDep,
    session: Session,
) -> TenantOffboardResponse:
    """Preview or apply tenant archive/delete offboarding with safeguards."""
    return service.offboard_tenant(
        tenant_id,
        payload,
        session,
        actor_user_id=current_user.user_id,
    )
