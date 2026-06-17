import app.core.scheduler as sched
from app.core.scheduler import (
    _run_locked,
    shutdown_scheduler,
    start_scheduler,
)


def teardown_function() -> None:
    # Ensure no scheduler leaks between tests.
    shutdown_scheduler()


def test_start_scheduler_disabled_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(sched.app_settings, "SCHEDULER_ENABLED", False)
    start_scheduler()
    assert sched._scheduler is None


def test_start_scheduler_registers_jobs(monkeypatch) -> None:
    monkeypatch.setattr(sched.app_settings, "SCHEDULER_ENABLED", True)
    start_scheduler()
    try:
        assert sched._scheduler is not None
        job_ids = {job.id for job in sched._scheduler.get_jobs()}
        assert job_ids == {"sla_check", "weekly_check"}
    finally:
        shutdown_scheduler()
    assert sched._scheduler is None


def test_run_locked_without_db_does_not_raise(monkeypatch) -> None:
    # Database.connection is None outside app lifespan — job should skip cleanly.
    monkeypatch.setattr(sched.Database, "connection", None, raising=False)
    called = {"ran": False}

    def fn(_session):
        called["ran"] = True

    _run_locked(999999, "test_job", fn)
    assert called["ran"] is False
