from uuid import UUID
from typing import Any, TYPE_CHECKING
from datetime import datetime
from sqlmodel import SQLModel, Field, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Index, text

from .base import BaseDB
from app.utils.funcs import utcnow


class BaseIncidentReport(SQLModel):
    incident_id: UUID = Field(foreign_key="incidents.id")
    technician_id: UUID = Field(foreign_key="technicians.id")

    site_name: str = Field(max_length=255)
    report_date: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    technician_name: str = Field(max_length=255)

    introduction: str | None = Field(default=None)
    problem_statement: str | None = Field(default=None)
    findings: str | None = Field(default=None)
    actions_taken: str | None = Field(default=None)
    root_cause_analysis: str | None = Field(default=None)
    conclusion: str | None = Field(default=None)

    attachments: dict[str, Any] | None = Field(default=None, sa_type=JSONB)

    # Populated once a PDF export has been stored in Supabase
    pdf_path: str | None = Field(default=None)
    pdf_url: str | None = Field(default=None)


class IncidentReport(BaseDB, BaseIncidentReport, table=True):
    __tablename__ = "incident_reports"  # type: ignore

    # Soft-delete-aware unique constraint: one active report per incident.
    # Must use Index (not UniqueConstraint) to support the postgresql_where clause.
    __table_args__ = (
        Index(
            "uq_incident_report_incident_active",
            "incident_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class IncidentReportCreate(SQLModel):
    incident_id: UUID
    # Auto-filled from JWT; admins may override by supplying it
    technician_id: UUID | None = Field(default=None)
    site_name: str = Field(max_length=255)
    report_date: datetime | None = Field(default=None)
    technician_name: str = Field(max_length=255)
    introduction: str | None = Field(default=None)
    problem_statement: str | None = Field(default=None)
    findings: str | None = Field(default=None)
    actions_taken: str | None = Field(default=None)
    root_cause_analysis: str | None = Field(default=None)
    conclusion: str | None = Field(default=None)
    attachments: dict[str, Any] | None = Field(default=None)


class IncidentReportUpdate(SQLModel):
    site_name: str | None = Field(default=None, max_length=255)
    technician_name: str | None = Field(default=None, max_length=255)
    introduction: str | None = Field(default=None)
    problem_statement: str | None = Field(default=None)
    findings: str | None = Field(default=None)
    actions_taken: str | None = Field(default=None)
    root_cause_analysis: str | None = Field(default=None)
    conclusion: str | None = Field(default=None)
    attachments: dict[str, Any] | None = Field(default=None)


class IncidentReportResponse(BaseDB, SQLModel):
    incident_id: UUID
    technician_id: UUID
    site_name: str = Field(default="")
    report_date: datetime
    technician_name: str = Field(default="")
    introduction: str | None = Field(default=None)
    problem_statement: str | None = Field(default=None)
    findings: str | None = Field(default=None)
    actions_taken: str | None = Field(default=None)
    root_cause_analysis: str | None = Field(default=None)
    conclusion: str | None = Field(default=None)
    attachments: dict[str, Any] | None = Field(default=None)
    pdf_path: str | None = Field(default=None)
    pdf_url: str | None = Field(default=None)
