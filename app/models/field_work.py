from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Field, SQLModel

from app.utils.enums import ReportType


class FieldWorkCreate(SQLModel):
    site_id: UUID
    seacom_ref: str = Field(min_length=1, max_length=100)
    performed_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)
    attachments: dict[str, Any] | None = Field(default=None)


class FieldWorkResponse(SQLModel):
    task_id: UUID
    report_id: UUID
    report_type: ReportType
