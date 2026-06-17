from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security_headers import SecurityHeadersMiddleware


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


def test_security_headers_present_on_response() -> None:
    response = _client().get("/ping")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "max-age=31536000" in response.headers["Strict-Transport-Security"]
    assert "geolocation=()" in response.headers["Permissions-Policy"]


def test_existing_header_not_overwritten() -> None:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/custom")
    def custom():
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"ok": True}, headers={"Referrer-Policy": "strict-origin"}
        )

    response = TestClient(app).get("/custom")
    # Route-set header wins (middleware uses setdefault).
    assert response.headers["Referrer-Policy"] == "strict-origin"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
