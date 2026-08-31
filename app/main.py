# import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# from strawberry.fastapi import GraphQLRouter
from loguru import logger as LOG
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api import router
from app.core import app_settings
from app.core.debug_middleware import DebugMiddleware
from app.core.rate_limiter import limiter
from app.core.scheduler import shutdown_scheduler, start_scheduler
from app.core.security_headers import SecurityHeadersMiddleware
from app.database import Database
from app.services.file import LOCAL_UPLOAD_ROOT

# from app.graphql.schema import schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    LOG.info("Starting application lifespan")
    try:
        Database.connect(app_settings.database_url)
        LOG.info("Database connected")
    except Exception as e:
        LOG.exception(f"Database connection failed: {e}")
        raise
    Database.init()
    LOG.debug("Database init complete")

    # Start SLA + weekly background checkers (APScheduler, advisory-locked).
    start_scheduler()

    yield

    LOG.info("Shutting down application lifespan")
    shutdown_scheduler()

    Database.disconnect()
    LOG.info("Database disconnected")


fastapi_app: FastAPI = FastAPI(
    title="Seacom-App",
    version="0.1.0",
    description="Backend API for Seacom field technician management system",
    lifespan=lifespan,
)

fastapi_app.state.limiter = limiter


@fastapi_app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )


fastapi_app.add_middleware(SecurityHeadersMiddleware)
fastapi_app.add_middleware(DebugMiddleware)
fastapi_app.add_middleware(SlowAPIMiddleware)

_cors_origins = app_settings.allowed_origins
if not _cors_origins:
    LOG.warning(
        "ALLOWED_ORIGINS is not set - all cross-origin browser requests will be "
        "blocked. Set ALLOWED_ORIGINS to a comma-separated list of trusted origins."
    )

fastapi_app.include_router(router)

if app_settings.is_development:
    # Serves what app/services/file.py writes to disk in development, so an
    # uploaded slip/photo/report attachment can be fetched back by the same
    # URL a Supabase-backed upload would have produced.
    LOCAL_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    fastapi_app.mount(
        "/local-uploads", StaticFiles(directory=LOCAL_UPLOAD_ROOT), name="local-uploads"
    )

# GraphQL router
# graphql_app = GraphQLRouter(schema)
# app.include_router(graphql_app, prefix="/graphql")


@fastapi_app.get("/", include_in_schema=False, status_code=307)
def root():
    """Redirect to the API docs."""
    return RedirectResponse(fastapi_app.docs_url or "/docs")


@fastapi_app.get("/health", tags=["Health"])
def health() -> dict:
    """Liveness probe - process is up. No dependency checks."""
    return {"status": "ok"}


@fastapi_app.get("/ready", tags=["Health"])
def ready() -> JSONResponse:
    """Readiness probe - verifies the database is reachable."""
    from sqlalchemy import text

    try:
        with Database.session() as session:
            session.execute(text("SELECT 1"))
        return JSONResponse(status_code=200, content={"status": "ready"})
    except Exception as exc:
        LOG.warning("Readiness check failed: {}", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "detail": "database unavailable"},
        )


@fastapi_app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    LOG.debug("Validation error: {}", exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


app = CORSMiddleware(
    fastapi_app,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
