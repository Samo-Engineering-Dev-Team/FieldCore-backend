from unittest.mock import MagicMock

import pytest

from app.core import SecurityUtils
from app.exceptions.http import BadRequestException, UnauthorizedException
from app.models import AdminPasswordReset, User, UserCreate
from app.services.auth import get_current_user
from app.services.user import _UserService
from app.utils.enums import UserRole, UserStatus


def build_user(password: str = "OldPassword1!") -> User:
    return User(
        name="Jane",
        surname="Doe",
        email="jane.doe@example.com",
        role=UserRole.TECHNICIAN,
        status=UserStatus.ACTIVE,
        password_hash=SecurityUtils.hash_password(password),
    )


def test_reset_password_updates_hash_and_commits() -> None:
    user = build_user()
    original_credentials_updated_at = user.credentials_updated_at
    session = MagicMock()
    service = _UserService()
    service._get_user = MagicMock(return_value=user)  # type: ignore[method-assign]

    payload = AdminPasswordReset(
        new_password="NewPassword1!",
        confirm_password="NewPassword1!",
    )

    response = service.reset_password(user.id, payload, session)

    assert response == {"message": "Password reset successfully"}
    assert SecurityUtils.check_password("NewPassword1!", user.password_hash)
    assert user.must_change_password is True
    assert user.credentials_updated_at >= original_credentials_updated_at
    session.commit.assert_called_once()
    session.refresh.assert_called_once_with(user)


def test_create_user_requires_password_change_on_first_login() -> None:
    session = MagicMock()
    service = _UserService()

    payload = UserCreate(
        name="Jane",
        surname="Doe",
        email="jane.doe@example.com",
        role=UserRole.TECHNICIAN,
        password="NewPassword1!",
    )

    response = service.create_user(payload, session)

    created_user = session.add.call_args.args[0]
    assert created_user.must_change_password is True
    assert response.must_change_password is True
    assert SecurityUtils.check_password("NewPassword1!", created_user.password_hash)
    session.commit.assert_called_once()
    session.refresh.assert_called_once_with(created_user)


def test_reset_password_rejects_mismatched_confirmation() -> None:
    user = build_user()
    session = MagicMock()
    service = _UserService()
    service._get_user = MagicMock(return_value=user)  # type: ignore[method-assign]

    payload = AdminPasswordReset(
        new_password="NewPassword1!",
        confirm_password="DifferentPwd1!",
    )

    with pytest.raises(BadRequestException, match="New password and confirmation do not match"):
        service.reset_password(user.id, payload, session)

    session.commit.assert_not_called()


def test_reset_password_rejects_current_password_reuse() -> None:
    user = build_user(password="SamePassword1!")
    session = MagicMock()
    service = _UserService()
    service._get_user = MagicMock(return_value=user)  # type: ignore[method-assign]

    payload = AdminPasswordReset(
        new_password="SamePassword1!",
        confirm_password="SamePassword1!",
    )

    with pytest.raises(BadRequestException, match="New password must be different from current password"):
        service.reset_password(user.id, payload, session)

    session.commit.assert_not_called()


def test_reset_password_revokes_existing_access_token() -> None:
    user = build_user()
    session = MagicMock()
    service = _UserService()
    service._get_user = MagicMock(return_value=user)  # type: ignore[method-assign]

    token = SecurityUtils.create_token(
        user.id,
        user.role,
        user.name,
        user.surname,
        user.must_change_password,
    )

    payload = AdminPasswordReset(
        new_password="NewPassword1!",
        confirm_password="NewPassword1!",
    )

    service.reset_password(user.id, payload, session)
    session.exec.return_value.first.return_value = user

    with pytest.raises(UnauthorizedException, match="Session expired"):
        get_current_user(token.access_token, session)
