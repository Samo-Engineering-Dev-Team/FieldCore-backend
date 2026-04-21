from typing import Any

from fastapi import APIRouter, Request

from app.database import Session
from app.exceptions.http import BadRequestException
from app.models import BillingWebhookIngestResponse
from app.services.billing import BillingSubscriptionServiceDep, get_billing_connector


router = APIRouter(prefix="/billing", tags=["Billing"])


@router.post("/webhooks/{provider}", response_model=BillingWebhookIngestResponse, status_code=200)
async def ingest_billing_webhook(
    provider: str,
    request: Request,
    service: BillingSubscriptionServiceDep,
    session: Session,
) -> BillingWebhookIngestResponse:
    """Ingest billing provider webhook events and update tenant subscription state."""
    payload: Any = await request.json()
    if not isinstance(payload, dict):
        raise BadRequestException("billing webhook payload must be a JSON object")

    connector = get_billing_connector(provider)
    event = connector.parse_webhook(payload, request.headers)
    return service.ingest_webhook_event(event, session)
