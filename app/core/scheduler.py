"""Background scheduler for SLA + weekly maintenance checks (M1).

Re-enables the previously-disabled automated checks using APScheduler's
``BackgroundScheduler`` (its own thread pool, so the synchronous, IO-heavy
checker functions never block the uvicorn event loop).

Each job is wrapped in a PostgreSQL session-level advisory lock so that if the
service is scaled to multiple instances, only one runner executes a given job
per tick — the others see the lock held and skip.
"""

from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger as LOG
from sqlalchemy import text
from sqlmodel import Session

from app.core.settings import app_settings
from app.database import Database

# Arbitrary but stable advisory-lock keys (one per job).
_SLA_LOCK_KEY = 471001
_WEEKLY_LOCK_KEY = 471002

_scheduler: BackgroundScheduler | None = None


def _run_locked(lock_key: int, job_name: str, fn: Callable[[Session], object]) -> None:
    """Run ``fn(session)`` only if we win the advisory lock for ``lock_key``."""
    engine = Database.connection
    if engine is None:
        LOG.warning("Scheduler job '{}' skipped: database not connected", job_name)
        return

    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        acquired = conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}
        ).scalar()
        if not acquired:
            LOG.info(
                "Scheduler job '{}' skipped: advisory lock held by another instance",
                job_name,
            )
            return
        try:
            with Database.session() as session:
                result = fn(session)
            LOG.info("Scheduler job '{}' completed: {}", job_name, result)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
    except Exception as exc:  # noqa: BLE001 — never let a job kill the scheduler
        LOG.exception("Scheduler job '{}' failed: {}", job_name, exc)
    finally:
        conn.close()


def _sla_job() -> None:
    from app.services.sla_checker import check_sla_breaches

    def run(session: Session) -> str:
        warnings, breaches = check_sla_breaches(session)
        return f"{len(warnings)} warning(s), {len(breaches)} breach(es)"

    _run_locked(_SLA_LOCK_KEY, "sla_check", run)


def _weekly_job() -> None:
    from app.services.weekly_checker import check_weekly_scheduled_tasks

    _run_locked(_WEEKLY_LOCK_KEY, "weekly_check", check_weekly_scheduled_tasks)


def start_scheduler() -> None:
    """Start the background scheduler. No-op if disabled or already running."""
    global _scheduler

    if not app_settings.SCHEDULER_ENABLED:
        LOG.info("Scheduler disabled (SCHEDULER_ENABLED=false)")
        return
    if _scheduler is not None:
        LOG.warning("Scheduler already started")
        return

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _sla_job,
        IntervalTrigger(minutes=app_settings.SLA_CHECK_INTERVAL_MINUTES),
        id="sla_check",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _weekly_job,
        CronTrigger(hour=app_settings.WEEKLY_CHECK_HOUR_UTC, minute=0),
        id="weekly_check",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    LOG.info(
        "Scheduler started: SLA check every {} min, weekly check daily at {:02d}:00 UTC",
        app_settings.SLA_CHECK_INTERVAL_MINUTES,
        app_settings.WEEKLY_CHECK_HOUR_UTC,
    )


def shutdown_scheduler() -> None:
    """Stop the background scheduler if running."""
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    LOG.info("Scheduler stopped")
