from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import field_validator
from sqlalchemy import Column, Index, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.base import BaseDB


JSON_VARIANT = JSON().with_variant(JSONB, "postgresql")


class TenantTemplate(BaseDB, table=True):
    """Tenant-scoped template override with platform default support."""

    __tablename__ = "tenant_templates"  # type: ignore
    __table_args__ = (
        Index("ix_tenant_templates_tenant_name", "tenant_id", "template_name"),
        Index("ix_tenant_templates_name_version", "template_name", "version"),
    )

    template_name: str = Field(max_length=150, nullable=False, index=True)
    content: Any = Field(sa_column=Column(JSON_VARIANT, nullable=False))
    tenant_id: str | None = Field(default=None, max_length=128, index=True)
    version: int = Field(default=1, ge=1, nullable=False, index=True)


class TenantTemplateResolved(SQLModel):
    template_name: str
    tenant_id: str | None = None
    source: str
    version: int | None = None
    content: Any


class TenantTemplatePreviewRequest(SQLModel):
    template_name: str = Field(min_length=1, max_length=150)
    tenant_id: str | None = Field(default=None, max_length=128)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("template_name")
    @classmethod
    def normalize_template_name(cls, value: str) -> str:
        return value.strip()


class TenantTemplatePreviewResponse(TenantTemplateResolved):
    rendered_content: Any


class TenantTemplateResponse(SQLModel):
    id: UUID
    template_name: str
    content: Any
    tenant_id: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
