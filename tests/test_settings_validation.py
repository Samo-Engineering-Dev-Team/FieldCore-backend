import pytest
from pydantic import ValidationError

from app.core.settings import AppSettings

# A 32+ char dummy secret so the JWT validator passes; these tests target the
# required-config checks added in H6, not the JWT rule.
_JWT = "x" * 40
_DB = dict(
    DB_HOST="localhost",
    DB_USER="postgres",
    DB_PASSWORD="pw",
    DB_NAME="db",
)


def _build(**overrides) -> AppSettings:
    # _env_file=None so the repo .env never leaks into the test.
    return AppSettings(_env_file=None, JWT_SECRET_KEY=_JWT, **overrides)


def test_development_requires_db_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        _build(ENVIRONMENT="development")  # no DB_* set
    assert "DB_HOST" in str(exc.value)


def test_development_minimal_config_ok() -> None:
    settings = _build(ENVIRONMENT="development", **_DB)
    assert settings.is_production is False


def test_production_requires_storage_and_origins() -> None:
    with pytest.raises(ValidationError) as exc:
        _build(ENVIRONMENT="production", **_DB)  # missing supabase + origins
    message = str(exc.value)
    assert "SUPABASE_URL/SUPABASE_SERVICE_KEY" in message
    assert "ALLOWED_ORIGINS" in message


def test_production_full_config_ok() -> None:
    settings = _build(
        ENVIRONMENT="production",
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_SERVICE_KEY="key",
        ALLOWED_ORIGINS="https://app.example.com",
        **_DB,
    )
    assert settings.is_production is True
    assert settings.allowed_origins == ["https://app.example.com"]
