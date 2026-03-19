"""Debug middleware for request/response logging and performance timing."""
import time
import json
import os
import asyncio
from typing import Callable, Any
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from loguru import logger as LOG


class DebugMiddleware(BaseHTTPMiddleware):
    """
    Middleware that provides debug functionality:
    - Request/response body logging
    - Performance timing headers (X-Response-Time)
    - SQL query logging (when enabled)
    """

    # Cache for settings to avoid DB lookups on every request
    _settings_cache: dict[str, Any] = {}
    _cache_ttl: float = 60.0  # Cache settings for 60 seconds
    _last_cache_update: float = 0.0
    _db_available: bool = False  # Track if DB lookup succeeded

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    def _get_debug_settings(self) -> dict[str, Any]:
        """Get debug settings from cache or environment (non-blocking)."""
        current_time = time.time()
        
        # Return cached settings if still valid
        if (current_time - self._last_cache_update) < self._cache_ttl and self._settings_cache:
            return self._settings_cache
        
        # Default settings from environment variables (immediate, no DB)
        defaults = {
            "debug_mode": os.getenv("DEBUG_MODE", "false").lower() == "true",
            "enable_request_logging": os.getenv("ENABLE_REQUEST_LOGGING", "false").lower() == "true",
            "enable_sql_logging": os.getenv("ENABLE_SQL_LOGGING", "false").lower() == "true",
            "enable_performance_headers": os.getenv("ENABLE_PERFORMANCE_HEADERS", "false").lower() == "true",
        }
        
        # Only try DB lookup if we've succeeded before or haven't tried yet
        # This prevents repeated slow DB connection attempts
        if not self._db_available and self._last_cache_update > 0:
            return defaults
            
        try:
            # Lazy imports to avoid circular dependency
            from app.database import Database
            from sqlmodel import Session
            
            if Database.connection:
                from app.services.system_settings import get_system_settings_service
                
                with Session(Database.connection) as session:
                    service = get_system_settings_service()
                    self._settings_cache = {
                        "debug_mode": service.get_setting("debug_mode", session, defaults["debug_mode"]),
                        "enable_request_logging": service.get_setting("enable_request_logging", session, defaults["enable_request_logging"]),
                        "enable_sql_logging": service.get_setting("enable_sql_logging", session, defaults["enable_sql_logging"]),
                        "enable_performance_headers": service.get_setting("enable_performance_headers", session, defaults["enable_performance_headers"]),
                    }
                    self._last_cache_update = current_time
                    self._db_available = True
                    return self._settings_cache
        except Exception as e:
            LOG.debug(f"Could not load debug settings from DB: {e}")
            self._db_available = False
        
        self._last_cache_update = current_time
        self._settings_cache = defaults
        return defaults

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process the request with debug logging and timing."""
        # Skip debug processing for auth and settings endpoints to prevent delays
        skip_paths = ["/api/v1/auth/", "/api/v1/settings/debug/", "/docs", "/openapi.json", "/redoc", "/health"]
        if any(request.url.path.startswith(path) for path in skip_paths):
            return await call_next(request)

        # Run the synchronous DB lookup in a thread pool to avoid blocking the event loop
        settings = await asyncio.get_event_loop().run_in_executor(None, self._get_debug_settings)
        
        # Start timing
        start_time = time.perf_counter()
        
        # Log request if enabled
        if settings.get("enable_request_logging"):
            await self._log_request(request)
        
        # Process the request
        response = await call_next(request)
        
        # Calculate processing time
        process_time = (time.perf_counter() - start_time) * 1000  # Convert to ms
        
        # Add performance header if enabled
        if settings.get("enable_performance_headers"):
            response.headers["X-Response-Time"] = f"{process_time:.2f}ms"
            response.headers["X-Debug-Mode"] = "enabled"
        
        # Log response if enabled
        if settings.get("enable_request_logging"):
            self._log_response(request, response, process_time)
        
        return response

    async def _log_request(self, request: Request) -> None:
        """Log incoming request details."""
        # Skip logging for certain paths
        skip_paths = ["/docs", "/openapi.json", "/redoc", "/favicon.ico", "/health"]
        if any(request.url.path.startswith(path) for path in skip_paths):
            return
        
        log_data = {
            "type": "REQUEST",
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "client_ip": request.client.host if request.client else "unknown",
            "user_agent": request.headers.get("user-agent", "unknown"),
        }
        
        # Log request body for POST/PATCH/PUT (be careful with sensitive data)
        if request.method in ["POST", "PATCH", "PUT"]:
            try:
                body = await request.body()
                if body:
                    # Try to parse as JSON
                    try:
                        body_json = json.loads(body)
                        # Redact sensitive fields
                        body_json = self._redact_sensitive(body_json)
                        log_data["body"] = body_json
                    except json.JSONDecodeError:
                        log_data["body"] = f"<binary data: {len(body)} bytes>"
                    
                    # Re-create the request body for downstream handlers
                    # Note: This works with Starlette's receive pattern
            except Exception as e:
                log_data["body_error"] = str(e)
        
        LOG.debug(f"[DEBUG] {json.dumps(log_data, indent=2, default=str)}")

    def _log_response(self, request: Request, response: Response, process_time: float) -> None:
        """Log outgoing response details."""
        # Skip logging for certain paths
        skip_paths = ["/docs", "/openapi.json", "/redoc", "/favicon.ico", "/health"]
        if any(request.url.path.startswith(path) for path in skip_paths):
            return
        
        log_data = {
            "type": "RESPONSE",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "process_time_ms": f"{process_time:.2f}",
        }
        
        # Color-code by status
        if response.status_code >= 500:
            LOG.error(f"[DEBUG] {json.dumps(log_data, default=str)}")
        elif response.status_code >= 400:
            LOG.warning(f"[DEBUG] {json.dumps(log_data, default=str)}")
        else:
            LOG.debug(f"[DEBUG] {json.dumps(log_data, default=str)}")

    def _redact_sensitive(self, data: dict) -> dict:
        """Redact sensitive fields from logged data."""
        sensitive_fields = [
            "password", "password_hash", "token", "access_token", "refresh_token",
            "secret", "api_key", "authorization", "cookie", "session"
        ]
        
        if not isinstance(data, dict):
            return data
        
        redacted = {}
        for key, value in data.items():
            key_lower = key.lower()
            if any(field in key_lower for field in sensitive_fields):
                redacted[key] = "[REDACTED]"
            elif isinstance(value, dict):
                redacted[key] = self._redact_sensitive(value)
            elif isinstance(value, list):
                redacted[key] = [
                    self._redact_sensitive(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                redacted[key] = value
        
        return redacted


def invalidate_debug_cache() -> None:
    """Invalidate the debug settings cache (call when settings change)."""
    DebugMiddleware._settings_cache = {}
    DebugMiddleware._last_cache_update = 0.0
