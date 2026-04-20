from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.utils.enums import LicenseHistoryAction
from app.utils.funcs import utcnow

from .base import BaseDB


class LicenseProductBase(SQLModel):
    sku: str = Field(max_length=64, nullable=False, index=True)
    name: str = Field(max_length=120, nullable=False)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = Field(default=True, nullable=False)


class LicenseProduct(BaseDB, LicenseProductBase, table=True):
    __tablename__ = "license_products"  # type: ignore
    __table_args__ = (
        UniqueConstraint("sku", name="uq_license_products_sku"),
    )


class LicenseProductCreate(LicenseProductBase):
    pass


class LicenseProductResponse(BaseDB, LicenseProductBase):
    pass


class LicensePlanBase(SQLModel):
    license_product_id: UUID = Field(
        foreign_key="license_products.id",
        nullable=False,
        index=True,
    )
    code: str = Field(max_length=64, nullable=False)
    name: str = Field(max_length=120, nullable=False)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = Field(default=True, nullable=False)


class LicensePlan(BaseDB, LicensePlanBase, table=True):
    __tablename__ = "license_plans"  # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "license_product_id",
            "code",
            name="uq_license_plans_product_code",
        ),
        Index("ix_license_plans_product_active", "license_product_id", "is_active"),
    )


class LicensePlanCreate(LicensePlanBase):
    pass


class LicensePlanResponse(BaseDB, LicensePlanBase):
    pass


class EntitlementBase(SQLModel):
    license_plan_id: UUID = Field(
        foreign_key="license_plans.id",
        nullable=False,
        index=True,
    )
    feature_key: str = Field(max_length=120, nullable=False, index=True)
    feature_name: str = Field(max_length=120, nullable=False)
    description: str | None = Field(default=None, max_length=500)
    grant_value: str | None = Field(default=None, max_length=120)
    is_enabled: bool = Field(default=True, nullable=False)


class Entitlement(BaseDB, EntitlementBase, table=True):
    __tablename__ = "entitlements"  # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "license_plan_id",
            "feature_key",
            name="uq_entitlements_plan_feature",
        ),
    )


class EntitlementCreate(EntitlementBase):
    pass


class EntitlementResponse(BaseDB, EntitlementBase):
    pass


class TenantLicenseBase(SQLModel):
    tenant_id: str = Field(max_length=128, nullable=False, index=True)
    license_plan_id: UUID = Field(
        foreign_key="license_plans.id",
        nullable=False,
        index=True,
    )
    starts_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        nullable=False,
    )
    ends_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    assigned_by_user_id: UUID | None = Field(default=None, index=True)
    unassigned_by_user_id: UUID | None = Field(default=None, index=True)


class TenantLicense(BaseDB, TenantLicenseBase, table=True):
    __tablename__ = "tenant_licenses"  # type: ignore
    __table_args__ = (
        Index("ix_tenant_licenses_lookup", "tenant_id", "license_plan_id", "starts_at"),
    )


class TenantLicenseAssign(SQLModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    license_plan_id: UUID
    starts_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    ends_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    note: str | None = Field(default=None, max_length=500)


class TenantLicenseUnassign(SQLModel):
    ends_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    note: str | None = Field(default=None, max_length=500)


class TenantLicenseResponse(BaseDB, TenantLicenseBase):
    pass


class LicenseHistoryBase(SQLModel):
    tenant_license_id: UUID = Field(
        foreign_key="tenant_licenses.id",
        nullable=False,
        index=True,
    )
    tenant_id: str = Field(max_length=128, nullable=False, index=True)
    license_plan_id: UUID = Field(
        foreign_key="license_plans.id",
        nullable=False,
        index=True,
    )
    action: LicenseHistoryAction = Field(nullable=False)
    actor_user_id: UUID | None = Field(default=None, index=True)
    effective_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        nullable=False,
    )
    note: str | None = Field(default=None, max_length=500)


class LicenseHistory(BaseDB, LicenseHistoryBase, table=True):
    __tablename__ = "license_history"  # type: ignore
    __table_args__ = (
        Index("ix_license_history_tenant_effective_at", "tenant_id", "effective_at"),
    )


class LicenseHistoryResponse(BaseDB, LicenseHistoryBase):
    pass


class LicenseHistoryDetail(LicenseHistoryResponse):
    product_sku: str
    product_name: str
    plan_code: str
    plan_name: str


class LicensePlanDetail(LicensePlanResponse):
    entitlements: list[EntitlementResponse] = Field(default_factory=list)


class LicenseProductCatalog(LicenseProductResponse):
    plans: list[LicensePlanDetail] = Field(default_factory=list)


class TenantLicenseDetail(TenantLicenseResponse):
    license_product_id: UUID
    product_sku: str
    product_name: str
    plan_code: str
    plan_name: str


class TenantLicenseDashboardSummary(SQLModel):
    tenant_id: str
    status: str
    total_license_count: int = 0
    active_license_count: int = 0
    scheduled_license_count: int = 0
    expired_license_count: int = 0
    active_entitlement_count: int = 0
    product_skus: list[str] = Field(default_factory=list)
    plan_codes: list[str] = Field(default_factory=list)
    plan_names: list[str] = Field(default_factory=list)
    next_expiry_at: datetime | None = None
    last_action: LicenseHistoryAction | None = None
    last_action_at: datetime | None = None


