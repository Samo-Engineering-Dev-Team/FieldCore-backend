from fastapi import APIRouter, Query
from typing import Any

from app.models.system_settings import (
    SystemSettingResponse,
    SystemSettingsResponse,
    SystemSettingUpdate,
    SystemSettingsBulkUpdate,
    DebugConfig,
)
from app.services.system_settings import SystemSettingsService
from app.services import CurrentUser
from app.database import Session
from app.utils.enums import UserRole
from app.exceptions.http import UnauthorizedException
from app.core.settings import app_settings

router = APIRouter(prefix="/settings", tags=["System Settings"])


def _require_admin(current_user):
    """Check if user has admin privileges."""
    if current_user.role != UserRole.ADMIN:
        raise UnauthorizedException("Only administrators can access system settings")


@router.get("/", response_model=SystemSettingsResponse, status_code=200)
def get_all_settings(
    service: SystemSettingsService,
    session: Session,
    current_user: CurrentUser,
) -> SystemSettingsResponse:
    """Get all system settings grouped by category. Admin only."""
    _require_admin(current_user)
    return service.get_settings_grouped(session)


@router.get("/list", response_model=list[SystemSettingResponse], status_code=200)
def get_settings_list(
    service: SystemSettingsService,
    session: Session,
    current_user: CurrentUser,
    category: str | None = Query(default=None, description="Filter by category"),
) -> list[SystemSettingResponse]:
    """Get all system settings as a flat list. Admin only."""
    _require_admin(current_user)
    if category:
        return service.get_settings_by_category(category, session)
    return service.get_all_settings(session)


@router.get("/debug", response_model=DebugConfig, status_code=200)
def get_debug_config(
    service: SystemSettingsService,
    session: Session,
    current_user: CurrentUser,
) -> DebugConfig:
    """Get current debug configuration. Admin only."""
    _require_admin(current_user)
    return service.get_debug_config(session)


@router.get("/debug/status", response_model=dict, status_code=200)
def get_debug_status(
    service: SystemSettingsService,
    session: Session,
) -> dict:
    """Get debug mode status. Public endpoint for middleware checks."""
    return {
        "debug_mode": service.get_setting("debug_mode", session, False),
        "enable_performance_headers": service.get_setting("enable_performance_headers", session, False),
    }


@router.get("/{key}", response_model=SystemSettingResponse, status_code=200)
def get_setting(
    key: str,
    service: SystemSettingsService,
    session: Session,
    current_user: CurrentUser,
) -> SystemSettingResponse:
    """Get a specific setting by key. Admin only."""
    _require_admin(current_user)
    return service.get_setting_full(key, session)


@router.patch("/{key}", response_model=SystemSettingResponse, status_code=200)
def update_setting(
    key: str,
    payload: SystemSettingUpdate,
    service: SystemSettingsService,
    session: Session,
    current_user: CurrentUser,
) -> SystemSettingResponse:
    """Update a specific setting. Admin only."""
    _require_admin(current_user)
    return service.update_setting(key, payload, session)


@router.patch("/", response_model=SystemSettingsResponse, status_code=200)
def bulk_update_settings(
    payload: SystemSettingsBulkUpdate,
    service: SystemSettingsService,
    session: Session,
    current_user: CurrentUser,
) -> SystemSettingsResponse:
    """Bulk update multiple settings. Admin only."""
    _require_admin(current_user)
    return service.bulk_update_settings(payload, session)


@router.post("/email/test", response_model=dict, status_code=200)
async def test_email(
    current_user: CurrentUser,
) -> dict:
    """
    Send a test email to the NOC distribution list to verify SMTP config.
    Admin only. Check your Mailtrap (or real) inbox after calling this.
    """
    _require_admin(current_user)

    if not app_settings.smtp_enabled:
        return {
            "ok": False,
            "message": "SMTP is not configured. Set SMTP_HOST, SMTP_USER and SMTP_PASSWORD in .env.",
            "smtp_host": app_settings.SMTP_HOST or "(not set)",
            "recipients": [],
        }

    if not app_settings.noc_email_list:
        return {
            "ok": False,
            "message": "NOC_EMAIL_ADDRESSES is empty — no recipients to send to.",
            "smtp_host": app_settings.SMTP_HOST,
            "recipients": [],
        }

    from app.services.email import EmailService
    EmailService.send_task_completed(
        ref_no="TEST-001",
        site_name="Test Site (email verification)",
        technician_name="System Administrator",
        task_type="email_test",
        completed_at="Now",
    )

    return {
        "ok": True,
        "message": "Test email queued. Check your Mailtrap inbox in a few seconds.",
        "smtp_host": app_settings.SMTP_HOST,
        "smtp_port": app_settings.SMTP_PORT,
        "from": f"{app_settings.SMTP_FROM_NAME} <{app_settings.SMTP_USER}>",
        "recipients": app_settings.noc_email_list,
    }


@router.post("/debug/toggle", response_model=dict, status_code=200)
def toggle_debug_mode(
    service: SystemSettingsService,
    session: Session,
    current_user: CurrentUser,
) -> dict:
    """Toggle debug mode on/off. Admin only."""
    _require_admin(current_user)
    
    current_value = service.get_setting("debug_mode", session, False)
    new_value = not current_value
    
    service.update_setting("debug_mode", SystemSettingUpdate(value=new_value), session)
    
    # Also toggle related debug settings
    service.update_setting("enable_request_logging", SystemSettingUpdate(value=new_value), session)
    service.update_setting("enable_performance_headers", SystemSettingUpdate(value=new_value), session)
    
    return {
        "debug_mode": new_value,
        "message": f"Debug mode {'enabled' if new_value else 'disabled'}",
        "affected_settings": [
            "debug_mode",
            "enable_request_logging", 
            "enable_performance_headers",
            "log_level"
        ]
    }
