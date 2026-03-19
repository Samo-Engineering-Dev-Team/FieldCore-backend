from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4, UUID

from sqlmodel import select

from app.core.settings import app_settings
from app.database import Database
from app.exceptions.http import ForbiddenException
from app.models import Report, ReportUpdate, Site, Task, Technician
from app.services.report import _ReportService
from app.utils.enums import ReportStatus, ReportType, TaskType
from app.utils.funcs import utcnow


@dataclass
class TestResult:
    name: str
    ok: bool
    detail: str = ""


def _pick_site_and_technician():
    with Database.session() as session:
        site = session.exec(select(Site).where(Site.deleted_at.is_(None))).first()  # type: ignore[arg-type]
        tech = session.exec(select(Technician).where(Technician.deleted_at.is_(None))).first()  # type: ignore[arg-type]
        if not site or not tech:
            raise RuntimeError("Missing site/technician rows required for report smoke test.")
        return site.id, tech.id


def _create_temp_task(report_type: ReportType, site_id: UUID, technician_id: UUID) -> UUID:
    with Database.session() as session:
        start_at = utcnow()
        task = Task(
            seacom_ref=f"qa-task-{uuid4().hex[:8]}",
            description=f"QA smoke task for {report_type.value}",
            start_time=start_at,
            end_time=start_at + timedelta(hours=1),
            task_type=TaskType.RHS,
            report_type=report_type.value,
            attachments=None,
            site_id=site_id,
            technician_id=technician_id,
            additional_technician_ids=[],
            hold_reason=None,
            held_at=None,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def _create_temp_report(report_type: ReportType, task_id: UUID, technician_id: UUID) -> UUID:
    with Database.session() as session:
        report = Report(
            report_type=report_type,
            data={
                "qa_smoke": True,
                "report_type": report_type.value,
                "seed": uuid4().hex[:8],
            },
            attachments={"files": []},
            service_provider="qa-smoke",
            seacom_ref=f"qa-{report_type.value}-{uuid4().hex[:6]}",
            technician_id=technician_id,
            task_id=task_id,
            status=ReportStatus.PENDING,
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        return report.id


def _cleanup_report(report_id: UUID) -> None:
    with Database.session() as session:
        report = session.exec(
            select(Report).where(Report.id == report_id, Report.deleted_at.is_(None))  # type: ignore[arg-type]
        ).first()
        if report:
            report.soft_delete()
            session.add(report)
            session.commit()


def _cleanup_task(task_id: UUID) -> None:
    with Database.session() as session:
        task = session.exec(
            select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))  # type: ignore[arg-type]
        ).first()
        if task:
            task.soft_delete()
            session.add(task)
            session.commit()


def _test_report_flow(report_type: ReportType, site_id: UUID, technician_id: UUID) -> list[TestResult]:
    service = _ReportService()
    results: list[TestResult] = []
    task_id = _create_temp_task(report_type, site_id, technician_id)
    report_id = _create_temp_report(report_type, task_id, technician_id)
    results.append(TestResult(f"{report_type.value}:create_temp_report", True, str(report_id)))

    try:
        with Database.session() as session:
            read = service.read_report(report_id, session)
            results.append(TestResult(f"{report_type.value}:read_report", read.id == report_id))

        with Database.session() as session:
            updated = service.update_report(
                report_id,
                ReportUpdate(
                    data={
                        "qa_smoke": True,
                        "report_type": report_type.value,
                        "updated": True,
                        "ts": int(time.time()),
                    },
                    attachments={"files": []},
                ),
                session,
            )
            results.append(
                TestResult(
                    f"{report_type.value}:update_report",
                    bool(updated.data.get("updated") is True),
                )
            )

        with Database.session() as session:
            started = service.start_report(report_id, session)
            results.append(
                TestResult(
                    f"{report_type.value}:start_report",
                    started.status == ReportStatus.STARTED,
                )
            )

        with Database.session() as session:
            completed = service.complete_report(report_id, session)
            results.append(
                TestResult(
                    f"{report_type.value}:complete_report",
                    completed.status == ReportStatus.COMPLETED,
                )
            )

        with Database.session() as session:
            try:
                service.update_report(
                    report_id,
                    ReportUpdate(data={"should_fail": True}),
                    session,
                )
                results.append(
                    TestResult(
                        f"{report_type.value}:update_after_complete",
                        False,
                        "Expected ForbiddenException but update succeeded.",
                    )
                )
            except ForbiddenException:
                session.rollback()
                results.append(
                    TestResult(f"{report_type.value}:update_after_complete", True)
                )

        with Database.session() as session:
            pdf_buffer, filename = service.export_report_pdf(report_id, session)
            pdf_size = len(pdf_buffer.getvalue())
            results.append(
                TestResult(
                    f"{report_type.value}:export_report_pdf",
                    pdf_size > 0 and filename.endswith(".pdf"),
                    f"size={pdf_size}",
                )
            )

        with Database.session() as session:
            service.delete_report(report_id, session)
            deleted = session.exec(
                select(Report).where(Report.id == report_id)  # type: ignore[arg-type]
            ).first()
            results.append(
                TestResult(
                    f"{report_type.value}:delete_report",
                    bool(deleted and deleted.deleted_at is not None),
                )
            )
    finally:
        _cleanup_report(report_id)
        _cleanup_task(task_id)

    return results


