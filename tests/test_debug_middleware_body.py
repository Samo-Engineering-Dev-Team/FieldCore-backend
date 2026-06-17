"""Regression guard for M2.

When request logging is enabled, DebugMiddleware reads the request body to log
it. This test proves the drained body is still delivered intact to the route
handler (relies on Starlette's _CachedRequest). If a future Starlette upgrade
breaks that contract, this test fails loudly.
"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.debug_middleware import DebugMiddleware, invalidate_debug_cache


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(DebugMiddleware)

    @app.post("/echo")
    async def echo(request: Request):
        data = await request.json()
        return {"received": data}

    return app


def test_post_body_survives_debug_logging(monkeypatch) -> None:
    # Force request-logging path (no DB → middleware falls back to env vars).
    monkeypatch.setenv("DEBUG_MODE", "true")
    monkeypatch.setenv("ENABLE_REQUEST_LOGGING", "true")
    invalidate_debug_cache()

    client = TestClient(_app())
    try:
        response = client.post("/echo", json={"hello": "world", "n": 42})
        assert response.status_code == 200
        assert response.json() == {"received": {"hello": "world", "n": 42}}
    finally:
        invalidate_debug_cache()


def test_post_body_intact_without_logging() -> None:
    invalidate_debug_cache()
    client = TestClient(_app())
    response = client.post("/echo", json={"a": 1})
    assert response.status_code == 200
    assert response.json() == {"received": {"a": 1}}
