from fastapi import APIRouter, Depends

from app.database import Session
from app.models import TenantDiagnosticsResponse
from app.services.auth import AdminOrSuperAdminUser, require_admin_or_super_admin
from app.services.support_diagnostics import SupportDiagnosticsServiceDep


router = APIRouter(
    prefix="/support",
    tags=["Support"],
    dependencies=[Depends(require_admin_or_super_admin)],
)


@router.get(
    "/tenants/{tenant_id}/diagnostics",
    response_model=TenantDiagnosticsResponse,
    status_code=200,
)
def read_tenant_diagnostics(
    tenant_id: str,
    current_user: AdminOrSuperAdminUser,
    service: SupportDiagnosticsServiceDep,
    session: Session,
) -> TenantDiagnosticsResponse:
    """Return tenant health summary for support triage."""
    return service.read_tenant_diagnostics(tenant_id, session, current_user)
