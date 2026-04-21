from datetime import datetime
from typing import Any

from sqlmodel import SQLModel, Field

from app.utils.funcs import utcnow


class TenantHealthCheck(SQLModel):
    name: str
    ok: bool
    detail: str | None = None


class TenantDiagnosticsResponse(SQLModel):
    generated_at: datetime = Field(default_factory=utcnow)
    tenant_id: str
    tenant_name: str | None = None
    tenant_status: str | None = None
    checks: list[TenantHealthCheck] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    recent_operations: list[dict[str, Any]] = Field(default_factory=list)
