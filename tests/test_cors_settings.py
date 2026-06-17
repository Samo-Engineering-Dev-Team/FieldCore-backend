from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.settings import AppSettings


def test_allowed_origins_strip_accidental_quotes() -> None:
    settings = AppSettings.model_construct(
        ALLOWED_ORIGINS='"https://field-core-frontend.vercel.app",http://localhost:5173'
    )

    assert settings.allowed_origins == [
        "https://field-core-frontend.vercel.app",
        "http://localhost:5173",
    ]


def test_allowed_origins_do_not_fallback_to_wildcard() -> None:
    settings = AppSettings.model_construct(ALLOWED_ORIGINS="")

    assert settings.allowed_origins == []


def test_prod_frontend_origin_gets_cors_header() -> None:
    settings = AppSettings.model_construct(
        ALLOWED_ORIGINS='"https://field-core-frontend.vercel.app"'
    )
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/users/")
    def users() -> list[dict[str, str]]:
        return []

    response = TestClient(app).options(
        "/api/v1/users/?offset=0&limit=100",
        headers={
            "Origin": "https://field-core-frontend.vercel.app",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "https://field-core-frontend.vercel.app"
    )


def test_exported_app_adds_cors_header_to_unhandled_500() -> None:
    from app.main import app, fastapi_app

    @fastapi_app.get("/__test__/cors-unhandled-500", include_in_schema=False)
    def unhandled_error() -> None:
        raise RuntimeError("boom")

    response = TestClient(app, raise_server_exceptions=False).get(
        "/__test__/cors-unhandled-500",
        headers={"Origin": "https://field-core-frontend.vercel.app"},
    )

    assert response.status_code == 500
    assert (
        response.headers["access-control-allow-origin"]
        == "https://field-core-frontend.vercel.app"
    )
