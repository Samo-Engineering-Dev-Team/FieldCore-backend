from unittest.mock import MagicMock
from datetime import timedelta

from fastapi import Response
import pytest

from app.core import (
    SecurityUtils,
    clear_auth_cookie,
    set_auth_cookie,
    set_performance_hint_cookies,
    set_session_cookies,
)
from app.core.settings import app_settings
from app.exceptions.http import UnauthorizedException
from app.models import User
from app.services.auth import get_current_user
from app.utils.enums import UserRole, UserStatus
from app.utils.funcs import utcnow


def _set_cookie_headers(response: Response) -> list[str]:
    return [
        value.decode()
        for key, value in response.raw_headers
        if key.lower() == b"set-cookie"
    ]


def build_user() -> User:
    return User(
        name="Jane",
        surname="Doe",
        email="jane.doe@example.com",
        role=UserRole.TECHNICIAN,
        status=UserStatus.ACTIVE,
        password_hash=SecurityUtils.hash_password("Password12345"),
    )


def test_set_auth_cookie_uses_httponly_access_token_cookie() -> None:
    user = build_user()
    token = SecurityUtils.create_token(
        user.id,
        user.role,
        user.name,
        user.surname,
    )
    response = Response()

    set_auth_cookie(response, token)

    headers = _set_cookie_headers(response)
    assert len(headers) == 1
    assert headers[0].startswith(f"{app_settings.AUTH_COOKIE_NAME}=")
    assert "HttpOnly" in headers[0]
    assert f"Max-Age={app_settings.JWT_TOKEN_EXPIRE_MINUTES * 60}" in headers[0]
    assert "SameSite=lax" in headers[0]


def test_set_session_cookies_stores_access_and_refresh_tokens() -> None:
    user = build_user()
    token = SecurityUtils.create_token(
        user.id,
        user.role,
        user.name,
        user.surname,
    )
    response = Response()

    set_session_cookies(response, token)

    headers = _set_cookie_headers(response)
    assert len(headers) == 2
    assert any(header.startswith(f"{app_settings.AUTH_COOKIE_NAME}=") for header in headers)
    assert any(
        header.startswith(f"{app_settings.REFRESH_COOKIE_NAME}=") for header in headers
    )
    assert all("HttpOnly" in header for header in headers)


def test_clear_auth_cookie_expires_cookie() -> None:
    response = Response()

    clear_auth_cookie(response)

    header = _set_cookie_headers(response)[0]
    assert header.startswith(f"{app_settings.AUTH_COOKIE_NAME}=")
    assert "Max-Age=0" in header


def test_get_current_user_accepts_auth_cookie_without_authorization_header() -> None:
    user = build_user()
    token = SecurityUtils.create_token(
        user.id,
        user.role,
        user.name,
        user.surname,
    )
    decoded = SecurityUtils.verify_access_token(token.access_token)
    assert decoded.iat is not None
    user.credentials_updated_at = decoded.iat - timedelta(seconds=1)
    session = MagicMock()
    session.exec.return_value.first.return_value = user

    current_user = get_current_user(
        Response(), token=None, session=session, cookie_token=token.access_token
    )

    assert current_user.user_id == user.id
    assert current_user.role == user.role


def test_get_current_user_refreshes_active_session_near_expiry() -> None:
    user = build_user()
    token = SecurityUtils.create_token(
        user.id,
        user.role,
        user.name,
        user.surname,
        exp=utcnow() + timedelta(minutes=1),
    )
    decoded = SecurityUtils.verify_access_token(token.access_token)
    assert decoded.iat is not None
    assert decoded.exp is not None
    user.credentials_updated_at = decoded.iat - timedelta(seconds=1)
    session = MagicMock()
    session.exec.return_value.first.return_value = user
    response = Response()

    current_user = get_current_user(response, token=token.access_token, session=session)

    headers = _set_cookie_headers(response)
    assert current_user.user_id == user.id
    assert current_user.exp is not None
    assert current_user.exp > decoded.exp
    assert any(header.startswith(f"{app_settings.AUTH_COOKIE_NAME}=") for header in headers)
    assert response.headers["X-FieldCore-Session-Refreshed"] == "true"


def test_get_current_user_renews_expired_access_token_with_refresh_cookie() -> None:
    user = build_user()
    expired_access = SecurityUtils.create_token(
        user.id,
        user.role,
        user.name,
        user.surname,
        exp=utcnow() - timedelta(minutes=1),
    )
    refresh = SecurityUtils.create_refresh_token(
        user.id,
        user.role,
        user.name,
        user.surname,
    )
    decoded_refresh = SecurityUtils.verify_refresh_token(refresh.access_token)
    assert decoded_refresh.iat is not None
    user.credentials_updated_at = decoded_refresh.iat - timedelta(seconds=1)
    session = MagicMock()
    session.exec.return_value.first.return_value = user
    response = Response()

    current_user = get_current_user(
        response,
        token=expired_access.access_token,
        session=session,
        refresh_cookie=refresh.access_token,
    )

    headers = _set_cookie_headers(response)
    assert current_user.user_id == user.id
    assert current_user.token_type == "access"
    assert any(header.startswith(f"{app_settings.AUTH_COOKIE_NAME}=") for header in headers)
    assert response.headers["X-FieldCore-Session-Refreshed"] == "true"


def test_get_current_user_rejects_token_issued_before_credentials_update() -> None:
    user = build_user()
    token = SecurityUtils.create_token(
        user.id,
        user.role,
        user.name,
        user.surname,
    )
    decoded = SecurityUtils.verify_access_token(token.access_token)
    assert decoded.iat is not None

    user.credentials_updated_at = decoded.iat + timedelta(seconds=1)
    session = MagicMock()
    session.exec.return_value.first.return_value = user

    with pytest.raises(UnauthorizedException, match="Session expired"):
        get_current_user(Response(), token=token.access_token, session=session)


def test_performance_hint_cookies_are_small_and_script_readable() -> None:
    response = Response()

    set_performance_hint_cookies(
        response,
        {
            "dashboard_view": "noc",
            "dashboard_region": "gauteng",
            "dashboard_date_range": "last_30_days",
            "table_density": "compact",
        },
    )

    headers = _set_cookie_headers(response)
    assert len(headers) == 4
    assert any(header.startswith("fieldcore_hint_dashboard_view=noc") for header in headers)
    assert all("HttpOnly" not in header for header in headers)
    assert all("Max-Age=2592000" in header for header in headers)
