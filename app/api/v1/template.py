from fastapi import APIRouter, Depends, Query

from app.database import Session
from app.models import TenantTemplatePreviewRequest, TenantTemplatePreviewResponse
from app.services.auth import PlatformAdminUser, require_platform_admin
from app.services.template import TemplateServiceDep


router = APIRouter(
    prefix="/templates",
    tags=["Templates"],
    dependencies=[Depends(require_platform_admin)],
)


@router.post("/preview", response_model=TenantTemplatePreviewResponse, status_code=200)
def preview_template(
    payload: TenantTemplatePreviewRequest,
    current_user: PlatformAdminUser,
    service: TemplateServiceDep,
    session: Session,
) -> TenantTemplatePreviewResponse:
    """Preview resolved tenant/platform template content."""
    return service.preview_template(
        session,
        tenant_id=payload.tenant_id,
        template_name=payload.template_name,
        context=payload.context,
    )


@router.get("/preview", response_model=TenantTemplatePreviewResponse, status_code=200)
def preview_template_query(
    current_user: PlatformAdminUser,
    service: TemplateServiceDep,
    session: Session,
    template_name: str = Query(..., min_length=1, max_length=150),
    tenant_id: str | None = Query(default=None, max_length=128),
) -> TenantTemplatePreviewResponse:
    """Preview a resolved template without custom context."""
    return service.preview_template(
        session,
        tenant_id=tenant_id,
        template_name=template_name,
    )
