from datetime import timedelta
from unittest.mock import MagicMock

from fastapi import Response
import pytest

from app.core import SecurityUtils
from app.exceptions.http import BadRequestException, UnauthorizedException
from app.models import PasswordResetCompletion, TokenData, User
from app.services.auth import _AuthService, get_current_user
from app.utils.enums import UserRole, UserStatus


def build_user(
    password: str = "TempPass1!",
    *,
    must_change_password: bool = False,
) -> User:
    return User(
        name="Jane",
        surname="Doe",
        email="jane.doe@example.com",
        role=UserRole.TECHNICIAN,
        status=UserStatus.ACTIVE,
        password_hash=SecurityUtils.hash_password(password),
        must_change_password=must_change_password,
    )


def test_complete_password_reset_clears_flag_and_returns_clean_token() -> None:
    user = build_user(must_change_password=True)
    session = MagicMock()
    service = _AuthService()
    service._get_user = MagicMock(return_value=user)  # type: ignore[method-assign]

    payload = PasswordResetCompletion(
        new_password="FinalPassword1!",
        confirm_password="FinalPassword1!",
    )

    token = service.complete_password_reset(user.id, payload, session)
    decoded = SecurityUtils.verify_access_token(token.access_token)

    assert decoded.must_change_password is False
    assert decoded.iat is not None
    assert SecurityUtils.check_password("FinalPassword1!", user.password_hash)
    assert user.must_change_password is False
    session.commit.assert_called_once()
    session.refresh.assert_called_once_with(user)


def test_complete_password_reset_requires_pending_flag() -> None:
    user = build_user(must_change_password=False)
    session = MagicMock()
    service = _AuthService()
    service._get_user = MagicMock(return_value=user)  # type: ignore[method-assign]

    payload = PasswordResetCompletion(
        new_password="FinalPassword1!",
        confirm_password="FinalPassword1!",
    )

    with pytest.raises(BadRequestException, match="Password reset is not required"):
        service.complete_password_reset(user.id, payload, session)

    session.commit.assert_not_called()


def test_read_current_user_returns_latest_reset_flag_from_database() -> None:
    user = build_user(must_change_password=False)
    session = MagicMock()
    service = _AuthService()
    service._get_user = MagicMock(return_value=user)  # type: ignore[method-assign]

    token_user = TokenData(
        user_id=user.id,
        role=user.role,
        name="Old",
        surname="Name",
        must_change_password=True,
        token_type="access",
    )

    result = service.read_current_user(token_user, session)

    assert result.user_id == user.id
    assert result.role == user.role
    assert result.name == user.name
    assert result.surname == user.surname
    assert result.must_change_password is False
    assert result.token_type == "access"


def test_get_current_user_rejects_token_issued_before_credentials_update() -> None:
    user = build_user()
    token = SecurityUtils.create_token(
        user.id,
        user.role,
        user.name,
        user.surname,
        user.must_change_password,
    )
    decoded = SecurityUtils.verify_access_token(token.access_token)
    assert decoded.iat is not None

    user.credentials_updated_at = decoded.iat + timedelta(seconds=1)
    session = MagicMock()
    session.exec.return_value.first.return_value = user

    with pytest.raises(UnauthorizedException, match="Session expired"):
        get_current_user(Response(), token=token.access_token, session=session)
