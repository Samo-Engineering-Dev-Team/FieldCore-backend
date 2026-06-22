import json
from datetime import timedelta
from typing import Annotated, Optional
from urllib.parse import urlparse
from uuid import UUID

from fastapi import Cookie, Depends, Request, Response
from fastapi.security import OAuth2PasswordBearer
from loguru import logger as LOG
from sqlmodel import Session, select, text
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialHint,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core import SecurityUtils, app_settings, set_auth_cookie
from app.database import get_session
from app.exceptions.http import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
)
from app.models import (
    LoginForm,
    PasskeyAuthenticationVerification,
    PasskeyCeremonyStart,
    PasskeyChallenge,
    PasskeyCredential,
    PasskeyCredentialResponse,
    PasskeyMutationResponse,
    PasskeyRegistrationVerification,
    PasswordChange,
    PasswordResetCompletion,
    Token,
    TokenData,
    User,
)
from app.utils.enums import PasskeyCeremonyType, UserRole
from app.utils.funcs import utcnow

oauth = OAuth2PasswordBearer("/api/v1/auth/login", auto_error=False)
PASSKEY_ELIGIBLE_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.MANAGER,
    UserRole.NOC,
}


def _record_login(
    session: Session,
    *,
    email: str,
    success: bool,
    user_id: Optional[UUID] = None,
    role: Optional[str] = None,
    failure_reason: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Insert a row into login_audit. Swallows errors so a DB issue never blocks login."""
    try:
        session.execute(
            text(
                """
                INSERT INTO login_audit
                    (user_id, email, ip_address, user_agent, success, failure_reason, role)
                VALUES
                    (:user_id, :email, :ip_address, :user_agent, :success, :failure_reason, :role)
                """
            ),
            {
                "user_id": str(user_id) if user_id else None,
                "email": email,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "success": success,
                "failure_reason": failure_reason,
                "role": str(role) if role else None,
            },
        )
        session.commit()
    except Exception as exc:
        LOG.warning("Could not write login_audit row: {}", exc)


class _AuthService:
    """"""

    def authenticate(
        self,
        form: LoginForm,
        session: Session,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Token:
        """"""
        statement = select(User).where(
            User.email == form.email,
            User.deleted_at.is_(None),  # type: ignore
        )
        user: User | None = session.exec(statement).first()

        if not user:
            _record_login(
                session,
                email=form.email,
                success=False,
                failure_reason="User not found",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise UnauthorizedException("Invalid email or password")

        if not user.is_active():
            _record_login(
                session,
                email=form.email,
                success=False,
                user_id=user.id,
                role=str(user.role),
                failure_reason="Account deactivated",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise UnauthorizedException(
                "This account has been deactivated. Please contact your admin."
            )

        if not SecurityUtils.check_password(form.password, user.password_hash):
            _record_login(
                session,
                email=form.email,
                success=False,
                user_id=user.id,
                role=str(user.role),
                failure_reason="Wrong password",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise UnauthorizedException("Invalid email or password")

        _record_login(
            session,
            email=form.email,
            success=True,
            user_id=user.id,
            role=str(user.role),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return SecurityUtils.create_token(
            user.id,
            user.role,
            user.name,
            user.surname,
            user.must_change_password,
        )

    def start_passkey_registration(
        self,
        current_user: TokenData,
        session: Session,
        request: Request,
    ) -> PasskeyCeremonyStart:
        """Create WebAuthn registration options for eligible users."""
        user = self._get_user(current_user.user_id, session)
        self._ensure_user_can_manage_passkeys(user)
        rp_id, origin = self._resolve_passkey_context(request)

        existing_passkeys = self._list_user_passkey_models(user.id, session)
        exclude_credentials = [
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(passkey.credential_id),
                transports=self._deserialize_transports(passkey.transports_json),
            )
            for passkey in existing_passkeys
        ]

        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=app_settings.PASSKEY_RP_NAME,
            user_name=user.email,
            user_id=user.id.bytes,
            user_display_name=self._display_name_for_user(user),
            timeout=app_settings.PASSKEY_CEREMONY_TIMEOUT_MS,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                require_resident_key=True,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=exclude_credentials or None,
            hints=[
                PublicKeyCredentialHint.CLIENT_DEVICE,
                PublicKeyCredentialHint.HYBRID,
                PublicKeyCredentialHint.SECURITY_KEY,
            ],
        )

        ceremony = PasskeyChallenge(
            flow=PasskeyCeremonyType.REGISTRATION,
            user_id=user.id,
            challenge=bytes_to_base64url(options.challenge),
            rp_id=rp_id,
            origin=origin,
            expires_at=utcnow()
            + timedelta(milliseconds=app_settings.PASSKEY_CEREMONY_TIMEOUT_MS),
        )
        session.add(ceremony)
        session.commit()
        session.refresh(ceremony)

        return PasskeyCeremonyStart(
            ceremony_id=ceremony.id,
            options=json.loads(options_to_json(options)),
        )

    def finish_passkey_registration(
        self,
        current_user: TokenData,
        payload: PasskeyRegistrationVerification,
        session: Session,
    ) -> PasskeyCredentialResponse:
        """Verify WebAuthn registration response and save passkey."""
        user = self._get_user(current_user.user_id, session)
        self._ensure_user_can_manage_passkeys(user)
        ceremony = self._get_passkey_ceremony(
            ceremony_id=payload.ceremony_id,
            flow=PasskeyCeremonyType.REGISTRATION,
            session=session,
            expected_user_id=user.id,
        )

        credential_id = self._extract_credential_id(payload.credential)
        if self._find_passkey_by_credential_id(credential_id, session):
            raise ConflictException("This passkey is already registered")

        try:
            verification = verify_registration_response(
                credential=payload.credential,
                expected_challenge=base64url_to_bytes(ceremony.challenge),
                expected_rp_id=ceremony.rp_id,
                expected_origin=ceremony.origin,
                require_user_verification=True,
            )
        except Exception as exc:
            LOG.warning(
                "Passkey registration verification failed for user {}: {}", user.id, exc
            )
            raise BadRequestException("Passkey registration could not be verified")

        passkey = PasskeyCredential(
            user_id=user.id,
            name=self._normalize_passkey_name(
                payload.name,
                session=session,
                user_id=user.id,
            ),
            credential_id=bytes_to_base64url(verification.credential_id),
            public_key=bytes_to_base64url(verification.credential_public_key),
            sign_count=verification.sign_count,
            transports_json=json.dumps(self._extract_transports(payload.credential)),
            aaguid=verification.aaguid,
            device_type=verification.credential_device_type.value,
            backed_up=verification.credential_backed_up,
        )

        ceremony.consumed_at = utcnow()
        ceremony.touch()
        session.add(passkey)
        session.add(ceremony)
        session.commit()
        session.refresh(passkey)

        return self._serialize_passkey(passkey)

    def start_passkey_authentication(
        self,
        session: Session,
        request: Request,
    ) -> PasskeyCeremonyStart:
        """Create WebAuthn authentication options for discoverable passkeys."""
        rp_id, origin = self._resolve_passkey_context(request)
        options = generate_authentication_options(
            rp_id=rp_id,
            timeout=app_settings.PASSKEY_CEREMONY_TIMEOUT_MS,
            allow_credentials=[],
            user_verification=UserVerificationRequirement.REQUIRED,
        )

        ceremony = PasskeyChallenge(
            flow=PasskeyCeremonyType.AUTHENTICATION,
            challenge=bytes_to_base64url(options.challenge),
            rp_id=rp_id,
            origin=origin,
            expires_at=utcnow()
            + timedelta(milliseconds=app_settings.PASSKEY_CEREMONY_TIMEOUT_MS),
        )
        session.add(ceremony)
        session.commit()
        session.refresh(ceremony)

        return PasskeyCeremonyStart(
            ceremony_id=ceremony.id,
            options=json.loads(options_to_json(options)),
        )

    def finish_passkey_authentication(
        self,
        payload: PasskeyAuthenticationVerification,
        session: Session,
        *,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Token:
        """Verify WebAuthn authentication response and issue app token."""
        ceremony = self._get_passkey_ceremony(
            ceremony_id=payload.ceremony_id,
            flow=PasskeyCeremonyType.AUTHENTICATION,
            session=session,
        )

        credential_id = self._extract_credential_id(payload.credential)
        passkey = self._find_passkey_by_credential_id(credential_id, session)
        if not passkey:
            _record_login(
                session,
                email="passkey",
                success=False,
                failure_reason="Unknown passkey credential",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise UnauthorizedException("Passkey login failed")

        user = self._get_user(passkey.user_id, session)
        if not user.is_active():
            _record_login(
                session,
                email=user.email,
                success=False,
                user_id=user.id,
                role=str(user.role),
                failure_reason="Account deactivated",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise UnauthorizedException(
                "This account has been deactivated. Please contact your admin."
            )

        if user.must_change_password:
            _record_login(
                session,
                email=user.email,
                success=False,
                user_id=user.id,
                role=str(user.role),
                failure_reason="Password reset required",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise UnauthorizedException(
                "Password reset required. Please sign in with your password."
            )

        if user.role not in PASSKEY_ELIGIBLE_ROLES:
            _record_login(
                session,
                email=user.email,
                success=False,
                user_id=user.id,
                role=str(user.role),
                failure_reason="Passkey not allowed for role",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise UnauthorizedException("Passkey login failed")

        try:
            verification = verify_authentication_response(
                credential=payload.credential,
                expected_challenge=base64url_to_bytes(ceremony.challenge),
                expected_rp_id=ceremony.rp_id,
                expected_origin=ceremony.origin,
                credential_public_key=base64url_to_bytes(passkey.public_key),
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True,
            )
        except Exception as exc:
            _record_login(
                session,
                email=user.email,
                success=False,
                user_id=user.id,
                role=str(user.role),
                failure_reason=f"Passkey verification failed: {str(exc)[:160]}",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise UnauthorizedException("Passkey login failed")

        passkey.sign_count = verification.new_sign_count
        passkey.device_type = verification.credential_device_type.value
        passkey.backed_up = verification.credential_backed_up
        passkey.last_used_at = utcnow()
        passkey.touch()
        ceremony.consumed_at = utcnow()
        ceremony.touch()

        session.add(passkey)
        session.add(ceremony)
        session.commit()

        _record_login(
            session,
            email=user.email,
            success=True,
            user_id=user.id,
            role=str(user.role),
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return SecurityUtils.create_token(
            user.id,
            user.role,
            user.name,
            user.surname,
            user.must_change_password,
        )

    def list_passkeys(
        self,
        current_user: TokenData,
        session: Session,
    ) -> list[PasskeyCredentialResponse]:
        """List current user's enrolled passkeys."""
        self._require_passkey_role(current_user.role)
        passkeys = self._list_user_passkey_models(current_user.user_id, session)
        return [self._serialize_passkey(passkey) for passkey in passkeys]

    def delete_passkey(
        self,
        current_user: TokenData,
        passkey_id: UUID,
        session: Session,
    ) -> PasskeyMutationResponse:
        """Delete one passkey belonging to current user."""
        self._require_passkey_role(current_user.role)
        passkey = session.exec(
            select(PasskeyCredential).where(
                PasskeyCredential.id == passkey_id,
                PasskeyCredential.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if not passkey or passkey.user_id != current_user.user_id:
            raise NotFoundException("Passkey not found")

        passkey.soft_delete()
        passkey.touch()
        session.add(passkey)
        session.commit()
        return PasskeyMutationResponse(message="Passkey removed")

    def change_password(
        self, user_id: UUID, payload: PasswordChange, session: Session
    ) -> dict:
        """Change the password for a user."""
        if payload.new_password != payload.confirm_password:
            raise BadRequestException("New password and confirmation do not match")

        user = self._get_user(user_id, session)

        if not SecurityUtils.check_password(
            payload.current_password, user.password_hash
        ):
            raise BadRequestException("Current password is incorrect")

        if payload.current_password == payload.new_password:
            raise BadRequestException(
                "New password must be different from current password"
            )

        user.password_hash = SecurityUtils.hash_password(payload.new_password)
        user.credentials_updated_at = utcnow()
        user.must_change_password = False
        session.add(user)
        session.commit()

        return {"message": "Password changed successfully"}

    def complete_password_reset(
        self,
        user_id: UUID,
        payload: PasswordResetCompletion,
        session: Session,
    ) -> Token:
        """Replace temporary password with user's final password and clear reset flag."""
        user = self._get_user(user_id, session)

        if not user.must_change_password:
            raise BadRequestException("Password reset is not required for this account")

        if payload.new_password != payload.confirm_password:
            raise BadRequestException("New password and confirmation do not match")

        if SecurityUtils.check_password(payload.new_password, user.password_hash):
            raise BadRequestException(
                "New password must be different from temporary password"
            )

        user.password_hash = SecurityUtils.hash_password(payload.new_password)
        user.credentials_updated_at = utcnow()
        user.must_change_password = False
        session.add(user)
        session.commit()
        session.refresh(user)

        return SecurityUtils.create_token(
            user.id,
            user.role,
            user.name,
            user.surname,
            user.must_change_password,
        )

    def logout(self, user_id: UUID, session: Session) -> dict:
        """Revoke all outstanding tokens for the user.

        Bumps ``sessions_revoked_at`` to now; ``get_current_user`` then rejects
        any access token issued before this moment. This is a global logout
        across every device for the user.
        """
        user = self._get_user(user_id, session)
        user.sessions_revoked_at = utcnow()
        user.touch()
        session.add(user)
        session.commit()
        return {"message": "Logged out successfully"}

    def read_current_user(self, current_user: TokenData, session: Session) -> TokenData:
        """Return latest user profile fields while keeping token metadata."""
        user = self._get_user(current_user.user_id, session)

        return TokenData(
            user_id=user.id,
            role=user.role,
            name=user.name,
            surname=user.surname,
            must_change_password=user.must_change_password,
            exp=current_user.exp,
            token_type=current_user.token_type,
            iat=current_user.iat,
        )

    def _get_user(self, user_id: UUID, session: Session) -> User:
        user = session.exec(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))  # type: ignore
        ).first()

        if not user:
            raise NotFoundException("User not found")

        return user

    def _require_passkey_role(self, role: UserRole) -> None:
        if role not in PASSKEY_ELIGIBLE_ROLES:
            raise ForbiddenException(
                "Passkeys are only available for admin, manager, and NOC users"
            )

    def _ensure_user_can_manage_passkeys(self, user: User) -> None:
        self._require_passkey_role(user.role)
        if not user.is_active():
            raise ForbiddenException("Only active users can manage passkeys")
        if user.must_change_password:
            raise ForbiddenException(
                "Complete your password reset before managing passkeys"
            )

    def _list_user_passkey_models(
        self, user_id: UUID, session: Session
    ) -> list[PasskeyCredential]:
        return list(
            session.exec(
                select(PasskeyCredential)
                .where(
                    PasskeyCredential.user_id == user_id,
                    PasskeyCredential.deleted_at.is_(None),  # type: ignore
                )
                .order_by(PasskeyCredential.created_at.desc())  # type: ignore
            ).all()
        )

    def _find_passkey_by_credential_id(
        self,
        credential_id: str,
        session: Session,
    ) -> PasskeyCredential | None:
        return session.exec(
            select(PasskeyCredential).where(
                PasskeyCredential.credential_id == credential_id,
                PasskeyCredential.deleted_at.is_(None),  # type: ignore
            )
        ).first()

    def _get_passkey_ceremony(
        self,
        *,
        ceremony_id: UUID,
        flow: PasskeyCeremonyType,
        session: Session,
        expected_user_id: UUID | None = None,
    ) -> PasskeyChallenge:
        ceremony = session.exec(
            select(PasskeyChallenge).where(
                PasskeyChallenge.id == ceremony_id,
                PasskeyChallenge.deleted_at.is_(None),  # type: ignore
            )
        ).first()

        if not ceremony or ceremony.flow != flow:
            raise BadRequestException("Passkey ceremony not found")

        if ceremony.consumed_at is not None:
            raise BadRequestException("Passkey ceremony has already been used")

        if ceremony.expires_at <= utcnow():
            raise BadRequestException("Passkey ceremony expired. Please try again.")

        if expected_user_id is not None and ceremony.user_id != expected_user_id:
            raise ForbiddenException("Passkey ceremony does not belong to current user")

        return ceremony

    def _resolve_passkey_context(self, request: Request) -> tuple[str, str]:
        origin = request.headers.get("Origin")
        if not origin:
            forwarded_proto = request.headers.get(
                "X-Forwarded-Proto", request.url.scheme
            )
            forwarded_host = request.headers.get(
                "X-Forwarded-Host"
            ) or request.headers.get("Host")
            if not forwarded_host:
                raise BadRequestException("Could not determine passkey origin")
            origin = f"{forwarded_proto}://{forwarded_host}"

        allowed_origins = app_settings.PASSKEY_ALLOWED_ORIGINS
        if allowed_origins and origin not in allowed_origins:
            raise BadRequestException("Passkey origin is not allowed")

        parsed = urlparse(origin)
        if not parsed.scheme or not parsed.hostname:
            raise BadRequestException("Invalid passkey origin")

        rp_id = app_settings.PASSKEY_RP_ID.strip() or parsed.hostname
        return rp_id, origin

    def _display_name_for_user(self, user: User) -> str:
        full_name = f"{user.name} {user.surname}".strip()
        return full_name or user.email

    def _normalize_passkey_name(
        self,
        name: str | None,
        *,
        session: Session,
        user_id: UUID,
    ) -> str:
        normalized = (name or "").strip()
        if normalized:
            return normalized[:100]

        existing_count = len(self._list_user_passkey_models(user_id, session))
        return f"Passkey {existing_count + 1}"

    def _extract_credential_id(self, credential: dict) -> str:
        credential_id = credential.get("id")
        if not isinstance(credential_id, str) or not credential_id.strip():
            raise BadRequestException("Passkey credential id missing")
        return credential_id

    def _extract_transports(self, credential: dict) -> list[str]:
        response = credential.get("response")
        if not isinstance(response, dict):
            return []

        transports = response.get("transports")
        if not isinstance(transports, list):
            return []

        return [transport for transport in transports if isinstance(transport, str)]

    def _deserialize_transports(
        self, transports_json: str | None
    ) -> list[AuthenticatorTransport] | None:
        if not transports_json:
            return None

        try:
            parsed = json.loads(transports_json)
        except json.JSONDecodeError:
            return None

        transports: list[AuthenticatorTransport] = []
        if not isinstance(parsed, list):
            return None

        for value in parsed:
            if not isinstance(value, str):
                continue
            try:
                transports.append(AuthenticatorTransport(value))
            except ValueError:
                continue

        return transports or None

    def _serialize_passkey(
        self, passkey: PasskeyCredential
    ) -> PasskeyCredentialResponse:
        transports: list[str] = []
        if passkey.transports_json:
            try:
                parsed = json.loads(passkey.transports_json)
                if isinstance(parsed, list):
                    transports = [value for value in parsed if isinstance(value, str)]
            except json.JSONDecodeError:
                transports = []

        return PasskeyCredentialResponse(
            id=passkey.id,
            name=passkey.name,
            created_at=passkey.created_at,
            last_used_at=passkey.last_used_at,
            device_type=passkey.device_type,
            backed_up=passkey.backed_up,
            transports=transports,
        )


def get_auth_service() -> _AuthService:
    """"""
    return _AuthService()


def _load_active_user(user_id: UUID, session: Session) -> User:
    user = session.exec(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))  # type: ignore
    ).first()

    if not user:
        raise UnauthorizedException("User not found")

    if not user.is_active():
        raise UnauthorizedException(
            "This account has been deactivated. Please contact your admin."
        )

    return user


