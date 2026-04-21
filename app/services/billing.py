from __future__ import annotations

from typing import Annotated, Any, Mapping, Protocol

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core import app_settings
from app.exceptions.http import BadRequestException, ConflictException, NotFoundException, UnauthorizedException
from app.models import (
    BillingWebhookIngestResponse,
    Tenant,
    TenantSubscription,
    TenantSubscriptionResponse,
    TenantSubscriptionState,
)
from app.utils.funcs import utcnow


BILLING_EVENT_STATE_MAP: dict[str, TenantSubscriptionState] = {
    "subscription.trial_started": TenantSubscriptionState.TRIAL,
    "subscription.trialing": TenantSubscriptionState.TRIAL,
    "customer.subscription.trialing": TenantSubscriptionState.TRIAL,
    "subscription.created": TenantSubscriptionState.TRIAL,
    "subscription.activated": TenantSubscriptionState.ACTIVE,
    "subscription.active": TenantSubscriptionState.ACTIVE,
    "subscription.renewed": TenantSubscriptionState.ACTIVE,
    "invoice.payment_succeeded": TenantSubscriptionState.ACTIVE,
    "payment.succeeded": TenantSubscriptionState.ACTIVE,
    "subscription.payment_succeeded": TenantSubscriptionState.ACTIVE,
    "invoice.payment_failed": TenantSubscriptionState.OVERDUE,
    "payment.failed": TenantSubscriptionState.OVERDUE,
    "subscription.overdue": TenantSubscriptionState.OVERDUE,
    "subscription.suspended": TenantSubscriptionState.SUSPENDED,
    "subscription.cancelled": TenantSubscriptionState.CANCELLED,
    "subscription.canceled": TenantSubscriptionState.CANCELLED,
    "customer.subscription.deleted": TenantSubscriptionState.CANCELLED,
}

BILLING_STATUS_STATE_MAP: dict[str, TenantSubscriptionState] = {
    "trial": TenantSubscriptionState.TRIAL,
    "trialing": TenantSubscriptionState.TRIAL,
    "active": TenantSubscriptionState.ACTIVE,
    "paid": TenantSubscriptionState.ACTIVE,
    "past_due": TenantSubscriptionState.OVERDUE,
    "overdue": TenantSubscriptionState.OVERDUE,
    "unpaid": TenantSubscriptionState.OVERDUE,
    "paused": TenantSubscriptionState.SUSPENDED,
    "suspended": TenantSubscriptionState.SUSPENDED,
    "cancelled": TenantSubscriptionState.CANCELLED,
    "canceled": TenantSubscriptionState.CANCELLED,
}


class BillingWebhookEvent(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=120)
    tenant_id: str = Field(min_length=1, max_length=128)
    target_state: TenantSubscriptionState
    provider_event_id: str | None = Field(default=None, max_length=180)
    provider_subscription_id: str | None = Field(default=None, max_length=180)
    billing_metadata: dict[str, Any] = Field(default_factory=dict)


