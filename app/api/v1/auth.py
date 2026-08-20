from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from typing import Annotated

from app.core import clear_session_cookies, set_performance_hint_cookies, set_session_cookies
from app.models import (
    LoginForm,
    PasskeyAuthenticationVerification,
    PasskeyCeremonyStart,
    PasskeyCredentialResponse,
    PasskeyMutationResponse,
    PasskeyRegistrationVerification,
    PasswordChange,
    PerformanceHintCookies,
    Token,
    TokenData,
)
from app.services import AuthService, CurrentUser
from app.services.auth import NocOrManagerOrAdminUser
from app.database import SessionDep
from app.core.rate_limiter import limiter

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=Token, status_code=201)
@limiter.limit("5/minute")
def login(
    request: Request,
    response: Response,
    service: AuthService,
    session: SessionDep,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> Token:
    """Authenticate user and return JWT access token. Rate limited to 5 requests per minute."""
    ip = request.headers.get(
        "X-Forwarded-For", request.client.host if request.client else None
    )
    ua = request.headers.get("User-Agent")
    token = service.authenticate(
        LoginForm(email=form.username, password=form.password),
        session,
        ip_address=ip,
        user_agent=ua,
    )
    set_session_cookies(response, token)
    return token


@router.post(
    "/login/passkey/options", response_model=PasskeyCeremonyStart, status_code=200
)
@limiter.limit("10/minute")
def start_passkey_login(
    request: Request,
    service: AuthService,
    session: SessionDep,
) -> PasskeyCeremonyStart:
    """Create WebAuthn authentication options for privileged-user passkeys."""
    return service.start_passkey_authentication(session, request)


@router.post("/login/passkey/verify", response_model=Token, status_code=200)
@limiter.limit("10/minute")
def verify_passkey_login(
    request: Request,
    response: Response,
    payload: PasskeyAuthenticationVerification,
    service: AuthService,
    session: SessionDep,
) -> Token:
    """Verify WebAuthn authentication and issue JWT token."""
    ip = request.headers.get(
        "X-Forwarded-For", request.client.host if request.client else None
    )
    ua = request.headers.get("User-Agent")
    token = service.finish_passkey_authentication(
        payload,
        session,
        ip_address=ip,
        user_agent=ua,
    )
    set_session_cookies(response, token)
    return token


@router.post("/change-password", status_code=200)
def change_password(
    payload: PasswordChange,
    current_user: CurrentUser,
    service: AuthService,
    session: SessionDep,
) -> dict:
    """Change the current user's password."""
    return service.change_password(current_user.user_id, payload, session)


@router.get(
    "/passkeys", response_model=list[PasskeyCredentialResponse], status_code=200
)
def list_passkeys(
    current_user: NocOrManagerOrAdminUser,
    service: AuthService,
    session: SessionDep,
) -> list[PasskeyCredentialResponse]:
    """List passkeys for current privileged user."""
    return service.list_passkeys(current_user, session)


@router.post(
    "/passkeys/register/options", response_model=PasskeyCeremonyStart, status_code=200
)
def start_passkey_registration(
    request: Request,
    current_user: NocOrManagerOrAdminUser,
    service: AuthService,
    session: SessionDep,
) -> PasskeyCeremonyStart:
    """Create WebAuthn registration options for current privileged user."""
    return service.start_passkey_registration(current_user, session, request)


@router.post(
    "/passkeys/register/verify",
    response_model=PasskeyCredentialResponse,
    status_code=201,
)
def verify_passkey_registration(
    payload: PasskeyRegistrationVerification,
    current_user: NocOrManagerOrAdminUser,
    service: AuthService,
    session: SessionDep,
) -> PasskeyCredentialResponse:
    """Verify WebAuthn registration and persist passkey."""
    return service.finish_passkey_registration(current_user, payload, session)


@router.delete(
    "/passkeys/{passkey_id}", response_model=PasskeyMutationResponse, status_code=200
)
def delete_passkey(
    passkey_id: str,
    current_user: NocOrManagerOrAdminUser,
    service: AuthService,
    session: SessionDep,
) -> PasskeyMutationResponse:
    """Delete one passkey for current privileged user."""
    from uuid import UUID

    return service.delete_passkey(current_user, UUID(passkey_id), session)


@router.post("/logout", status_code=200)
def logout(
    response: Response,
    current_user: CurrentUser,
    service: AuthService,
    session: SessionDep,
) -> dict:
    """Revoke all of the current user's tokens (global logout across devices)."""
    result = service.logout(current_user.user_id, session)
    clear_session_cookies(response)
    return result


@router.get("/me", response_model=TokenData, status_code=200)
def get_current_user(user: CurrentUser) -> TokenData:
    """"""
    return user


@router.put("/performance-hints", status_code=200)
def update_performance_hints(
    payload: PerformanceHintCookies,
    response: Response,
    current_user: CurrentUser,
) -> dict:
    """Set tiny UI hint cookies so the frontend can restore common views quickly."""
    set_performance_hint_cookies(
        response,
        {
            "dashboard_view": payload.dashboard_view,
            "dashboard_region": payload.dashboard_region,
            "dashboard_date_range": payload.dashboard_date_range,
            "table_density": payload.table_density,
        },
    )
    return {"message": "Performance hints saved", "user_id": str(current_user.user_id)}
