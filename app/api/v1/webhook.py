from fastapi import APIRouter, HTTPException
from typing import List, Optional
from pydantic import BaseModel, HttpUrl

from app.services import CurrentUser
from app.services.authorization import require_management
from app.services.webhook import WebhookService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_MANAGE_MSG = "Only NOC, managers, or admins can manage webhooks."


class WebhookCreate(BaseModel):
    url: HttpUrl
    event_type: str
    secret: Optional[str] = None


class WebhookResponse(BaseModel):
    """Public webhook shape — never exposes the signing secret."""

    id: int
    url: str
    event_type: str
    is_active: bool


@router.post("/", response_model=WebhookResponse)
def register_webhook(webhook_data: WebhookCreate, current_user: CurrentUser):
    """Register a new webhook for event notifications. Management only.

    Sync ``def`` on purpose: the service does blocking ORM I/O, so FastAPI runs
    this in its threadpool instead of blocking the event loop (M3).
    """
    require_management(current_user, _MANAGE_MSG)
    try:
        webhook = WebhookService.register_webhook(
            str(webhook_data.url), webhook_data.event_type, webhook_data.secret
        )
        return webhook
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to register webhook: {str(e)}"
        )


@router.get("/", response_model=List[WebhookResponse])
def list_webhooks(current_user: CurrentUser, event_type: Optional[str] = None):
    """List active webhooks, optionally filtered by event type. Management only."""
    require_management(current_user, _MANAGE_MSG)
    return WebhookService.list_webhooks(event_type)


@router.delete("/{webhook_id}")
def deactivate_webhook(webhook_id: int, current_user: CurrentUser):
    """Deactivate a webhook. Management only."""
    require_management(current_user, _MANAGE_MSG)
    success = WebhookService.deactivate_webhook(webhook_id)
    if not success:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"message": "Webhook deactivated"}
