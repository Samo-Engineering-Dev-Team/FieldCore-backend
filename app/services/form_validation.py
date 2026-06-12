"""
Server-side validation engine for dynamic form submissions.

Pure and dependency-free (no DB, no FastAPI request object) so it is easy to
unit test in isolation — mirrors how PDF rendering logic is tested standalone.

`validate_submission` checks a raw submission against a template's structure:
  - rejects unknown keys
  - enforces required fields
  - coerces/validates each value against its declared type
  - enforces per-type constraints (min/max, lengths, enum options,
    attachment mime/size)
All errors are collected (not first-fail) and raised as a structured
FormValidationException keyed by field.

Extensibility: add a new FieldType value (app/utils/enums.py) and register a
coerce/validate callable in FIELD_TYPE_VALIDATORS below. No schema changes.
"""

from datetime import date, datetime
from typing import Any, Callable

from app.exceptions.http import FormValidationException
from app.models.form_template import FieldDefinition, TemplateStructure
from app.utils.enums import FieldType


class FieldValidationError(Exception):
    """Raised by a type validator to signal a single field-level failure."""


# --- per-type coerce/validate functions ---------------------------------
# Each takes (field, value) and returns the coerced value, or raises
# FieldValidationError with a human-readable message.


def _validate_string(field: FieldDefinition, value: Any) -> str:
    if not isinstance(value, str):
        raise FieldValidationError("expected a string")
    c = field.constraints
    if c.min_length is not None and len(value) < c.min_length:
        raise FieldValidationError(f"must be at least {c.min_length} characters")
    if c.max_length is not None and len(value) > c.max_length:
        raise FieldValidationError(f"must be at most {c.max_length} characters")
    return value


def _validate_number(field: FieldDefinition, value: Any) -> float:
    if isinstance(value, bool):
        # bool is an int subclass; reject to avoid True -> 1 surprises.
        raise FieldValidationError("expected a number")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise FieldValidationError("expected a number")
    else:
        raise FieldValidationError("expected a number")

    c = field.constraints
    if c.min is not None and number < c.min:
        raise FieldValidationError(f"must be >= {c.min}")
    if c.max is not None and number > c.max:
        raise FieldValidationError(f"must be <= {c.max}")
    return number


def _validate_boolean(field: FieldDefinition, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    raise FieldValidationError("expected a boolean")


def _validate_date(field: FieldDefinition, value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        # Prefer date-only parsing so "2026-01-02" round-trips as a date,
        # falling back to full datetime for values that carry a time.
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            try:
                return datetime.fromisoformat(value).isoformat()
            except ValueError:
                raise FieldValidationError("expected an ISO date/datetime string")
    raise FieldValidationError("expected an ISO date/datetime string")


def _validate_enum(field: FieldDefinition, value: Any) -> str:
    allowed = {opt.value for opt in (field.options or [])}
    if value not in allowed:
        raise FieldValidationError(f"must be one of: {', '.join(sorted(allowed))}")
    return value


def _validate_attachment(field: FieldDefinition, value: Any) -> dict[str, Any]:
    # Value is an attachment reference, shaped like the upload metadata from
    # FileService (content_type, size, file_path/public_url, ...).
    if not isinstance(value, dict):
        raise FieldValidationError("expected an attachment reference object")

    c = field.constraints
    content_type = value.get("content_type")
    if c.allowed_mime_types is not None:
        if content_type not in c.allowed_mime_types:
            raise FieldValidationError(
                f"mime type '{content_type}' not allowed; "
                f"allowed: {', '.join(c.allowed_mime_types)}"
            )
    if c.max_size_bytes is not None:
        size = value.get("size")
        if not isinstance(size, int):
            raise FieldValidationError("attachment is missing a numeric size")
        if size > c.max_size_bytes:
            raise FieldValidationError(f"exceeds max size of {c.max_size_bytes} bytes")
    return value


FIELD_TYPE_VALIDATORS: dict[FieldType, Callable[[FieldDefinition, Any], Any]] = {
    FieldType.STRING: _validate_string,
    FieldType.NUMBER: _validate_number,
    FieldType.BOOLEAN: _validate_boolean,
    FieldType.DATE: _validate_date,
    FieldType.ENUM: _validate_enum,
    FieldType.ATTACHMENT: _validate_attachment,
}


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def validate_submission(
    structure: TemplateStructure,
    values: dict[str, Any] | None,
    attachments: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Validate a raw submission against a template structure.

    Non-attachment fields are read from `values`; attachment fields are read
    from `attachments`. Returns ``(coerced_values, coerced_attachments)`` on
    success, or raises FormValidationException with a per-field error map.
    """
    values = values or {}
    attachments = attachments or {}
    field_map = structure.field_map()
    errors: dict[str, list[str]] = {}

    def add_error(key: str, message: str) -> None:
        errors.setdefault(key, []).append(message)

    # Reject unknown keys (in both values and attachments).
    attachment_keys = {
        f.key for f in field_map.values() if f.type == FieldType.ATTACHMENT
    }
    value_keys = {k for k in field_map if k not in attachment_keys}
    for key in values:
        if key not in value_keys:
            add_error(key, "unknown field")
    for key in attachments:
        if key not in attachment_keys:
            add_error(key, "unknown attachment field")

    coerced_values: dict[str, Any] = {}
    coerced_attachments: dict[str, Any] = {}

    for key, field in field_map.items():
        is_attachment = field.type == FieldType.ATTACHMENT
        source = attachments if is_attachment else values
        present = key in source
        raw = source.get(key)

        if not present or _is_empty(raw):
            if field.required:
                add_error(key, "this field is required")
            continue

        validator = FIELD_TYPE_VALIDATORS.get(field.type)
        if validator is None:
            add_error(key, f"unsupported field type '{field.type}'")
            continue

        try:
            coerced = validator(field, raw)
        except FieldValidationError as exc:
            add_error(key, str(exc))
            continue

        if is_attachment:
            coerced_attachments[key] = coerced
        else:
            coerced_values[key] = coerced

    if errors:
        raise FormValidationException(errors=errors)

    return coerced_values, coerced_attachments