def _ensure_token_not_revoked(current_user: TokenData, user: User) -> None:
    if (
        current_user.iat is not None
        and user.credentials_updated_at is not None
        and current_user.iat < user.credentials_updated_at
    ):
        raise UnauthorizedException("Session expired. Please log in again.")

    if (
        current_user.iat is not None
        and user.sessions_revoked_at is not None
        and current_user.iat < user.sessions_revoked_at
    ):
        raise UnauthorizedException("Session ended. Please log in again.")


def _token_response_from_user(user: User, token_data: TokenData) -> TokenData:
    return TokenData(
        user_id=user.id,
        role=user.role,
        name=user.name,
        surname=user.surname,
        must_change_password=user.must_change_password,
        exp=token_data.exp,
        token_type=token_data.token_type,
        iat=token_data.iat,
    )


def _should_refresh_access_cookie(current_user: TokenData) -> bool:
    if current_user.exp is None:
        return False

    refresh_at = utcnow() + timedelta(
        minutes=app_settings.SESSION_SLIDING_REFRESH_MINUTES
    )
    return current_user.exp <= refresh_at


def _refresh_access_cookie(response: Response, user: User) -> TokenData:
    token = SecurityUtils.create_token(
        user.id,
        user.role,
        user.name,
        user.surname,
        user.must_change_password,
    )
    set_auth_cookie(response, token)
    response.headers["X-FieldCore-Session-Refreshed"] = "true"
    return SecurityUtils.verify_access_token(token.access_token)


