from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from typing import Annotated

from app.models import (
    LoginForm,
    PasskeyAuthenticationVerification,
    PasskeyCeremonyStart,
    PasskeyCredentialResponse,
    PasskeyMutationResponse,
    PasskeyRegistrationVerification,
    PasswordChange,
    PasswordResetCompletion,
    Token,
    TokenData,
)
from app.services import AuthService, CurrentUser
from app.services.auth import NocOrManagerOrAdminUser
from app.database import Session
from app.core.rate_limiter import limiter
from app.core.settings import app_settings

router = APIRouter(prefix="/auth", tags=["Auth"])


def _client_ip(request: Request) -> str | None:
    if app_settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/login", response_model=Token, status_code=201)
@limiter.limit("5/minute")
def login(
    request: Request,
    service: AuthService,
    session: Session,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    ) -> Token:
    """Authenticate user and return JWT access token. Rate limited to 5 requests per minute."""
    ip = _client_ip(request)
    ua = request.headers.get("User-Agent")
    return service.authenticate(
        LoginForm(email=form.username, password=form.password),
        session,
        ip_address=ip,
        user_agent=ua,
    )


@router.post("/login/passkey/options", response_model=PasskeyCeremonyStart, status_code=200)
@limiter.limit("10/minute")
def start_passkey_login(
    request: Request,
    service: AuthService,
    session: Session,
) -> PasskeyCeremonyStart:
    """Create WebAuthn authentication options for privileged-user passkeys."""
    return service.start_passkey_authentication(session, request)


@router.post("/login/passkey/verify", response_model=Token, status_code=200)
@limiter.limit("10/minute")
def verify_passkey_login(
    request: Request,
    payload: PasskeyAuthenticationVerification,
    service: AuthService,
    session: Session,
) -> Token:
    """Verify WebAuthn authentication and issue JWT token."""
    ip = _client_ip(request)
    ua = request.headers.get("User-Agent")
    return service.finish_passkey_authentication(
        payload,
        session,
        ip_address=ip,
        user_agent=ua,
    )


@router.post("/change-password", status_code=200)
def change_password(
    payload: PasswordChange,
    current_user: CurrentUser,
    service: AuthService,
    session: Session,
) -> dict:
    """Change the current user's password."""
    return service.change_password(current_user.user_id, payload, session)


@router.post("/complete-password-reset", response_model=Token, status_code=200)
def complete_password_reset(
    payload: PasswordResetCompletion,
    current_user: CurrentUser,
    service: AuthService,
    session: Session,
) -> Token:
    """Replace temporary password with a final password after admin reset."""
    return service.complete_password_reset(current_user.user_id, payload, session)


@router.get("/passkeys", response_model=list[PasskeyCredentialResponse], status_code=200)
def list_passkeys(
    current_user: NocOrManagerOrAdminUser,
    service: AuthService,
    session: Session,
) -> list[PasskeyCredentialResponse]:
    """List passkeys for current privileged user."""
    return service.list_passkeys(current_user, session)


@router.post("/passkeys/register/options", response_model=PasskeyCeremonyStart, status_code=200)
def start_passkey_registration(
    request: Request,
    current_user: NocOrManagerOrAdminUser,
    service: AuthService,
    session: Session,
) -> PasskeyCeremonyStart:
    """Create WebAuthn registration options for current privileged user."""
    return service.start_passkey_registration(current_user, session, request)


@router.post("/passkeys/register/verify", response_model=PasskeyCredentialResponse, status_code=201)
def verify_passkey_registration(
    payload: PasskeyRegistrationVerification,
    current_user: NocOrManagerOrAdminUser,
    service: AuthService,
    session: Session,
) -> PasskeyCredentialResponse:
    """Verify WebAuthn registration and persist passkey."""
    return service.finish_passkey_registration(current_user, payload, session)


@router.delete("/passkeys/{passkey_id}", response_model=PasskeyMutationResponse, status_code=200)
def delete_passkey(
    passkey_id: str,
    current_user: NocOrManagerOrAdminUser,
    service: AuthService,
    session: Session,
) -> PasskeyMutationResponse:
    """Delete one passkey for current privileged user."""
    from uuid import UUID

    return service.delete_passkey(current_user, UUID(passkey_id), session)


@router.get("/me", response_model=TokenData, status_code=200)
def get_current_user(user: CurrentUser) -> TokenData:
    """"""
    return user
