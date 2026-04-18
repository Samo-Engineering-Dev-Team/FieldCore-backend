from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from typing import Annotated

from app.models import Token, TokenData, LoginForm, PasswordChange, PasswordResetCompletion
from app.services import AuthService, CurrentUser
from app.database import Session
from app.core.rate_limiter import limiter

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=Token, status_code=201)
@limiter.limit("5/minute")
def login(
    request: Request,
    service: AuthService,
    session: Session,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    ) -> Token:
    """Authenticate user and return JWT access token. Rate limited to 5 requests per minute."""
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else None)
    ua = request.headers.get("User-Agent")
    return service.authenticate(
        LoginForm(email=form.username, password=form.password),
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


@router.get("/me", response_model=TokenData, status_code=200)
def get_current_user(user: CurrentUser) -> TokenData:
    """"""
    return user
