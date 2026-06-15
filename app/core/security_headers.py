"""Security response headers middleware (H3).

Adds a baseline set of hardening headers to every response. Kept deliberately
small and API-appropriate: no Content-Security-Policy is set globally so the
interactive docs (Swagger UI / ReDoc, which load CDN assets) keep working.
"""

from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# Static headers applied to every response.
_SECURITY_HEADERS: dict[str, str] = {
    # Stop MIME sniffing.
    "X-Content-Type-Options": "nosniff",
    # Disallow framing (clickjacking) — this is a JSON API, never framed.
    "X-Frame-Options": "DENY",
    # Don't leak URLs/tokens via the Referer header.
    "Referrer-Policy": "no-referrer",
    # Force HTTPS for a year (incl. subdomains). Browsers ignore it over plain
    # HTTP, so it's safe to always send.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # Drop powerful browser features we never use.
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach baseline security headers to outgoing responses."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response
