from fastapi import APIRouter, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from app.models import Webhook
from app.services.webhook import WebhookService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookCreate(BaseModel):
    url: str
    event_type: str
    secret: Optional[str] = None

@router.post("/", response_model=Webhook)
async def register_webhook(
    webhook_data: WebhookCreate
):
    """Register a new webhook for event notifications."""
    try:
        webhook = WebhookService.register_webhook(webhook_data.url, webhook_data.event_type, webhook_data.secret)
        return webhook
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to register webhook: {str(e)}")


@router.get("/", response_model=List[Webhook])
async def list_webhooks(
    event_type: str = None
):
    """List active webhooks, optionally filtered by event type."""
    return WebhookService.list_webhooks(event_type)


@router.delete("/{webhook_id}")
async def deactivate_webhook(
    webhook_id: int
):
    """Deactivate a webhook."""
    success = WebhookService.deactivate_webhook(webhook_id)
    if not success:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"message": "Webhook deactivated"}