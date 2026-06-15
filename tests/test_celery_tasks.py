"""L2 — Celery notification tasks.

With no broker configured (test env), Celery runs eagerly, so .delay executes
the task inline. These tests confirm the tasks are registered and dispatch to
the underlying notification helpers with arguments intact.
"""

import app.services.incident as incident_service
from app.core.celery_app import celery_app
from app.tasks.notifications import (
    notify_incident_created,
    notify_incident_resolved,
    notify_incident_started,
)


def test_eager_mode_active_without_broker() -> None:
    assert celery_app.conf.task_always_eager is True


def test_tasks_registered() -> None:
    names = set(celery_app.tasks.keys())
    assert "notifications.incident_created" in names
    assert "notifications.incident_started" in names
    assert "notifications.incident_resolved" in names


def test_incident_created_dispatches(monkeypatch) -> None:
    calls = {}

    def fake(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr(incident_service, "_bg_notify_incident_created", fake)

    uid = "123e4567-e89b-12d3-a456-426614174000"
    notify_incident_created.delay(
        technician_id=uid,
        site_name="Site A",
        tech_name="Jane Doe",
        description="fibre cut",
        assigning_user_id=None,
    )

    assert str(calls["technician_id"]) == uid
    assert calls["site_name"] == "Site A"
    assert calls["assigning_user_id"] is None


def test_incident_started_and_resolved_dispatch(monkeypatch) -> None:
    started = {}
    resolved = {}
    monkeypatch.setattr(
        incident_service,
        "_bg_notify_incident_started",
        lambda **k: started.update(k),
    )
    monkeypatch.setattr(
        incident_service,
        "_bg_notify_incident_resolved",
        lambda **k: resolved.update(k),
    )

    notify_incident_started.delay(site_name="S", tech_name="T")
    notify_incident_resolved.delay(
        site_name="S", tech_name="T", ref_no="R1", severity="major", description="d"
    )

    assert started == {"site_name": "S", "tech_name": "T"}
    assert resolved["ref_no"] == "R1"
    assert resolved["severity"] == "major"
