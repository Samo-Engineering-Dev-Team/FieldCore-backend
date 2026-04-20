import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from app.services.webhook import WebhookService


class _DummyExecResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DummySession:
    def __init__(self, rows):
        self.rows = rows
        self.query = None

    def exec(self, query):
        self.query = query
        return _DummyExecResult(self.rows)


class _DummyAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_send_webhook_injects_tenant_id_and_scopes_query(monkeypatch) -> None:
    dummy_session = _DummySession([SimpleNamespace(url="https://example.com", secret=None)])

    @contextmanager
    def fake_session():
        yield dummy_session

    sent_payloads: list[dict] = []

    async def fake_send_to_webhook(webhook, payload, client):
        sent_payloads.append(payload)

    monkeypatch.setattr("app.services.webhook.Database.session", fake_session)
    monkeypatch.setattr("app.services.webhook.httpx.AsyncClient", lambda timeout=10.0: _DummyAsyncClient())
    monkeypatch.setattr(
        WebhookService,
        "_send_to_webhook",
        staticmethod(fake_send_to_webhook),
    )

    asyncio.run(
        WebhookService.send_webhook(
            "sla_warning",
            {"incident_id": "incident-123"},
            tenant_id="tenant-alpha",
        )
    )

    assert sent_payloads == [{"incident_id": "incident-123", "tenant_id": "tenant-alpha"}]
    assert "webhooks.tenant_id" in str(dummy_session.query)


def test_send_webhook_skips_when_tenant_missing(monkeypatch) -> None:
    def fail_session():
        raise AssertionError("Database lookup should not run when tenant_id is missing")

    monkeypatch.setattr("app.services.webhook.Database.session", fail_session)

    asyncio.run(WebhookService.send_webhook("sla_warning", {"incident_id": "incident-123"}))
