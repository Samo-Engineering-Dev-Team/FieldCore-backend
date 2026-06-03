"""Attachment constraint enforcement against FileService-shaped metadata."""

import pytest

from app.exceptions.http import FormValidationException
from app.models.form_template import (
    TemplateStructure,
    SectionDefinition,
    FieldDefinition,
    FieldConstraints,
)
from app.services.form_validation import validate_submission
from app.utils.enums import FieldType


def _structure(required=False):
    return TemplateStructure(sections=[
        SectionDefinition(title="Files", order=0, fields=[
            FieldDefinition(
                key="photo", label="Photo", type=FieldType.ATTACHMENT, order=0,
                required=required,
                constraints=FieldConstraints(
                    allowed_mime_types=["image/png", "image/jpeg"],
                    max_size_bytes=1024,
                ),
            ),
        ]),
    ])


# Shaped like the dict returned by FileService.upload_file / file upload endpoint.
def _upload(content_type="image/png", size=500):
    return {
        "file_path": "form-submissions/abc.png",
        "public_url": "https://x/abc.png",
        "content_type": content_type,
        "size": size,
        "original_name": "abc.png",
    }


def test_valid_attachment_accepted():
    _, attachments = validate_submission(_structure(), {}, {"photo": _upload()})
    assert attachments["photo"]["content_type"] == "image/png"


def test_disallowed_mime_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {}, {"photo": _upload(content_type="application/pdf")})
    assert "photo" in exc.value.errors


def test_oversize_attachment_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {}, {"photo": _upload(size=999_999)})
    assert "photo" in exc.value.errors


def test_required_attachment_missing_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(required=True), {}, {})
    assert "photo" in exc.value.errors


def test_unknown_attachment_key_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {}, {"bogus": _upload()})
    assert "bogus" in exc.value.errors


def test_non_object_attachment_rejected():
    with pytest.raises(FormValidationException) as exc:
        validate_submission(_structure(), {}, {"photo": "not-a-dict"})
    assert "photo" in exc.value.errors
