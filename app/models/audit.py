from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Column, DateTime, Index, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.utils.funcs import utcnow


JSON_VARIANT = JSON().with_variant(JSONB, "postgresql")


class AuditLog(SQLModel, table=True):
    """Append-only audit event for admin, tenant, licensing, and billing operations."""

    __tablename__ = "audit_log"  # type: ignore
    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_log_action_type", "action_type"),
        Index("ix_audit_log_actor_created", "actor_user_id", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        nullable=False,
    )
    actor_user_id: UUID | None = Field(default=None, index=True)
    tenant_id: str | None = Field(default=None, max_length=128, index=True)
    action_type: str = Field(max_length=120, nullable=False)
    resource: str = Field(max_length=200, nullable=False, index=True)
    before: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON_VARIANT))
    after: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON_VARIANT))
    request_id: str | None = Field(default=None, max_length=128, index=True)


class AuditLogResponse(SQLModel):
    id: int
    created_at: datetime
    actor_user_id: UUID | None = None
    tenant_id: str | None = None
    action_type: str
    resource: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    request_id: str | None = None


class AuditLogListResponse(SQLModel):
    data: list[AuditLogResponse] = Field(default_factory=list)
    total: int = 0