def _test_lock_retry(site_id: UUID, technician_id: UUID) -> TestResult:
    service = _ReportService()
    task_id = _create_temp_task(ReportType.REPEATER, site_id, technician_id)
    report_id = _create_temp_report(ReportType.REPEATER, task_id, technician_id)

    def hold_lock() -> None:
        with Database.session() as lock_session:
            row = lock_session.exec(
                select(Report).where(Report.id == report_id).with_for_update()  # type: ignore[arg-type]
            ).first()
            if row:
                time.sleep(6)
                lock_session.commit()

    lock_thread = threading.Thread(target=hold_lock, daemon=True)
    lock_thread.start()
    # Give locker time to acquire the lock.
    time.sleep(0.6)

    try:
        with Database.session() as session:
            start = time.perf_counter()
            updated = service.update_report(
                report_id,
                ReportUpdate(
                    data={
                        "qa_lock_retry": True,
                        "ts": int(time.time()),
                    }
                ),
                session,
            )
            duration = time.perf_counter() - start
            ok = bool(updated.data.get("qa_lock_retry") is True)
            return TestResult(
                "lock_retry:update_report_under_lock",
                ok,
                f"duration_seconds={duration:.2f}",
            )
    finally:
        lock_thread.join(timeout=15)
        _cleanup_report(report_id)
        _cleanup_task(task_id)


def _test_read_reports_filters() -> list[TestResult]:
    service = _ReportService()
    results: list[TestResult] = []
    with Database.session() as session:
        for report_type in ReportType:
            rows = service.read_reports(
                session=session,
                report_type=report_type,
                status=None,
                technician_id=None,
                offset=0,
                limit=20,
            )
            type_match = all(r.report_type == report_type for r in rows)
            results.append(
                TestResult(
                    f"read_reports:{report_type.value}:filter_by_type",
                    type_match,
                    f"rows={len(rows)}",
                )
            )
    return results


def main() -> int:
    Database.connect(app_settings.database_url)
    all_results: list[TestResult] = []
    try:
        site_id, technician_id = _pick_site_and_technician()

        for report_type in ReportType:
            all_results.extend(_test_report_flow(report_type, site_id, technician_id))

        all_results.extend(_test_read_reports_filters())
        all_results.append(_test_lock_retry(site_id, technician_id))
    finally:
        Database.disconnect()

    failed = [r for r in all_results if not r.ok]
    for result in all_results:
        status = "PASS" if result.ok else "FAIL"
        detail = f" ({result.detail})" if result.detail else ""
        print(f"[{status}] {result.name}{detail}")

    print(f"\nSummary: {len(all_results) - len(failed)}/{len(all_results)} passed")
    if failed:
        print("Failures:")
        for item in failed:
            print(f" - {item.name}: {item.detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
