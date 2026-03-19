from types import SimpleNamespace

from app.services.task import _TaskService
from app.utils.enums import ReportType, TaskType


def make_task(report_type: str | None, task_type: TaskType):
    return SimpleNamespace(report_type=report_type, task_type=task_type)


def test_resolve_report_type_uses_valid_task_report_type() -> None:
    service = _TaskService()
    task = make_task("diesel", TaskType.RHS)

    resolved = service._resolve_report_type(task)  # type: ignore[arg-type]

    assert resolved == ReportType.DIESEL


def test_resolve_report_type_normalizes_rhs_alias() -> None:
    service = _TaskService()
    task = make_task("rhs", TaskType.RHS)

    resolved = service._resolve_report_type(task)  # type: ignore[arg-type]

    assert resolved == ReportType.DIESEL


def test_resolve_report_type_normalizes_corrective_alias() -> None:
    service = _TaskService()
    task = make_task("corrective", TaskType.RHS)

    resolved = service._resolve_report_type(task)  # type: ignore[arg-type]

    assert resolved == ReportType.DIESEL


def test_resolve_report_type_maps_routine_maintenance_alias() -> None:
    service = _TaskService()
    task = make_task("routine-maintenance", TaskType.RHS)

    resolved = service._resolve_report_type(task)  # type: ignore[arg-type]

    assert resolved == ReportType.ROUTINE_DRIVE


def test_resolve_report_type_falls_back_to_task_type_for_rhs() -> None:
    service = _TaskService()
    task = make_task("not-a-real-report-type", TaskType.RHS)

    resolved = service._resolve_report_type(task)  # type: ignore[arg-type]

    assert resolved == ReportType.DIESEL


def test_resolve_report_type_falls_back_to_task_type_for_routine() -> None:
    service = _TaskService()
    task = make_task("not-a-real-report-type", TaskType.ROUTINE_MAINTENANCE)

    resolved = service._resolve_report_type(task)  # type: ignore[arg-type]

    assert resolved == ReportType.ROUTINE_DRIVE
