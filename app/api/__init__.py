from fastapi import APIRouter, Depends
from .v1 import router as v1_router
from .v2 import router as v2_router
from .v1.route_patrol import router as route_patrol_router
from app.services.auth import get_current_user

router = APIRouter(prefix="/api")
router.include_router(v1_router)
router.include_router(v2_router)
router.include_router(
    route_patrol_router,
    dependencies=[Depends(get_current_user)],
    include_in_schema=False,
)
