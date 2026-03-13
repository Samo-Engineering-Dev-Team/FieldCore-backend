from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import asyncio
from strawberry.fastapi import GraphQLRouter
from loguru import logger as LOG

from app.database import Database
from app.core import app_settings
from app.core.rate_limiter import limiter
from app.core.debug_middleware import DebugMiddleware
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    LOG.info("Starting application lifespan")
    from app.core import app_settings
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
    
    yield
    
    LOG.info("Shutting down application lifespan")
    # Cancel background task on shutdown
    # sla_task.cancel()
    # try:
    #     await sla_task
    # except asyncio.CancelledError:
    #     pass
    
    Database.disconnect()
    LOG.info("Database disconnected")


app: FastAPI = FastAPI(
    title="Seacom-App",
    version="0.1.0",
    description="Backend API for Seacom field technician management system",
    lifespan=lifespan
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
app.add_middleware(DebugMiddleware)
app.add_middleware(SlowAPIMiddleware)
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
def root() -> RedirectResponse:
    """"""
    return RedirectResponse(app.docs_url or "/docs")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    LOG.debug("Validation error: {}", exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )
