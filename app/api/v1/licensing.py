from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.database import Session
from app.models import (
    EntitlementCreate,
    EntitlementResponse,
    LicensePlanCreate,
    LicensePlanDetail,
    LicensePlanResponse,
    LicenseProductCatalog,
    LicenseProductCreate,
    LicenseProductResponse,
    TenantComplianceOverviewResponse,
    TenantComplianceRunSummary,
    LicensingDashboardResponse,
    TenantLicenseAssign,
    TenantLicenseDetail,
    TenantLicenseUnassign,
)
from app.services.auth import AdminOrSuperAdminUser, require_admin_or_super_admin
from app.services.audit import request_id_from_headers
from app.services.licensing import LicensingServiceDep
from app.services.licensing_compliance import LicensingComplianceServiceDep


router = APIRouter(
    prefix="/licensing",
    tags=["Licensing"],
    dependencies=[Depends(require_admin_or_super_admin)],
)


@router.post("/products", response_model=LicenseProductResponse, status_code=201)
def create_license_product(
    payload: LicenseProductCreate,
    request: Request,
    current_user: AdminOrSuperAdminUser,
    service: LicensingServiceDep,
    session: Session,
) -> LicenseProductResponse:
    """Create one license product/SKU. Platform admin only."""
    return service.create_product(
        payload,
        session,
        actor_user_id=current_user.user_id,
        request_id=request_id_from_headers(request),
    )


@router.get("/products", response_model=list[LicenseProductCatalog], status_code=200)
def list_license_products(
    service: LicensingServiceDep,
    session: Session,
    include_inactive: bool = Query(default=False),
) -> list[LicenseProductCatalog]:
    """List license catalog grouped by product -> plans -> entitlements. Platform admin only."""
    return service.list_products(session, include_inactive)


@router.post("/plans", response_model=LicensePlanResponse, status_code=201)
def create_license_plan(
    payload: LicensePlanCreate,
    request: Request,
    current_user: AdminOrSuperAdminUser,
    service: LicensingServiceDep,
    session: Session,
) -> LicensePlanResponse:
    """Create one plan under existing license product. Platform admin only."""
    return service.create_plan(
        payload,
        session,
        actor_user_id=current_user.user_id,
        request_id=request_id_from_headers(request),
    )


@router.get("/plans", response_model=list[LicensePlanDetail], status_code=200)
def list_license_plans(
    service: LicensingServiceDep,
    session: Session,
    license_product_id: UUID | None = Query(default=None),
    include_inactive: bool = Query(default=False),
) -> list[LicensePlanDetail]:
    """List plans with entitlements. Platform admin only."""
    return service.list_plans(session, license_product_id, include_inactive)


@router.post("/entitlements", response_model=EntitlementResponse, status_code=201)
def create_entitlement(
    payload: EntitlementCreate,
    request: Request,
    current_user: AdminOrSuperAdminUser,
    service: LicensingServiceDep,
    session: Session,
) -> EntitlementResponse:
    """Create one entitlement for existing plan. Platform admin only."""
    return service.create_entitlement(
        payload,
        session,
        actor_user_id=current_user.user_id,
        request_id=request_id_from_headers(request),
    )


@router.get("/entitlements", response_model=list[EntitlementResponse], status_code=200)
def list_entitlements(
    service: LicensingServiceDep,
    session: Session,
    license_plan_id: UUID | None = Query(default=None),
) -> list[EntitlementResponse]:
    """List entitlements, optionally filtered by plan. Platform admin only."""
    return service.list_entitlements(session, license_plan_id)


@router.get("/dashboard", response_model=LicensingDashboardResponse, status_code=200)
def get_licensing_dashboard(
    service: LicensingServiceDep,
    session: Session,
    expiring_within_days: int = Query(default=30, ge=0, le=365),
    history_limit: int = Query(default=20, ge=1, le=100),
) -> LicensingDashboardResponse:
    """Get aggregate licensing oversight data for platform admins."""
    return service.get_dashboard_overview(
        session,
        expiring_within_days=expiring_within_days,
        history_limit=history_limit,
    )


@router.post("/compliance/run-daily", response_model=TenantComplianceRunSummary, status_code=200)
def run_daily_metering(
    service: LicensingComplianceServiceDep,
    session: Session,
    usage_date: date | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
) -> TenantComplianceRunSummary:
    """Compute daily tenant usage + compliance snapshots. Platform admin only."""
    return service.compute_daily_metering(session, usage_date=usage_date, tenant_id=tenant_id)


@router.get("/compliance", response_model=TenantComplianceOverviewResponse, status_code=200)
def get_tenant_compliance_overview(
    service: LicensingComplianceServiceDep,
    session: Session,
    usage_date: date | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
) -> TenantComplianceOverviewResponse:
    """Get compliance snapshot for admin dashboard consumption."""
    return service.get_compliance_overview(session, usage_date=usage_date, tenant_id=tenant_id)


@router.post("/tenant-licenses", response_model=TenantLicenseDetail, status_code=201)
def assign_tenant_license(
    payload: TenantLicenseAssign,
    request: Request,
    current_user: AdminOrSuperAdminUser,
    service: LicensingServiceDep,
    session: Session,
) -> TenantLicenseDetail:
    """Assign one license plan to tenant. Platform admin only."""
    return service.assign_tenant_license(
        payload,
        session,
        actor_user_id=current_user.user_id,
        request_id=request_id_from_headers(request),
    )


@router.get("/tenant-licenses", response_model=list[TenantLicenseDetail], status_code=200)
def list_tenant_licenses(
    service: LicensingServiceDep,
    session: Session,
    tenant_id: str | None = Query(default=None),
    active_only: bool = Query(default=False),
) -> list[TenantLicenseDetail]:
    """List tenant license assignments. Platform admin only."""
    return service.list_tenant_licenses(session, tenant_id=tenant_id, active_only=active_only)


@router.patch(
    "/tenant-licenses/{tenant_license_id}/unassign",
    response_model=TenantLicenseDetail,
    status_code=200,
)
def unassign_tenant_license(
    tenant_license_id: UUID,
    payload: TenantLicenseUnassign,
    request: Request,
    current_user: AdminOrSuperAdminUser,
    service: LicensingServiceDep,
    session: Session,
) -> TenantLicenseDetail:
    """End one tenant license assignment. Platform admin only."""
    return service.unassign_tenant_license(
        tenant_license_id,
        payload,
        session,
        actor_user_id=current_user.user_id,
        request_id=request_id_from_headers(request),
    )
