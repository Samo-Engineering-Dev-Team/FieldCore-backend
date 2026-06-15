"""Celery application (L2).

Moves notification/email/webhook delivery off in-process FastAPI BackgroundTasks
onto a durable queue so work survives restarts and can retry.

If no broker is configured (local/dev/tests), tasks run eagerly (inline) so the
app behaves like before without requiring a worker.
"""

from celery import Celery

from app.core.settings import app_settings

celery_app = Celery(
    "fieldcore",
    broker=app_settings.CELERY_BROKER_URL or "memory://",
    backend=app_settings.CELERY_RESULT_BACKEND or None,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_always_eager=app_settings.celery_eager,
    # In eager mode, surface task exceptions to the caller instead of hiding them.
    task_eager_propagates=False,
    task_acks_late=True,
    worker_max_tasks_per_child=200,
)

# Import task modules so @celery_app.task registrations happen on app load.
# Done after celery_app exists to avoid a circular import.
from app.tasks import notifications  # noqa: E402,F401
