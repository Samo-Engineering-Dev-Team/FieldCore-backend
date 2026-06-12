"""
FormSubmission model — a filled-in instance of a FormTemplate.

Submissions snapshot the template structure they were validated against
(`template_snapshot` + `template_version`), so later edits to the template
never invalidate historical submissions.
"""

from uuid import UUID
from typing import Any
from sqlmodel import SQLModel, Field
from sqlalchemy.dialects.postgresql import JSONB

from .base import BaseDB


class BaseFormSubmission(SQLModel):
    template_id: UUID = Field(foreign_key="form_templates.id", nullable=False, index=True)
    template_version: int = Field(nullable=False)
    template_snapshot: dict[str, Any] = Field(
        sa_type=JSONB, nullable=False,
        description="Frozen copy of the template structure at submit time",
    )
    values: dict[str, Any] = Field(
        sa_type=JSONB, nullable=False,
        description="Coerced submitted values keyed by field key",
    )
    attachments: dict[str, Any] | None = Field(
        default=None, sa_type=JSONB,
        description="Uploaded attachment references keyed by field key",
    )
    submitted_by: UUID = Field(nullable=False, index=True)


class FormSubmission(BaseDB, BaseFormSubmission, table=True):
    __tablename__ = "form_submissions"  # type: ignore


class FormSubmissionCreate(SQLModel):
    """Raw submission. `values`/`attachments` are validated against the template."""
    template_id: UUID
    values: dict[str, Any] = Field(default_factory=dict)
    attachments: dict[str, Any] | None = Field(default=None)


class FormSubmissionResponse(BaseDB, BaseFormSubmission):
    ...
