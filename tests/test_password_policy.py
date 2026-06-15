import pytest
from pydantic import ValidationError

from app.models.auth import PasswordChange, validate_password_strength
from app.models.user import UserCreate
from app.utils.enums import UserRole


def test_validate_password_strength_helper() -> None:
    assert validate_password_strength("LongEnough123") == "LongEnough123"
    with pytest.raises(ValueError):
        validate_password_strength("alllettersnodigit")
    with pytest.raises(ValueError):
        validate_password_strength("1234567890123")  # digits only, no letter


def test_password_change_rejects_short() -> None:
    with pytest.raises(ValidationError):
        PasswordChange(
            current_password="x",
            new_password="Short1",  # < 12 chars
            confirm_password="Short1",
        )


def test_password_change_rejects_no_digit() -> None:
    with pytest.raises(ValidationError):
        PasswordChange(
            current_password="x",
            new_password="NoDigitsHereAtAll",
            confirm_password="NoDigitsHereAtAll",
        )


def test_password_change_accepts_strong() -> None:
    pc = PasswordChange(
        current_password="x",
        new_password="StrongPassw0rd",
        confirm_password="StrongPassw0rd",
    )
    assert pc.new_password == "StrongPassw0rd"


def test_password_change_allows_long_passphrase() -> None:
    long_pw = "correct horse battery staple 2024"  # 33 chars
    pc = PasswordChange(
        current_password="x", new_password=long_pw, confirm_password=long_pw
    )
    assert pc.new_password == long_pw


def test_user_create_enforces_policy() -> None:
    with pytest.raises(ValidationError):
        UserCreate(
            name="A", surname="B", email="a@b.com", role=UserRole.NOC, password="weak1"
        )
    user = UserCreate(
        name="A",
        surname="B",
        email="a@b.com",
        role=UserRole.NOC,
        password="ValidPassw0rd",
    )
    assert user.password == "ValidPassw0rd"
