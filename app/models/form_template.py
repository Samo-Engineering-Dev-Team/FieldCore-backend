"""
FormTemplate model — reusable, data-defined form/checklist definitions.

A template is an ordered list of sections, each an ordered list of typed
fields. The whole structure is stored as a single validated JSONB tree
(`structure`) rather than normalized child tables — this matches the
JSONB-heavy style used elsewhere (see Report.data, RoutineInspection.data),
lets a client create a whole template in one request, and makes the
submission snapshot a direct copy.

Field types come from app.utils.enums.FieldType. New types can be added
without schema changes (see app/services/form_validation.py).
"""

from typing import Any
from uuid import UUID
from pydantic import BaseModel, model_validator
from sqlmodel import SQLModel, Field
from sqlalchemy.dialects.postgresql import JSONB

from .base import BaseDB
from app.utils.enums import FieldType


class FieldOption(BaseModel):
    """A selectable option for an enum/select field."""
    value: str
    label: str | None = None


class FieldConstraints(BaseModel):
    """
    Type-appropriate validation attributes. All optional; only the ones
    relevant to a field's type are enforced by the validation engine.

    Permissive by design (extra attributes allowed) so new field types can
    introduce new constraints without changing this schema.
    """
    model_config = {"extra": "allow"}

    # number
    min: float | None = None
    max: float | None = None
    # string
    min_length: int | None = None
    max_length: int | None = None
    # attachment
    allowed_mime_types: list[str] | None = None
    max_size_bytes: int | None = None


class FieldDefinition(BaseModel):
    """A single typed field within a section."""
    key: str = Field(description="Machine name, unique within the template")
    label: str
    type: FieldType
    order: int = 0
    required: bool = False
    constraints: FieldConstraints = Field(default_factory=FieldConstraints)
    options: list[FieldOption] | None = Field(
        default=None, description="Allowed options for enum/select fields"
    )

    @model_validator(mode="after")
    def _enum_requires_options(self) -> "FieldDefinition":
        if self.type == FieldType.ENUM and not self.options:
            raise ValueError(f"enum field '{self.key}' must declare at least one option")
        return self


class SectionDefinition(BaseModel):
    """A named, ordered grouping of fields."""
    title: str
    description: str | None = None
    order: int = 0
    fields: list[FieldDefinition] = Field(default_factory=list)


class TemplateStructure(BaseModel):
    """The full ordered sections+fields tree stored in FormTemplate.structure."""
    sections: list[SectionDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_uniqueness(self) -> "TemplateStructure":
        # Field keys must be unique across the whole template.
        seen_keys: set[str] = set()
        for section in self.sections:
            for field in section.fields:
                if field.key in seen_keys:
                    raise ValueError(f"duplicate field key '{field.key}' in template")
                seen_keys.add(field.key)

        # Section order indices must be unique.
        section_orders = [s.order for s in self.sections]
        if len(section_orders) != len(set(section_orders)):
            raise ValueError("duplicate section order index in template")

        # Field order indices must be unique within their section.
        for section in self.sections:
            field_orders = [f.order for f in section.fields]
            if len(field_orders) != len(set(field_orders)):
                raise ValueError(f"duplicate field order index in section '{section.title}'")

        return self

    def iter_fields(self):
        """Yield (section, field) pairs across the whole template."""
        for section in self.sections:
            for field in section.fields:
                yield section, field

    def field_map(self) -> dict[str, FieldDefinition]:
        """Map of field key -> FieldDefinition for fast lookup."""
        return {field.key: field for _, field in self.iter_fields()}


class BaseFormTemplate(SQLModel):
    category_id: UUID = Field(
        foreign_key="template_categories.id", nullable=False, index=True,
        description="The dynamic category (type) this template belongs to",
    )
    key: str = Field(max_length=100, nullable=False, index=True,
                     description="Machine identifier, unique among active templates")
    name: str = Field(max_length=200, nullable=False)
    description: str | None = Field(default=None, max_length=2000)
    version: int = Field(default=1, nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    structure: dict[str, Any] = Field(
        sa_type=JSONB, nullable=False,
        description="Ordered sections+typed fields (TemplateStructure shape)",
    )


class FormTemplate(BaseDB, BaseFormTemplate, table=True):
    __tablename__ = "form_templates"  # type: ignore

    def structure_model(self) -> TemplateStructure:
        """Parse the stored structure JSONB into a validated TemplateStructure."""
        return TemplateStructure.model_validate(self.structure)


class FormTemplateCreate(SQLModel):
    """Create a template with its full nested structure in one request."""
    category_id: UUID = Field(description="The category (type) this template belongs to")
    key: str = Field(max_length=100)
    name: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool = Field(default=True)
    structure: TemplateStructure


class FormTemplateUpdate(SQLModel):
    """Partial update. Supplying a new structure bumps the version."""
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = Field(default=None)
    structure: TemplateStructure | None = Field(default=None)


class FormTemplateResponse(BaseDB, BaseFormTemplate):
    ...
