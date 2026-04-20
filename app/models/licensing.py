from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, UniqueConstraint
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
