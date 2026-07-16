import pytest
from fastapi import HTTPException

from app.api.v1.file import _validate_folder
from app.services.file import FileService


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
