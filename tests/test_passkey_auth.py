from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core import SecurityUtils
from app.exceptions.http import ForbiddenException, UnauthorizedException
from app.models import (
    PasskeyAuthenticationVerification,
    PasskeyChallenge,
    PasskeyCredential,
    PasskeyRegistrationVerification,
    TokenData,
    User,
)
from app.services.auth import _AuthService
from app.utils.enums import PasskeyCeremonyType, UserRole, UserStatus
from app.utils.funcs import utcnow
from webauthn.helpers.structs import CredentialDeviceType


class FakeExecResult:
    def __init__(self, *, first=None, all_=None):
        self._first = first
        self._all = all_ if all_ is not None else []

    def first(self):
        return self._first

    def all(self):
        return self._all


def build_user(
    *,
    role: UserRole = UserRole.ADMIN,
    must_change_password: bool = False,
) -> User:
    return User(
        name="Jane",
        surname="Doe",
        email="jane.doe@example.com",
        role=role,
        status=UserStatus.ACTIVE,
        password_hash=SecurityUtils.hash_password("TempPass1!"),
        must_change_password=must_change_password,
    )


def build_current_user(user: User) -> TokenData:
    return TokenData(
        user_id=user.id,
        role=user.role,
        name=user.name,
        surname=user.surname,
        must_change_password=user.must_change_password,
        token_type="access",
    )


def test_start_passkey_registration_creates_ceremony(monkeypatch) -> None:
    user = build_user(role=UserRole.ADMIN)
    current_user = build_current_user(user)
    session = MagicMock()
    session.exec.side_effect = [
        FakeExecResult(first=user),
        FakeExecResult(all_=[]),
    ]

    options = SimpleNamespace(challenge=b"register-challenge")
    monkeypatch.setattr("app.services.auth.generate_registration_options", lambda **_: options)
    monkeypatch.setattr("app.services.auth.options_to_json", lambda _opts: '{"rp":{"name":"FieldCore"}}')

    request = MagicMock()
    request.headers = {"Origin": "http://localhost:5173"}

    result = _AuthService().start_passkey_registration(current_user, session, request)

    ceremony = session.add.call_args.args[0]
    assert result.ceremony_id == ceremony.id
    assert result.options == {"rp": {"name": "FieldCore"}}
    assert ceremony.flow == PasskeyCeremonyType.REGISTRATION
    assert ceremony.user_id == user.id
    assert ceremony.origin == "http://localhost:5173"
    assert ceremony.challenge
    session.commit.assert_called_once()
    session.refresh.assert_called_once_with(ceremony)


def test_finish_passkey_registration_saves_passkey(monkeypatch) -> None:
    user = build_user(role=UserRole.MANAGER)
    current_user = build_current_user(user)
    ceremony = PasskeyChallenge(
        flow=PasskeyCeremonyType.REGISTRATION,
        user_id=user.id,
        challenge="c2F2ZWQ",
        rp_id="fieldcore.example.com",
        origin="https://fieldcore.example.com",
        expires_at=utcnow() + timedelta(minutes=5),
    )
    session = MagicMock()
    session.exec.side_effect = [
        FakeExecResult(first=user),
        FakeExecResult(first=ceremony),
        FakeExecResult(first=None),
        FakeExecResult(all_=[]),
    ]

    monkeypatch.setattr(
        "app.services.auth.verify_registration_response",
        lambda **_: SimpleNamespace(
            credential_id=b"credential-1",
            credential_public_key=b"public-key-1",
            sign_count=1,
            aaguid="aaguid-1",
            credential_device_type=CredentialDeviceType.MULTI_DEVICE,
            credential_backed_up=True,
        ),
    )

    payload = PasskeyRegistrationVerification(
        ceremony_id=ceremony.id,
        credential={
            "id": "credential-1",
            "response": {"transports": ["internal", "hybrid"]},
        },
        name="Office Laptop",
    )

    result = _AuthService().finish_passkey_registration(current_user, payload, session)

    added_models = [call.args[0] for call in session.add.call_args_list]
    passkey = next(model for model in added_models if isinstance(model, PasskeyCredential))

    assert result.name == "Office Laptop"
    assert result.transports == ["internal", "hybrid"]
    assert passkey.user_id == user.id
    assert passkey.name == "Office Laptop"
    assert passkey.device_type == CredentialDeviceType.MULTI_DEVICE.value
    assert passkey.backed_up is True
    assert ceremony.consumed_at is not None
    session.commit.assert_called_once()
    session.refresh.assert_called_once_with(passkey)


def test_finish_passkey_authentication_blocks_password_reset_accounts() -> None:
    user = build_user(role=UserRole.NOC, must_change_password=True)
    ceremony = PasskeyChallenge(
        flow=PasskeyCeremonyType.AUTHENTICATION,
        challenge="c2F2ZWQ",
        rp_id="fieldcore.example.com",
        origin="https://fieldcore.example.com",
        expires_at=utcnow() + timedelta(minutes=5),
    )
    passkey = PasskeyCredential(
        user_id=user.id,
        name="Phone",
        credential_id="credential-1",
        public_key="cHVibGljLWtleQ",
        sign_count=1,
    )
    session = MagicMock()
    session.exec.side_effect = [
        FakeExecResult(first=ceremony),
        FakeExecResult(first=passkey),
        FakeExecResult(first=user),
    ]

    payload = PasskeyAuthenticationVerification(
        ceremony_id=ceremony.id,
        credential={"id": "credential-1"},
    )

    with pytest.raises(UnauthorizedException, match="Password reset required"):
        _AuthService().finish_passkey_authentication(payload, session)


def test_finish_passkey_authentication_updates_sign_count_and_returns_token(monkeypatch) -> None:
    user = build_user(role=UserRole.ADMIN)
    ceremony = PasskeyChallenge(
        flow=PasskeyCeremonyType.AUTHENTICATION,
        challenge="c2F2ZWQ",
        rp_id="fieldcore.example.com",
        origin="https://fieldcore.example.com",
        expires_at=utcnow() + timedelta(minutes=5),
    )
    passkey = PasskeyCredential(
        user_id=user.id,
        name="Desktop",
        credential_id="credential-1",
        public_key="cHVibGljLWtleQ",
        sign_count=1,
    )
    session = MagicMock()
    session.exec.side_effect = [
        FakeExecResult(first=ceremony),
        FakeExecResult(first=passkey),
        FakeExecResult(first=user),
    ]

    monkeypatch.setattr(
        "app.services.auth.verify_authentication_response",
        lambda **_: SimpleNamespace(
            new_sign_count=7,
            credential_device_type=CredentialDeviceType.MULTI_DEVICE,
            credential_backed_up=True,
        ),
    )

    payload = PasskeyAuthenticationVerification(
        ceremony_id=ceremony.id,
        credential={"id": "credential-1"},
    )

    token = _AuthService().finish_passkey_authentication(payload, session)
    decoded = SecurityUtils.verify_access_token(token.access_token)

    assert decoded.user_id == user.id
    assert passkey.sign_count == 7
    assert passkey.last_used_at is not None
    assert passkey.backed_up is True
    assert ceremony.consumed_at is not None
    assert session.commit.call_count >= 1


def test_list_passkeys_blocks_technicians() -> None:
    user = build_user(role=UserRole.TECHNICIAN)
    current_user = build_current_user(user)

    with pytest.raises(ForbiddenException, match="Passkeys are only available"):
        _AuthService().list_passkeys(current_user, MagicMock())
