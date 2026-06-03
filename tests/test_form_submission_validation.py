"""Submission validation engine: coercion, constraints, rejections."""

import pytest

from app.exceptions.http import FormValidationException
from app.models.form_template import (
    TemplateStructure,
    SectionDefinition,
    FieldDefinition,
    FieldConstraints,
    FieldOption,
)
from app.services.form_validation import validate_submission
from app.utils.enums import FieldType


def _structure():
    return TemplateStructure(sections=[
        SectionDefinition(title="Main", order=0, fields=[
            FieldDefinition(key="name", label="Name", type=FieldType.STRING, order=0,
                            required=True, constraints=FieldConstraints(max_length=10)),
            FieldDefinition(key="age", label="Age", type=FieldType.NUMBER, order=1,
                            constraints=FieldConstraints(min=0, max=120)),
            FieldDefinition(key="active", label="Active", type=FieldType.BOOLEAN, order=2),
            FieldDefinition(key="when", label="When", type=FieldType.DATE, order=3),
            FieldDefinition(key="color", label="Color", type=FieldType.ENUM, order=4,
                            options=[FieldOption(value="red"), FieldOption(value="blue")]),
        ]),
        SectionDefinition(title="Files", order=1, fields=[
            FieldDefinition(key="photo", label="Photo", type=FieldType.ATTACHMENT, order=0,
                            constraints=FieldConstraints(
                                allowed_mime_types=["image/png"], max_size_bytes=1000)),
        ]),
    ])


def test_happy_path_coerces_all_types():
    values, attachments = validate_submission(
        _structure(),
        {"name": "Ada", "age": "42", "active": "true", "when": "2026-01-02", "color": "red"},
        {"photo": {"content_type": "image/png", "size": 500, "file_path": "x/y.png"}},
    )
    assert values["name"] == "Ada"
    assert values["age"] == 42.0
    assert values["active"] is True
    assert values["when"] == "2026-01-02"
    assert values["color"] == "red"
    assert attachments["photo"]["file_path"] == "x/y.png"


def test_missing_required_field_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {"age": 30}, {})
    assert "name" in exc.value.errors


def test_unknown_key_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {"name": "x", "bogus": 1}, {})
    assert "bogus" in exc.value.errors


def test_number_out_of_range_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {"name": "x", "age": 999}, {})
    assert "age" in exc.value.errors


def test_string_over_max_length_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {"name": "x" * 50}, {})
    assert "name" in exc.value.errors


def test_enum_value_not_in_options_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {"name": "x", "color": "green"}, {})
    assert "color" in exc.value.errors


def test_non_numeric_number_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {"name": "x", "age": "abc"}, {})
    assert "age" in exc.value.errors


def test_all_errors_collected_not_first_fail():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {"age": 999, "color": "green"}, {})
    # missing required name + bad age + bad color all reported.
    assert {"name", "age", "color"} <= set(exc.value.errors)


def test_optional_empty_field_skipped():
    values, _ = validate_submission(_structure(), {"name": "x", "age": ""}, {})
    assert "age" not in values
