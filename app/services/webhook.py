import httpx
import json
import hmac
import hashlib
import asyncio
from typing import Dict, Any, List
from sqlmodel import select
from app.database import Database
from app.models import Webhook
from loguru import logger as LOG
from app.services.tenant_scope import normalize_tenant_id, require_tenant_id


class WebhookService:
    @staticmethod
    async def send_webhook(
        event_type: str,
        payload: Dict[str, Any],
        *,
        tenant_id: str | None = None,
    ) -> None:
        """Send webhook notifications for a specific event type."""
        try:
            scoped_tenant_id = normalize_tenant_id(tenant_id or payload.get("tenant_id"))
            if scoped_tenant_id is None:
                LOG.warning("Skipping webhook '{}' because tenant_id is missing", event_type)
                return

            scoped_payload = dict(payload)
            scoped_payload["tenant_id"] = scoped_tenant_id

            with Database.session() as session:
                webhooks = session.exec(
                    select(Webhook).where(
                        Webhook.event_type == event_type,
                        Webhook.is_active == True,
                        Webhook.tenant_id == scoped_tenant_id,
                    )
                ).all()

            if not webhooks:
                return

            async with httpx.AsyncClient(timeout=10.0) as client:
                results = await asyncio.gather(
                    *(
                        WebhookService._send_to_webhook(webhook, scoped_payload, client)
                        for webhook in webhooks
                    ),
                    return_exceptions=True,
                )
                for webhook, result in zip(webhooks, results):
                    if isinstance(result, Exception):
                        LOG.error(f"Webhook delivery error for {webhook.url}: {result}")

        except Exception as e:
            LOG.error(f"Error sending webhooks for {event_type}: {e}")

    @staticmethod
    async def _send_to_webhook(webhook: Webhook, payload: Dict[str, Any], client: httpx.AsyncClient) -> None:
        """Send payload to a specific webhook URL."""
        try:
            headers = {"Content-Type": "application/json"}

            # Add signature if secret is provided
            if webhook.secret:
                payload_str = json.dumps(payload, sort_keys=True)
                signature = hmac.new(
                    webhook.secret.encode(),
                    payload_str.encode(),
                    hashlib.sha256
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={signature}"

            response = await client.post(
                webhook.url,
                json=payload,
                headers=headers
            )

            if response.status_code >= 400:
                LOG.warning(f"Webhook failed: {webhook.url} - {response.status_code}: {response.text}")
            else:
                LOG.info(f"Webhook sent successfully: {webhook.url}")

        except Exception as e:
            LOG.error(f"Error sending webhook to {webhook.url}: {e}")

    @staticmethod
    def register_webhook(
        url: str,
        event_type: str,
        tenant_id: str | None,
        secret: str = None,
    ) -> Webhook:
        """Register a new webhook."""
        with Database.session() as session:
            webhook = Webhook(
                tenant_id=require_tenant_id(tenant_id),
                url=url,
                event_type=event_type,
                secret=secret,
            )
            session.add(webhook)
            session.commit()
            session.refresh(webhook)
            return webhook

    @staticmethod
    def list_webhooks(tenant_id: str | None, event_type: str = None) -> List[Webhook]:
        """List active webhooks, optionally filtered by event type."""
        scoped_tenant_id = require_tenant_id(tenant_id)
        with Database.session() as session:
            query = select(Webhook).where(
                Webhook.is_active == True,
                Webhook.tenant_id == scoped_tenant_id,
            )
            if event_type:
                query = query.where(Webhook.event_type == event_type)
            return session.exec(query).all()

    @staticmethod
    def deactivate_webhook(webhook_id: int, tenant_id: str | None) -> bool:
        """Deactivate a webhook."""
        scoped_tenant_id = require_tenant_id(tenant_id)
        with Database.session() as session:
            webhook = session.exec(
                select(Webhook).where(
                    Webhook.id == webhook_id,
                    Webhook.tenant_id == scoped_tenant_id,
                )
            ).first()
            if webhook:
                webhook.is_active = False
                session.commit()
                return True
            return False
