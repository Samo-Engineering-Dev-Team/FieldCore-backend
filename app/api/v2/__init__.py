from fastapi import APIRouter, Depends
from .template_category import router as template_category_router
from .form_template import router as form_template_router
from .form_submission import router as form_submission_router
from app.services.auth import get_current_user

router = APIRouter(prefix="/v2")
router.include_router(template_category_router, dependencies=[Depends(get_current_user)])
router.include_router(form_template_router, dependencies=[Depends(get_current_user)])
router.include_router(form_submission_router, dependencies=[Depends(get_current_user)])
