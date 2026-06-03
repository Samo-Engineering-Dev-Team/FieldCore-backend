"""Structure-level validation for form templates."""

import pytest
from pydantic import ValidationError

from app.models.form_template import (
    TemplateStructure,
    SectionDefinition,
    FieldDefinition,
    FieldOption,
)
from app.utils.enums import FieldType


def _field(key, ftype=FieldType.STRING, order=0, options=None):
    return FieldDefinition(key=key, label=key.title(), type=ftype, order=order, options=options)


def test_valid_template_accepted():
    structure = TemplateStructure(sections=[
        SectionDefinition(title="A", order=0, fields=[
            _field("name", order=0),
            _field("age", FieldType.NUMBER, order=1),
        ]),
        SectionDefinition(title="B", order=1, fields=[
            _field("color", FieldType.ENUM, order=0,
                   options=[FieldOption(value="red"), FieldOption(value="blue")]),
        ]),
    ])
    assert set(structure.field_map()) == {"name", "age", "color"}


def test_duplicate_field_key_rejected():
    with pytest.raises(ValidationError, match="duplicate field key"):
        TemplateStructure(sections=[
            SectionDefinition(title="A", order=0, fields=[_field("dup", order=0)]),
            SectionDefinition(title="B", order=1, fields=[_field("dup", order=0)]),
        ])


def test_duplicate_section_order_rejected():
    with pytest.raises(ValidationError, match="duplicate section order"):
        TemplateStructure(sections=[
            SectionDefinition(title="A", order=0, fields=[_field("a", order=0)]),
            SectionDefinition(title="B", order=0, fields=[_field("b", order=0)]),
        ])


def test_duplicate_field_order_within_section_rejected():
    with pytest.raises(ValidationError, match="duplicate field order"):
        TemplateStructure(sections=[
            SectionDefinition(title="A", order=0, fields=[
                _field("a", order=0),
                _field("b", order=0),
            ]),
        ])


def test_enum_field_without_options_rejected():
    with pytest.raises(ValidationError, match="must declare at least one option"):
        FieldDefinition(key="c", label="C", type=FieldType.ENUM, order=0)
