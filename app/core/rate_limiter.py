"""Rate limiting configuration for API endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import time

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from loguru import logger as LOG
from redis import Redis
from redis.exceptions import RedisError
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.metrics import tenant_metrics
from app.core.security import SecurityUtils
from app.core.settings import app_settings
from app.exceptions.http import UnauthorizedException
from app.utils.enums import UserRole


limiter = Limiter(key_func=get_remote_address)


SKIPPED_RATE_LIMIT_PATHS = {
    "/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
}


@dataclass(frozen=True)
class RateLimitIdentity:
    tenant_id: str
    subject_id: str
    subject_type: str
    role: UserRole | None = None
    override_requested: bool = False


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    key: str
    bypassed: bool = False


class TenantRateLimiter:
    """Redis-backed per-tenant fixed-window limiter with memory fallback."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        limit: int | None = None,
        window_seconds: int | None = None,
        redis_url: str | None = None,
        fail_open: bool | None = None,
    ) -> None:
        self.enabled = app_settings.TENANT_RATE_LIMIT_ENABLED if enabled is None else enabled
        self.limit = limit or app_settings.TENANT_RATE_LIMIT_REQUESTS
        self.window_seconds = window_seconds or app_settings.TENANT_RATE_LIMIT_WINDOW_SECONDS
        self.fail_open = app_settings.TENANT_RATE_LIMIT_FAIL_OPEN if fail_open is None else fail_open
        self._redis_url = redis_url if redis_url is not None else app_settings.tenant_rate_limit_redis_url
        self._redis: Redis | None = None
        self._redis_failed = False
        self._memory: dict[str, tuple[int, float]] = {}
        self._lock = Lock()

    def identify(self, request: Request) -> RateLimitIdentity:
        api_key = request.headers.get("X-API-Key")
        token_data = self._decode_bearer(request)
        tenant_id = "anonymous"
        subject_id = self._client_host(request)
        subject_type = "ip"
        role: UserRole | None = None

        if token_data is not None:
            tenant_id = token_data.tenant_id or "platform"
            subject_id = str(token_data.user_id)
            subject_type = "user"
            role = token_data.role

        if api_key:
            subject_id = api_key.strip()
            subject_type = "api_key"

        override_header = request.headers.get(app_settings.TENANT_RATE_LIMIT_OVERRIDE_HEADER, "")
        override_requested = override_header.strip().lower() in {"1", "true", "yes", "emergency"}

        request.state.tenant_id = tenant_id
        request.state.rate_limit_subject_type = subject_type
        if role is not None:
            request.state.user_role = role.value

        return RateLimitIdentity(
            tenant_id=tenant_id,
            subject_id=subject_id,
            subject_type=subject_type,
            role=role,
            override_requested=override_requested,
        )

    def check_identity(self, identity: RateLimitIdentity) -> RateLimitDecision:
        key = self._key(identity)
        if not self.enabled:
            return RateLimitDecision(True, self.limit, self.limit, self.window_seconds, key)

        if identity.role == UserRole.SUPER_ADMIN and identity.override_requested:
            tenant_metrics.record_rate_limit_bypass(
                tenant_id=identity.tenant_id,
                role=identity.role.value,
            )
            return RateLimitDecision(
                True,
                self.limit,
                self.limit,
                self.window_seconds,
                key,
                bypassed=True,
            )

        try:
            count, ttl = self._increment(key)
        except RedisError as exc:
            LOG.warning("Tenant rate limit Redis error: {}", exc)
            if self.fail_open:
                return RateLimitDecision(True, self.limit, self.limit, self.window_seconds, key)
            count, ttl = self._increment_memory(key)

        remaining = max(self.limit - count, 0)
        allowed = count <= self.limit
        if not allowed:
            tenant_metrics.record_rate_limit_hit(
                tenant_id=identity.tenant_id,
                subject_type=identity.subject_type,
            )
        return RateLimitDecision(allowed, self.limit, remaining, ttl, key)

    def should_skip(self, request: Request) -> bool:
        if request.method.upper() == "OPTIONS":
            return True
        path = request.url.path.rstrip("/") or "/"
        if path in SKIPPED_RATE_LIMIT_PATHS:
            return True
        return path.startswith("/docs/") or path.startswith("/redoc/")

    def _decode_bearer(self, request: Request):
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        try:
            return SecurityUtils.decode_token(token.strip(), "access")
        except UnauthorizedException:
            return None

    def _client_host(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For") if app_settings.TRUST_PROXY_HEADERS else None
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    def _key(self, identity: RateLimitIdentity) -> str:
        return f"ratelimit:{identity.tenant_id}:{identity.subject_type}:{identity.subject_id}"

    def _redis_client(self) -> Redis | None:
        if not self._redis_url or self._redis_failed:
            return None
        if self._redis is None:
            try:
                self._redis = Redis.from_url(
                    self._redis_url,
                    socket_connect_timeout=app_settings.PRESENCE_REDIS_CONNECT_TIMEOUT_SECONDS,
                    socket_timeout=app_settings.PRESENCE_REDIS_SOCKET_TIMEOUT_SECONDS,
                    decode_responses=True,
                )
            except RedisError as exc:
                self._redis_failed = True
                LOG.warning("Could not initialize Redis tenant rate limiter: {}", exc)
                return None
        return self._redis

    def _increment(self, key: str) -> tuple[int, int]:
        redis_client = self._redis_client()
        if redis_client is None:
            return self._increment_memory(key)

        count = int(redis_client.incr(key))
        if count == 1:
            redis_client.expire(key, self.window_seconds)
            ttl = self.window_seconds
        else:
            ttl = int(redis_client.ttl(key))
            if ttl < 0:
                redis_client.expire(key, self.window_seconds)
                ttl = self.window_seconds
        return count, ttl

    def _increment_memory(self, key: str) -> tuple[int, int]:
        now = time()
        with self._lock:
            count, expires_at = self._memory.get(key, (0, now + self.window_seconds))
            if expires_at <= now:
                count = 0
                expires_at = now + self.window_seconds
            count += 1
            self._memory[key] = (count, expires_at)
            ttl = max(int(expires_at - now), 1)
        return count, ttl


tenant_rate_limiter = TenantRateLimiter()


class TenantRateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce tenant/user fixed-window limits before hitting route handlers."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if tenant_rate_limiter.should_skip(request):
            return await call_next(request)

        identity = tenant_rate_limiter.identify(request)
        decision = tenant_rate_limiter.check_identity(identity)
        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Tenant rate limit exceeded. Please try again later.",
                    "tenant_id": identity.tenant_id,
                    "retry_after_seconds": decision.reset_seconds,
                },
                headers={
                    "Retry-After": str(decision.reset_seconds),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(decision.reset_seconds),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        response.headers["X-RateLimit-Reset"] = str(decision.reset_seconds)
        if decision.bypassed:
            response.headers["X-RateLimit-Bypass"] = "super_admin_emergency"
        return response