class BillingConnector(Protocol):
    provider: str

    def parse_webhook(
        self,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> BillingWebhookEvent:
        """Verify provider webhook payload and normalize it to one billing event."""


class MockBillingConnector:
    provider = "mock"

    def __init__(self, webhook_secret: str | None = None) -> None:
        self.webhook_secret = (webhook_secret or "").strip() or None

    def parse_webhook(
        self,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> BillingWebhookEvent:
        if self.webhook_secret:
            supplied_secret = (
                headers.get("x-fieldcore-billing-webhook-secret")
                or headers.get("X-FieldCore-Billing-Webhook-Secret")
                or ""
            )
            if supplied_secret != self.webhook_secret:
                raise UnauthorizedException("Invalid billing webhook secret")

        data = payload.get("data")
        if not isinstance(data, Mapping):
            data = {}

        object_data = data.get("object")
        if not isinstance(object_data, Mapping):
            object_data = {}

        metadata = payload.get("metadata") or data.get("metadata") or object_data.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            metadata = {}

        event_type = str(payload.get("event_type") or payload.get("type") or "").strip()
        if not event_type:
            raise BadRequestException("billing event_type is required")

        target_state = self._target_state(payload, data, object_data, event_type)
        tenant_id = self._first_string(
            payload.get("tenant_id"),
            data.get("tenant_id"),
            object_data.get("tenant_id"),
            metadata.get("tenant_id"),
        )
        if tenant_id is None:
            raise BadRequestException("billing webhook tenant_id is required")

        provider_event_id = self._first_string(
            payload.get("id"),
            payload.get("event_id"),
            data.get("event_id"),
        )
        provider_subscription_id = self._first_string(
            payload.get("provider_subscription_id"),
            payload.get("subscription_id"),
            data.get("provider_subscription_id"),
            data.get("subscription_id"),
            object_data.get("provider_subscription_id"),
            object_data.get("subscription_id"),
            object_data.get("id"),
            metadata.get("provider_subscription_id"),
            metadata.get("subscription_id"),
        )

        billing_metadata: dict[str, Any] = dict(metadata)
        explicit_billing_metadata = payload.get("billing_metadata")
        if isinstance(explicit_billing_metadata, Mapping):
            billing_metadata.update(explicit_billing_metadata)

        return BillingWebhookEvent(
            provider=self.provider,
            event_type=event_type,
            tenant_id=tenant_id.strip().lower(),
            target_state=target_state,
            provider_event_id=provider_event_id,
            provider_subscription_id=provider_subscription_id,
            billing_metadata=billing_metadata,
        )

    def _target_state(
        self,
        payload: Mapping[str, Any],
        data: Mapping[str, Any],
        object_data: Mapping[str, Any],
        event_type: str,
    ) -> TenantSubscriptionState:
        explicit_state = self._first_string(
            payload.get("state"),
            data.get("state"),
            object_data.get("state"),
            payload.get("status"),
            data.get("status"),
            object_data.get("status"),
        )
        if explicit_state is not None:
            normalized_state = explicit_state.strip().lower()
            target_state = BILLING_STATUS_STATE_MAP.get(normalized_state)
            if target_state is not None:
                return target_state
            try:
                return TenantSubscriptionState(normalized_state)
            except ValueError as exc:
                raise BadRequestException(f"Unsupported subscription state '{explicit_state}'") from exc

        target_state = BILLING_EVENT_STATE_MAP.get(event_type)
        if target_state is None:
            raise BadRequestException(f"Unsupported billing event_type '{event_type}'")
        return target_state

    def _first_string(self, *values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None


class BillingSubscriptionService:
    def ingest_webhook_event(
        self,
        event: BillingWebhookEvent,
        session: Session,
    ) -> BillingWebhookIngestResponse:
        tenant = session.exec(
            select(Tenant).where(Tenant.id == event.tenant_id, Tenant.deleted_at.is_(None))
        ).first()
        if not tenant:
            raise NotFoundException("Tenant not found for billing webhook")

        subscription = self._get_or_create_subscription(event, session)
        previous_state = TenantSubscriptionState(subscription.state)

        if self._already_processed(subscription, event.provider_event_id):
            return self._response(
                event,
                subscription,
                previous_state=previous_state,
                processed=False,
                message="Billing webhook event already processed",
            )

        metadata = self._merged_metadata(subscription, event)
        try:
            subscription.transition_to(event.target_state)
        except ValueError as exc:
            raise ConflictException(str(exc)) from exc

        subscription.billing_metadata = metadata
        session.add(subscription)
        session.commit()
        session.refresh(subscription)

        return self._response(
            event,
            subscription,
            previous_state=previous_state,
            processed=True,
            message="Billing webhook event processed",
        )

    def _get_or_create_subscription(
        self,
        event: BillingWebhookEvent,
        session: Session,
    ) -> TenantSubscription:
        subscription = session.exec(
            select(TenantSubscription).where(
                TenantSubscription.tenant_id == event.tenant_id,
                TenantSubscription.deleted_at.is_(None),
            )
        ).first()
        if subscription:
            return subscription

        subscription = TenantSubscription(tenant_id=event.tenant_id)
        session.add(subscription)
        session.flush()
        return subscription

    def _already_processed(
        self,
        subscription: TenantSubscription,
        provider_event_id: str | None,
    ) -> bool:
        if not provider_event_id:
            return False
        processed_event_ids = subscription.billing_metadata.get("processed_event_ids")
        if not isinstance(processed_event_ids, list):
            return False
        return provider_event_id in {str(event_id) for event_id in processed_event_ids}

    def _merged_metadata(
        self,
        subscription: TenantSubscription,
        event: BillingWebhookEvent,
    ) -> dict[str, Any]:
        now = utcnow()
        metadata = dict(subscription.billing_metadata or {})
        metadata.update(event.billing_metadata)
        metadata["provider"] = event.provider
        if event.provider_subscription_id:
            metadata["provider_subscription_id"] = event.provider_subscription_id
        metadata["last_event"] = {
            "id": event.provider_event_id,
            "type": event.event_type,
            "state": event.target_state.value,
            "processed_at": now.isoformat(),
        }

        if event.provider_event_id:
            processed_event_ids = metadata.get("processed_event_ids")
            if not isinstance(processed_event_ids, list):
                processed_event_ids = []
            if event.provider_event_id not in processed_event_ids:
                processed_event_ids.append(event.provider_event_id)
            metadata["processed_event_ids"] = processed_event_ids[-50:]

        return metadata

    def _response(
        self,
        event: BillingWebhookEvent,
        subscription: TenantSubscription,
        *,
        previous_state: TenantSubscriptionState,
        processed: bool,
        message: str,
    ) -> BillingWebhookIngestResponse:
        return BillingWebhookIngestResponse(
            provider=event.provider,
            event_type=event.event_type,
            tenant_id=event.tenant_id,
            previous_state=previous_state,
            new_state=TenantSubscriptionState(subscription.state),
            processed=processed,
            subscription=TenantSubscriptionResponse.model_validate(subscription),
            message=message,
        )


def get_billing_connector(provider: str) -> BillingConnector:
    normalized_provider = provider.strip().lower()
    if normalized_provider == "mock":
        return MockBillingConnector(app_settings.BILLING_WEBHOOK_SECRET)
    raise BadRequestException(f"Unsupported billing provider '{provider}'")


def get_billing_subscription_service() -> BillingSubscriptionService:
    return BillingSubscriptionService()


BillingSubscriptionServiceDep = Annotated[
    BillingSubscriptionService,
    Depends(get_billing_subscription_service),
]
