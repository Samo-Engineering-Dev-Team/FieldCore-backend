"""Celery tasks for incident notifications/email/webhooks (L2).

Thin wrappers around the existing ``_bg_notify_*`` helpers in the incident
service. Arguments are JSON-serializable (UUIDs passed as strings) so they
survive the broker round-trip; conversion to UUID happens here.
"""

from uuid import UUID

from app.core.celery_app import celery_app


@celery_app.task(
    name="notifications.incident_created",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def notify_incident_created(
    self,
    technician_id: str,
    site_name: str,
    tech_name: str,
    description: str,
    assigning_user_id: str | None,
) -> None:
    from app.services.incident import _bg_notify_incident_created

    try:
        _bg_notify_incident_created(
            technician_id=UUID(technician_id),
            site_name=site_name,
            tech_name=tech_name,
            description=description,
            assigning_user_id=UUID(assigning_user_id) if assigning_user_id else None,
        )
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)


@celery_app.task(
    name="notifications.incident_started",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def notify_incident_started(self, site_name: str, tech_name: str) -> None:
    from app.services.incident import _bg_notify_incident_started

    try:
        _bg_notify_incident_started(site_name=site_name, tech_name=tech_name)
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)


@celery_app.task(
    name="notifications.incident_resolved",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def notify_incident_resolved(
    self,
    site_name: str,
    tech_name: str,
    ref_no: str | None,
    severity: str,
    description: str,
) -> None:
    from app.services.incident import _bg_notify_incident_resolved

    try:
        _bg_notify_incident_resolved(
            site_name=site_name,
            tech_name=tech_name,
            ref_no=ref_no,
            severity=severity,
            description=description,
        )
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)
