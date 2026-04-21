from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import asyncio
from strawberry.fastapi import GraphQLRouter
from loguru import logger as LOG

from app.database import Database
from app.core import app_settings
from app.core.metrics import TenantMetricsMiddleware, tenant_metrics
from app.core.rate_limiter import TenantRateLimitMiddleware, limiter
from app.core.debug_middleware import DebugMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.api import router
from app.graphql.schema import schema


# Background task for SLA checking
async def sla_check_background_task():
    """Background task that periodically checks for SLA breaches."""
    from loguru import logger as LOG
    
    # Wait for startup to complete
    await asyncio.sleep(30)
    
    while True:
        try:
            from sqlmodel import Session
            from app.services.sla_checker import check_sla_breaches
            
            with Session(Database.connection) as session:
                warnings, breaches = check_sla_breaches(session)
                
                if warnings or breaches:
                    LOG.info(f"SLA Check: {len(warnings)} warnings, {len(breaches)} breaches found")
        except Exception as e:
            LOG.error(f"SLA check error: {e}")
            import traceback
            LOG.error(f"SLA check traceback: {traceback.format_exc()}")
        
        # Check every 15 minutes
        await asyncio.sleep(15 * 60)


async def licensing_metering_background_task():
    """Optional background task that snapshots tenant usage/compliance once per interval."""
    await asyncio.sleep(app_settings.LICENSING_METERING_STARTUP_DELAY_SECONDS)

    from sqlmodel import Session
    from app.services.licensing_compliance import LicensingComplianceService
    from app.utils.funcs import utcnow

    service = LicensingComplianceService()

    while True:
        try:
            if Database.connection is None:
                raise RuntimeError("database connection is not available")

            with Session(Database.connection) as session:
                summary = service.compute_daily_metering(session, usage_date=utcnow().date())
                LOG.info(
                    "Licensing metering run complete for {}: {} tenants, {} overage tenants",
                    summary.usage_date,
                    summary.processed_tenant_count,
                    summary.overage_tenant_count,
                )
        except Exception as e:
            LOG.error(f"Licensing metering error: {e}")
            import traceback
            LOG.error(f"Licensing metering traceback: {traceback.format_exc()}")

        await asyncio.sleep(app_settings.LICENSING_METERING_JOB_INTERVAL_SECONDS)


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
    
    # Start SLA check background task
    # sla_task = asyncio.create_task(sla_check_background_task())
    metering_task = None
    if app_settings.LICENSING_METERING_JOB_ENABLED:
        metering_task = asyncio.create_task(licensing_metering_background_task())
    
    yield
    
    LOG.info("Shutting down application lifespan")
    # Cancel background task on shutdown
    # sla_task.cancel()
    # try:
    #     await sla_task
    # except asyncio.CancelledError:
    #     pass
    if metering_task is not None:
        metering_task.cancel()
        try:
            await metering_task
        except asyncio.CancelledError:
            pass
    
    Database.disconnect()
    LOG.info("Database disconnected")


app: FastAPI = FastAPI(
    title="Seacom-App",
    version="0.1.0",
    description="Backend API for Seacom field technician management system",
    lifespan=lifespan,
    docs_url="/docs" if app_settings.api_docs_enabled else None,
    redoc_url="/redoc" if app_settings.api_docs_enabled else None,
    openapi_url="/openapi.json" if app_settings.api_docs_enabled else None,
)

app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."}
    )

# Middleware order: last added = outermost (processes request first)
# CORS must be outermost so preflight OPTIONS requests are handled before anything else
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(DebugMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(TenantRateLimitMiddleware)
app.add_middleware(TenantMetricsMiddleware)
if app_settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=app_settings.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router)

# GraphQL router
# graphql_app = GraphQLRouter(schema)
# app.include_router(graphql_app, prefix="/graphql")


@app.get("/", include_in_schema=False, status_code=307)
def root() -> RedirectResponse | JSONResponse:
    """"""
    if app.docs_url:
        return RedirectResponse(app.docs_url)
    return JSONResponse({"status": "ok"})


if app_settings.ENABLE_METRICS_ENDPOINT:
    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> PlainTextResponse:
        expected_token = app_settings.METRICS_BEARER_TOKEN
        if expected_token:
            authorization = request.headers.get("Authorization", "")
            if authorization != f"Bearer {expected_token}":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        elif app_settings.is_production:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        return PlainTextResponse(
            tenant_metrics.render_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    LOG.debug("Validation error: {}", exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )
