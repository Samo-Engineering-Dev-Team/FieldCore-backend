import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.api.v1.billing import router as billing_router
from app.database import Database
from app.exceptions.http import ConflictException
from app.models import Tenant, TenantSubscription, TenantSubscriptionState
from app.services.billing import BillingSubscriptionService, MockBillingConnector


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            TenantSubscription.__table__,
        ],
    )
    with Session(engine) as session:
        yield session


def _add_tenant(session: Session, tenant_id: str = "tenant-alpha") -> None:
    session.add(Tenant(id=tenant_id, slug=tenant_id, name="Tenant Alpha"))
    session.commit()


def test_tenant_subscription_state_machine_blocks_cancelled_reactivation() -> None:
    subscription = TenantSubscription(tenant_id="tenant-alpha")

    assert subscription.can_transition_to(TenantSubscriptionState.ACTIVE) is True
    assert subscription.transition_to(TenantSubscriptionState.ACTIVE) is True
    assert subscription.transition_to(TenantSubscriptionState.OVERDUE) is True
    assert subscription.transition_to(TenantSubscriptionState.SUSPENDED) is True
    assert subscription.transition_to(TenantSubscriptionState.CANCELLED) is True

    assert subscription.can_transition_to(TenantSubscriptionState.ACTIVE) is False
    with pytest.raises(ValueError, match="Cannot transition"):
        subscription.transition_to(TenantSubscriptionState.ACTIVE)


def test_billing_webhook_event_updates_subscription_and_is_idempotent(session: Session) -> None:
    _add_tenant(session)
    connector = MockBillingConnector()
    service = BillingSubscriptionService()

    event = connector.parse_webhook(
        {
            "id": "evt_001",
            "type": "subscription.activated",
            "tenant_id": "tenant-alpha",
            "subscription_id": "sub_001",
            "metadata": {"plan": "pro"},
        },
        {},
    )
    response = service.ingest_webhook_event(event, session)

    assert response.processed is True
    assert response.previous_state == TenantSubscriptionState.TRIAL
    assert response.new_state == TenantSubscriptionState.ACTIVE
    assert response.subscription.billing_metadata["provider_subscription_id"] == "sub_001"
    assert response.subscription.billing_metadata["plan"] == "pro"

    duplicate = service.ingest_webhook_event(event, session)
    assert duplicate.processed is False
    assert duplicate.new_state == TenantSubscriptionState.ACTIVE

    subscriptions = list(session.exec(select(TenantSubscription)).all())
    assert len(subscriptions) == 1
    assert subscriptions[0].billing_metadata["processed_event_ids"] == ["evt_001"]

    failed_payment = connector.parse_webhook(
        {
            "id": "evt_002",
            "type": "invoice.payment_failed",
            "data": {"tenant_id": "tenant-alpha", "subscription_id": "sub_001"},
        },
        {},
    )
    overdue = service.ingest_webhook_event(failed_payment, session)
    assert overdue.previous_state == TenantSubscriptionState.ACTIVE
    assert overdue.new_state == TenantSubscriptionState.OVERDUE


def test_billing_service_rejects_invalid_transition(session: Session) -> None:
    _add_tenant(session)
    connector = MockBillingConnector()
    service = BillingSubscriptionService()

    cancelled = connector.parse_webhook(
        {"id": "evt_cancel", "type": "subscription.cancelled", "tenant_id": "tenant-alpha"},
        {},
    )
    service.ingest_webhook_event(cancelled, session)

    active = connector.parse_webhook(
        {"id": "evt_active", "type": "subscription.activated", "tenant_id": "tenant-alpha"},
        {},
    )
    with pytest.raises(ConflictException, match="Cannot transition"):
        service.ingest_webhook_event(active, session)


def test_billing_webhook_endpoint_ingests_without_user_token(session: Session) -> None:
    _add_tenant(session)
    app = FastAPI()
    app.include_router(billing_router)

    def override_session():
        yield session

    app.dependency_overrides[Database.get_session] = override_session
    client = TestClient(app)

    response = client.post(
        "/billing/webhooks/mock",
        json={
            "id": "evt_endpoint",
            "type": "subscription.activated",
            "tenant_id": "tenant-alpha",
            "subscription_id": "sub_endpoint",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["processed"] is True
    assert body["new_state"] == "active"
    assert body["subscription"]["billing_metadata"]["provider_subscription_id"] == "sub_endpoint"