class LicensingDashboardMetrics(SQLModel):
    tracked_tenants: int = 0
    licensed_tenants: int = 0
    active_assignments: int = 0
    active_entitlements: int = 0
    expiring_soon_assignments: int = 0
    expired_assignments: int = 0
    scheduled_assignments: int = 0
    changes_last_30_days: int = 0


class LicensingDashboardResponse(SQLModel):
    generated_at: datetime = Field(default_factory=utcnow)
    metrics: LicensingDashboardMetrics
    tenant_summaries: list[TenantLicenseDashboardSummary] = Field(default_factory=list)
    recent_history: list[LicenseHistoryDetail] = Field(default_factory=list)


class TenantFeatureUsageEventBase(SQLModel):
    tenant_id: str = Field(max_length=128, nullable=False, index=True)
    feature_key: str = Field(max_length=120, nullable=False, index=True)
    feature_name: str | None = Field(default=None, max_length=120)
    usage_quantity: int = Field(default=1, nullable=False, ge=0)
    occurred_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        nullable=False,
        index=True,
    )
    recorded_by_user_id: UUID | None = Field(default=None, index=True)


class TenantFeatureUsageEvent(BaseDB, TenantFeatureUsageEventBase, table=True):
    __tablename__ = "tenant_feature_usage_events"  # type: ignore
    __table_args__ = (
        Index(
            "ix_tenant_feature_usage_events_lookup",
            "tenant_id",
            "feature_key",
            "occurred_at",
        ),
    )


class TenantFeatureUsageEventCreate(TenantFeatureUsageEventBase):
    pass


class TenantFeatureUsageEventResponse(BaseDB, TenantFeatureUsageEventBase):
    pass


class TenantUsageDailyBase(SQLModel):
    tenant_id: str = Field(max_length=128, nullable=False, index=True)
    usage_date: date = Field(
        sa_type=Date(),  # type: ignore[arg-type]
        nullable=False,
        index=True,
    )
    feature_key: str = Field(max_length=120, nullable=False, index=True)
    feature_name: str = Field(max_length=120, nullable=False)
    usage_value: int = Field(default=0, nullable=False, ge=0)
    source: str = Field(max_length=64, nullable=False, default="unknown")
    last_computed_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        nullable=False,
    )


class TenantUsageDaily(BaseDB, TenantUsageDailyBase, table=True):
    __tablename__ = "tenant_usage_daily"  # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "usage_date",
            "feature_key",
            name="uq_tenant_usage_daily_tenant_date_feature",
        ),
        Index("ix_tenant_usage_daily_lookup", "tenant_id", "usage_date", "feature_key"),
    )


class TenantUsageDailyResponse(BaseDB, TenantUsageDailyBase):
    pass


class TenantComplianceRecordBase(SQLModel):
    tenant_id: str = Field(max_length=128, nullable=False, index=True)
    usage_date: date = Field(
        sa_type=Date(),  # type: ignore[arg-type]
        nullable=False,
        index=True,
    )
    feature_key: str = Field(max_length=120, nullable=False, index=True)
    feature_name: str = Field(max_length=120, nullable=False)
    entitlement_value: str | None = Field(default=None, max_length=120)
    entitlement_limit: int | None = Field(default=None)
    entitlement_is_enabled: bool = Field(default=False, nullable=False)
    usage_value: int = Field(default=0, nullable=False, ge=0)
    overage_value: int = Field(default=0, nullable=False, ge=0)
    status: str = Field(max_length=32, nullable=False)
    source: str | None = Field(default=None, max_length=64)
    plan_codes_json: str | None = Field(default=None)
    evaluated_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        nullable=False,
    )


class TenantComplianceRecord(BaseDB, TenantComplianceRecordBase, table=True):
    __tablename__ = "tenant_compliance_records"  # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "usage_date",
            "feature_key",
            name="uq_tenant_compliance_records_tenant_date_feature",
        ),
        Index(
            "ix_tenant_compliance_records_lookup",
            "tenant_id",
            "usage_date",
            "status",
        ),
    )


class TenantComplianceRecordResponse(BaseDB, TenantComplianceRecordBase):
    pass


class TenantComplianceMetric(SQLModel):
    feature_key: str
    feature_name: str
    entitlement_value: str | None = None
    entitlement_limit: int | None = None
    entitlement_is_enabled: bool = False
    usage_value: int = 0
    overage_value: int = 0
    status: str
    source: str | None = None
    plan_codes: list[str] = Field(default_factory=list)


class TenantComplianceTenantSnapshot(SQLModel):
    tenant_id: str
    usage_date: date
    has_overages: bool = False
    overage_count: int = 0
    metrics: list[TenantComplianceMetric] = Field(default_factory=list)


class TenantComplianceOverviewResponse(SQLModel):
    generated_at: datetime = Field(default_factory=utcnow)
    usage_date: date | None = None
    tenant_count: int = 0
    overage_tenant_count: int = 0
    tenants: list[TenantComplianceTenantSnapshot] = Field(default_factory=list)


class TenantComplianceRunSummary(SQLModel):
    usage_date: date
    processed_at: datetime = Field(default_factory=utcnow)
    processed_tenant_count: int = 0
    usage_row_count: int = 0
    compliance_row_count: int = 0
    overage_tenant_count: int = 0
