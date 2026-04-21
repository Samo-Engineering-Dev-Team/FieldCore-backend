from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import EmailStr, field_validator
from sqlalchemy import Column, DateTime, Index, JSON, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.utils.funcs import utcnow


JSON_VARIANT = JSON().with_variant(JSONB, "postgresql")


class TenantStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TenantOperationType(StrEnum):
    BOOTSTRAP = "bootstrap"
    IMPORT = "import"
    OFFBOARD = "offboard"


class TenantOffboardMode(StrEnum):
    ARCHIVE = "archive"
    DELETE = "delete"


class Tenant(SQLModel, table=True):
    """Dedicated tenant entity. The string id is the tenant scope used by existing rows."""

    __tablename__ = "tenants"  # type: ignore
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenants_slug"),
    )

    id: str = Field(primary_key=True, max_length=128)
    slug: str = Field(max_length=128, nullable=False, index=True)
    name: str = Field(max_length=160, nullable=False)
    status: TenantStatus = Field(default=TenantStatus.ACTIVE, nullable=False, index=True)
    archived_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        nullable=False,
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        nullable=False,
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )

    def touch(self) -> None:
        self.updated_at = utcnow()

    def archive(self) -> None:
        now = utcnow()
        self.status = TenantStatus.ARCHIVED
        self.archived_at = now
        self.updated_at = now


class TenantOperationLog(SQLModel, table=True):
    """Audit trail for tenant lifecycle operations and dry-run previews."""

    __tablename__ = "tenant_operation_logs"  # type: ignore
    __table_args__ = (
        Index("ix_tenant_operation_logs_tenant_created", "tenant_id", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(max_length=128, nullable=False, index=True)
    operation: TenantOperationType = Field(nullable=False, index=True)
    dry_run: bool = Field(default=True, nullable=False)
    actor_user_id: UUID | None = Field(default=None, index=True)
    status: str = Field(default="completed", max_length=32, nullable=False)
    message: str | None = Field(default=None, sa_column=Column(Text))
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON_VARIANT))
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        nullable=False,
    )


class TenantResponse(SQLModel):
    id: str
    slug: str
    name: str
    status: TenantStatus
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TenantBootstrapRequest(SQLModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=128)
    admin_email: EmailStr
    admin_name: str = Field(min_length=1, max_length=100)
    admin_surname: str = Field(min_length=1, max_length=100)
    admin_password: str = Field(min_length=8, max_length=16)
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        return value.strip().lower()


class TenantBootstrapResponse(SQLModel):
    tenant: TenantResponse
    admin_user_id: UUID
    setting_count: int
    operation_log_id: UUID


class TenantSettingImportItem(SQLModel):
    key: str = Field(min_length=1, max_length=100)
    value: Any
    description: str | None = None
    category: str = Field(default="general", max_length=50)


class TenantOperationalImportRequest(SQLModel):
    dry_run: bool = True
    confirm_tenant_id: str | None = Field(default=None, max_length=128)
    user_emails: list[EmailStr] = Field(default_factory=list)
    settings: list[TenantSettingImportItem] = Field(default_factory=list)


class TenantImportUserAction(SQLModel):
    email: EmailStr
    action: str
    reason: str | None = None


class TenantImportSettingAction(SQLModel):
    key: str
    action: str
    reason: str | None = None


class TenantOperationalImportResponse(SQLModel):
    tenant_id: str
    dry_run: bool
    applied: bool
    user_actions: list[TenantImportUserAction] = Field(default_factory=list)
    setting_actions: list[TenantImportSettingAction] = Field(default_factory=list)
    conflict_count: int = 0
    operation_log_id: UUID


class TenantOffboardRequest(SQLModel):
    mode: TenantOffboardMode = TenantOffboardMode.ARCHIVE
    dry_run: bool = True
    confirm_tenant_id: str | None = Field(default=None, max_length=128)
    acknowledge_data_loss: bool = False
    reason: str | None = Field(default=None, max_length=500)


class TenantRowAction(SQLModel):
    table: str
    rows: int = 0
    action: str


class TenantOffboardResponse(SQLModel):
    tenant_id: str
    mode: TenantOffboardMode
    dry_run: bool
    applied: bool
    safe_to_delete: bool
    blockers: list[str] = Field(default_factory=list)
    row_actions: list[TenantRowAction] = Field(default_factory=list)
    operation_log_id: UUID
