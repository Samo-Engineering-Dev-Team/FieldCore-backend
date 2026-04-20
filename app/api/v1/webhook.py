from fastapi import APIRouter, HTTPException
from typing import List, Optional
from app.models import Webhook
from app.services import CurrentUser
from app.services.webhook import WebhookService
from app.services.authorization import require_management

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


from pydantic import BaseModel

class WebhookCreate(BaseModel):
    url: str
    event_type: str
    secret: Optional[str] = None

@router.post("/", response_model=Webhook)
async def register_webhook(
    webhook_data: WebhookCreate,
    current_user: CurrentUser,
):
    """Register a new webhook for event notifications."""
    try:
        require_management(current_user, "Only NOC, managers, or admins can manage webhooks.")
        webhook = WebhookService.register_webhook(
            webhook_data.url,
            webhook_data.event_type,
            tenant_id=current_user.tenant_id,
            secret=webhook_data.secret,
        )
        return webhook
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to register webhook: {str(e)}")


@router.get("/", response_model=List[Webhook])
async def list_webhooks(
    current_user: CurrentUser,
    event_type: str = None
):
    """List active webhooks, optionally filtered by event type."""
    require_management(current_user, "Only NOC, managers, or admins can manage webhooks.")
    return WebhookService.list_webhooks(current_user.tenant_id, event_type)


@router.delete("/{webhook_id}")
async def deactivate_webhook(
    webhook_id: int,
    current_user: CurrentUser,
):
    """Deactivate a webhook."""
    require_management(current_user, "Only NOC, managers, or admins can manage webhooks.")
    success = WebhookService.deactivate_webhook(webhook_id, current_user.tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"message": "Webhook deactivated"}