def get_current_user(
    response: Response,
    token: str | None = Depends(oauth),
    session: Session = Depends(get_session),
    cookie_token: str | None = Cookie(
        default=None, alias=app_settings.AUTH_COOKIE_NAME
    ),
    refresh_cookie: str | None = Cookie(
        default=None, alias=app_settings.REFRESH_COOKIE_NAME
    ),
) -> TokenData:
    """Decode access token, then renew active cookie sessions when needed."""
    token_value = token or cookie_token
    access_error: UnauthorizedException | None = None

    if token_value:
        try:
            current_user = SecurityUtils.decode_token(token_value, "access")
        except UnauthorizedException as exc:
            access_error = exc
        else:
            user = _load_active_user(current_user.user_id, session)
            _ensure_token_not_revoked(current_user, user)

            if _should_refresh_access_cookie(current_user):
                current_user = _refresh_access_cookie(response, user)

            return _token_response_from_user(user, current_user)

    if not refresh_cookie:
        if access_error is not None:
            raise access_error
        raise UnauthorizedException("Authentication required")

    current_user = SecurityUtils.decode_token(refresh_cookie, "refresh")
    user = _load_active_user(current_user.user_id, session)
    _ensure_token_not_revoked(current_user, user)

    current_user = _refresh_access_cookie(response, user)

    return _token_response_from_user(user, current_user)


def require_admin(current_user: TokenData = Depends(get_current_user)) -> TokenData:
    """Dependency that ensures the current user is an admin."""
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
        raise ForbiddenException("Admin access required")
    return current_user


def require_noc_or_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Dependency that ensures the current user is NOC or admin."""
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.NOC):
        raise ForbiddenException("NOC or Admin access required")
    return current_user


def require_manager_or_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Dependency that ensures the current user is manager or admin."""
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.MANAGER):
        raise ForbiddenException("Manager or Admin access required")
    return current_user


def require_noc_or_manager_or_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Dependency that ensures the current user is NOC, manager, or admin."""
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.MANAGER, UserRole.NOC):
        raise ForbiddenException("NOC, Manager, or Admin access required")
    return current_user


AuthService = Annotated[_AuthService, Depends(get_auth_service)]
CurrentUser = Annotated[TokenData, Depends(get_current_user)]
AdminUser = Annotated[TokenData, Depends(require_admin)]
NocOrAdminUser = Annotated[TokenData, Depends(require_noc_or_admin)]
ManagerOrAdminUser = Annotated[TokenData, Depends(require_manager_or_admin)]
NocOrManagerOrAdminUser = Annotated[TokenData, Depends(require_noc_or_manager_or_admin)]
