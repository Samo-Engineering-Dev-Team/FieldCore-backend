from fastapi import APIRouter, Depends
from .auth import router as auth_router
from .user import router as user_router
from .technician import router as technician_router
from .site import router as site_router
from .task import router as task_router
from .incident import router as incident_router
from .report import router as report_router
from .notification import router as notification_router
from .access_request import router as access_request_router
from .routine_check import router as routine_check_router
from .routine_issue import router as routine_issue_router
from .routine_inspection import router as routine_inspection_router
from .management_dashboard import router as management_dashboard_router
from .file import router as file_router
from .client import router as client_router
from .webhook import router as webhook_router
from .presence import router as sessions_router
from .system_settings import router as system_settings_router
from .incident_report import router as incident_report_router
from .maintenance_schedule import router as maintenance_schedule_router
from .route_patrol import router as route_patrol_router
from .billing import router as billing_router
from .licensing import router as licensing_router
from .tenant import router as tenant_router
from .audit import router as audit_router
from .template import router as template_router
from .support import router as support_router
from app.services.auth import get_current_user
from os import getenv
_allow_dev = getenv("ALLOW_DEV_ENDPOINTS", "false").lower() == "true"
if _allow_dev:
	from .dev_client import router as dev_client_router

router = APIRouter(prefix="/v1")
router.include_router(auth_router)
router.include_router(billing_router)
router.include_router(user_router, dependencies=[Depends(get_current_user)])
router.include_router(technician_router, dependencies=[Depends(get_current_user)])
router.include_router(site_router, dependencies=[Depends(get_current_user)])
router.include_router(task_router, dependencies=[Depends(get_current_user)])
router.include_router(incident_router, dependencies=[Depends(get_current_user)])
router.include_router(report_router, dependencies=[Depends(get_current_user)])
router.include_router(notification_router, dependencies=[Depends(get_current_user)])
router.include_router(access_request_router, dependencies=[Depends(get_current_user)])
router.include_router(routine_check_router, dependencies=[Depends(get_current_user)])
router.include_router(routine_issue_router, dependencies=[Depends(get_current_user)])
router.include_router(routine_inspection_router, dependencies=[Depends(get_current_user)])
router.include_router(management_dashboard_router, dependencies=[Depends(get_current_user)])
router.include_router(file_router, dependencies=[Depends(get_current_user)])
router.include_router(client_router, dependencies=[Depends(get_current_user)])
router.include_router(webhook_router, dependencies=[Depends(get_current_user)])
router.include_router(sessions_router, dependencies=[Depends(get_current_user)])
router.include_router(system_settings_router, dependencies=[Depends(get_current_user)])
router.include_router(incident_report_router, dependencies=[Depends(get_current_user)])
router.include_router(maintenance_schedule_router, dependencies=[Depends(get_current_user)])
router.include_router(route_patrol_router, dependencies=[Depends(get_current_user)])
router.include_router(licensing_router, dependencies=[Depends(get_current_user)])
router.include_router(tenant_router, dependencies=[Depends(get_current_user)])
router.include_router(audit_router, dependencies=[Depends(get_current_user)])
router.include_router(template_router, dependencies=[Depends(get_current_user)])
router.include_router(support_router, dependencies=[Depends(get_current_user)])
if _allow_dev:
	router.include_router(dev_client_router)
