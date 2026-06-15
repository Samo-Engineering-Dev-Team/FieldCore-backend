import pytest
from fastapi import HTTPException

from app.api.v1.file import (
    _content_type_matches_bytes,
    _validate_folder,
)
from app.services.file import FileService

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PDF = b"%PDF-1.7\n" + b"\x00" * 16


def test_magic_bytes_accept_matching_type() -> None:
    assert _content_type_matches_bytes(PNG, "image/png")
    assert _content_type_matches_bytes(JPEG, "image/jpeg")
    assert _content_type_matches_bytes(PDF, "application/pdf")


def test_magic_bytes_reject_spoofed_type() -> None:
    # Declared png but actually text/script bytes.
    assert not _content_type_matches_bytes(b"<html>nope", "image/png")
    # Declared pdf but is a png.
    assert not _content_type_matches_bytes(PNG, "application/pdf")
    assert not _content_type_matches_bytes(b"", "image/png")


def test_validate_folder_allows_known() -> None:
    assert _validate_folder("incidents") == "incidents"


def test_validate_folder_rejects_unknown() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_folder("../etc")
    assert exc.value.status_code == 400


def test_build_file_path_sanitizes_extension() -> None:
    svc = FileService()
    path = svc._build_file_path("evil.jp/g/../x", "incidents")
    assert path.startswith("incidents/")
    # Exactly one slash (folder separator) — no injected path segments.
    assert path.count("/") == 1
    assert ".." not in path
