from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.database import Session
from app.models import AuditLogListResponse
from app.services.audit import AuditServiceDep
from app.services.auth import PlatformAdminUser, require_platform_admin


router = APIRouter(
    prefix="/audit-logs",
    tags=["Audit Logs"],
    dependencies=[Depends(require_platform_admin)],
)


@router.get("", response_model=AuditLogListResponse, status_code=200)
def list_audit_logs(
    current_user: PlatformAdminUser,
    service: AuditServiceDep,
    session: Session,
    tenant_id: str | None = Query(default=None, max_length=128),
    action_type: str | None = Query(default=None, max_length=120),
    resource: str | None = Query(default=None, max_length=200),
    actor_user_id: UUID | None = Query(default=None),
    request_id: str | None = Query(default=None, max_length=128),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> AuditLogListResponse:
    """Read-only audit trail query for platform administrators."""
    return service.list_logs(
        session,
        tenant_id=tenant_id,
        action_type=action_type,
        resource=resource,
        actor_user_id=actor_user_id,
        request_id=request_id,
        created_from=created_from,
        created_to=created_to,
        offset=offset,
        limit=limit,
    )
