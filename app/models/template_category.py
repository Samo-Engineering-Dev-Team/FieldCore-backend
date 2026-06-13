"""
TemplateCategory model — the dynamic "type ID" for form templates.

Replaces the hardcoded ReportType enum. Each FormTemplate references one
category by id. Categories are admin-manageable at runtime; the built-in rows
(TASK, SHEQ, INCIDENT, ROUTINE_DRIVE, DIESEL, REPEATER) are seeded with
is_system=True and cannot be deleted or have their `code` changed.

A category declares `requires_link` (LinkTarget), which dictates whether a
submission under that category must attach to a domain object (task/incident).
"""

from sqlmodel import SQLModel, Field
from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum

from .base import BaseDB
from app.utils.enums import LinkTarget


class BaseTemplateCategory(SQLModel):
    code: str = Field(
        max_length=50, nullable=False, index=True,
        description="Immutable machine identifier, unique among categories (e.g. TASK, SHEQ)",
    )
    name: str = Field(max_length=200, nullable=False)
    description: str | None = Field(default=None, max_length=2000)
    requires_link: LinkTarget = Field(
        default=LinkTarget.NONE,
        sa_column=Column(
            SAEnum(LinkTarget, name="linktarget"),
            nullable=False,
        ),
        description="Domain object a submission under this category must attach to",
    )
    is_system: bool = Field(
        default=False, nullable=False,
        description="Built-in category: cannot be deleted or have its code changed",
    )
    is_active: bool = Field(default=True, nullable=False)


class TemplateCategory(BaseDB, BaseTemplateCategory, table=True):
    __tablename__ = "template_categories"  # type: ignore


class TemplateCategoryCreate(SQLModel):
    code: str = Field(max_length=50)
    name: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    requires_link: LinkTarget = Field(default=LinkTarget.NONE)
    is_active: bool = Field(default=True)


class TemplateCategoryUpdate(SQLModel):
    """Partial update. `code` is immutable and intentionally not updatable."""
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    requires_link: LinkTarget | None = Field(default=None)
    is_active: bool | None = Field(default=None)


class TemplateCategoryResponse(BaseDB, BaseTemplateCategory):
    ...
