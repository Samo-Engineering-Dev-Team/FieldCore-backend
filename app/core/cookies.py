from fastapi import Response

from app.models import Token

from .security import SecurityUtils
from .settings import app_settings

PERFORMANCE_HINT_COOKIE_NAMES = {
    "dashboard_view": "fieldcore_hint_dashboard_view",
    "dashboard_region": "fieldcore_hint_dashboard_region",
    "dashboard_date_range": "fieldcore_hint_dashboard_date_range",
    "table_density": "fieldcore_hint_table_density",
}


def _cookie_secure() -> bool:
    return app_settings.AUTH_COOKIE_SECURE or app_settings.is_production


def _cookie_domain() -> str | None:
    domain = (app_settings.AUTH_COOKIE_DOMAIN or "").strip()
    return domain or None


def _cookie_samesite() -> str:
    return app_settings.AUTH_COOKIE_SAMESITE.strip().lower()


def set_auth_cookie(response: Response, token: Token) -> None:
    """Store the access token in an HttpOnly cookie for browser clients."""
    response.set_cookie(
        key=app_settings.AUTH_COOKIE_NAME,
        value=token.access_token,
        max_age=app_settings.JWT_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        domain=_cookie_domain(),
        secure=_cookie_secure(),
        httponly=True,
        samesite=_cookie_samesite(),
    )


def set_refresh_cookie(response: Response, token: Token) -> None:
    """Store a longer-lived refresh token for sliding browser sessions."""
    response.set_cookie(
        key=app_settings.REFRESH_COOKIE_NAME,
        value=token.access_token,
        max_age=app_settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/",
        domain=_cookie_domain(),
        secure=_cookie_secure(),
        httponly=True,
        samesite=_cookie_samesite(),
    )


def set_session_cookies(response: Response, access_token: Token) -> None:
    """Store access and refresh cookies for a browser login."""
    token_data = SecurityUtils.verify_access_token(access_token.access_token)
    refresh_token = SecurityUtils.create_refresh_token(
        token_data.user_id,
        token_data.role,
        token_data.name or "",
        token_data.surname or "",
    )

    set_auth_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=app_settings.AUTH_COOKIE_NAME,
        path="/",
        domain=_cookie_domain(),
        secure=_cookie_secure(),
        httponly=True,
        samesite=_cookie_samesite(),
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=app_settings.REFRESH_COOKIE_NAME,
        path="/",
        domain=_cookie_domain(),
        secure=_cookie_secure(),
        httponly=True,
        samesite=_cookie_samesite(),
    )


def clear_session_cookies(response: Response) -> None:
    clear_auth_cookie(response)
    clear_refresh_cookie(response)


def set_performance_hint_cookies(
    response: Response, hints: dict[str, str | None]
) -> None:
    """Persist tiny UI hints that let the frontend avoid default bootstrap calls."""
    max_age = app_settings.PERFORMANCE_COOKIE_MAX_AGE_DAYS * 24 * 60 * 60

    for hint_name, cookie_name in PERFORMANCE_HINT_COOKIE_NAMES.items():
        value = hints.get(hint_name)
        if value is None:
            response.delete_cookie(
                key=cookie_name,
                path="/",
                domain=_cookie_domain(),
                secure=_cookie_secure(),
                samesite=_cookie_samesite(),
            )
            continue

        response.set_cookie(
            key=cookie_name,
            value=value,
            max_age=max_age,
            path="/",
            domain=_cookie_domain(),
            secure=_cookie_secure(),
            httponly=False,
            samesite=_cookie_samesite(),
        )
