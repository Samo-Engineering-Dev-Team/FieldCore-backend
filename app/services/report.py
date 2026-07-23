from uuid import UUID
from io import BytesIO
from fastapi import Depends
from typing import List, Annotated, Any
from sqlmodel import Session, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload
from loguru import logger as LOG
import time

from app.utils.enums import ReportType, ReportStatus, TaskType, UserRole
from app.models import (
    Report,
    ReportCreate,
    ReportUpdate,
    ReportResponse,
    Task,
    Technician,
)
from app.models.auth import TokenData
from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
    ForbiddenException,
)
from app.services.pdf import get_pdf_service
from app.services.authorization import (
    require_report_export,
    require_report_read,
    require_report_write,
)
from app.services.maintenance_schedule import get_maintenance_schedule_service
from app.services.report_support import (
    create_noc_notifications,
    normalize_attachment_item,
    normalize_attachments,
    validate_report_data_schema,
)


class _ReportService:
    FIELD_REPORT_SCHEDULE_TYPES = {
        ReportType.ROUTINE_DRIVE: "routine_drive",
        ReportType.REPEATER: "repeater_site_visit",
        ReportType.DIESEL: "generator_diesel_refill",
    }

    @staticmethod
    def _is_lock_or_timeout_error(error: Exception) -> bool:
        error_text = str(error).lower()
        return any(
            marker in error_text
            for marker in (
                "lock timeout",
                "locknotavailable",
                "could not obtain lock",
                "canceling statement due to statement timeout",
                "querycanceled",
                "deadlock detected",
            )
        )

    def _normalize_attachment_item(self, item: Any) -> dict[str, Any]:
        """Normalize attachment item via shared report support helper."""
        return normalize_attachment_item(item)

    def _normalize_attachments(self, attachments: Any) -> dict[str, Any] | None:
        """Normalize attachments into canonical shape via shared helper."""
        return normalize_attachments(attachments)

    def report_to_response(self, report: Report) -> ReportResponse:
        technician_name = "Unknown Technician"
        if report.technician and report.technician.user:
            technician_name = (
                f"{report.technician.user.name} {report.technician.user.surname}"
            )

        attachments = self._normalize_attachments(report.attachments) or {"files": []}
        num_attachments = len(attachments.get("files", []))

        # Get seacom_ref from report, or fall back to task's seacom_ref
        seacom_ref = report.seacom_ref
        if not seacom_ref and report.task:
            seacom_ref = report.task.seacom_ref

        # Build response, excluding seacom_ref from dump to avoid duplicate
        report_data = report.model_dump(exclude={"seacom_ref", "attachments"})
        return ReportResponse(
            **report_data,
            attachments=attachments,
            num_attachments=num_attachments,
            technician_fullname=technician_name,
            seacom_ref=seacom_ref,
        )

    def _get_technician_by_user(self, user_id: UUID, session: Session) -> Technician:
        statement = select(Technician).where(
            Technician.user_id == user_id,
            Technician.deleted_at.is_(None),  # type: ignore
        )
        technician: Technician | None = session.exec(statement).first()
        if not technician:
            raise NotFoundException("technician profile not found for current user")
        return technician

    def _assert_can_access_report(
        self,
        report: Report,
        current_user: TokenData,
        session: Session,
        action: str,
    ) -> None:
        require_report_read(
            current_user,
            f"You do not have permission to {action} reports.",
        )

        if current_user.role != UserRole.TECHNICIAN:
            return

        technician = self._get_technician_by_user(current_user.user_id, session)
        if report.technician_id != technician.id:
            raise ForbiddenException(f"Technicians can only {action} their own reports")

    def create_report(
        self,
        data: ReportCreate,
        session: Session,
        current_user: TokenData,
    ) -> ReportResponse:
        require_report_write(
            current_user,
            "You do not have permission to create reports.",
        )

        if current_user.role == UserRole.TECHNICIAN:
            technician = self._get_technician_by_user(current_user.user_id, session)
            if data.technician_id != technician.id:
                raise ForbiddenException(
                    "Technicians can only create reports for themselves"
                )
            data = data.model_copy(update={"technician_id": technician.id})

        report_data = data.model_dump()
        report_data["attachments"] = self._normalize_attachments(
            report_data.get("attachments")
        )
        validate_report_data_schema(report_data["report_type"], report_data.get("data"))
        report: Report = Report(**report_data)
        try:
            session.add(report)
            session.commit()
            session.refresh(report)

            # Get task and technician info for notification
            task = session.exec(select(Task).where(Task.id == data.task_id)).first()
            technician = session.exec(
                select(Technician).where(Technician.id == data.technician_id)
            ).first()

            if task and technician:
                # Create notification for NOC operators about new report
                from app.services.notification import NotificationTemplates

                # Get site name safely
                site_name = task.site.name if task.site else "Unknown Site"
                technician_name = (
                    technician.user.name if technician.user else "Unknown Technician"
                )

                create_noc_notifications(
                    session=session,
                    template=NotificationTemplates.report_submitted(
                        technician_name=technician_name,
                        report_type=data.report_type,
                        site_name=site_name,
                    ),
                )

            return self.report_to_response(report)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating report: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating report: {e}")

    def read_report(
        self,
        report_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> ReportResponse:
        report = self._get_report(report_id, session)
        self._assert_can_access_report(report, current_user, session, "view")
        return self.report_to_response(report)

    def read_reports(
        self,
        session: Session,
        current_user: TokenData,
        report_type: ReportType | None = None,
        status: ReportStatus | None = None,
        technician_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[ReportResponse]:
        require_report_read(
            current_user,
            "You do not have permission to view reports.",
        )

        statement = (
            select(Report)
            .options(
                selectinload(Report.task).selectinload(Task.site),
                selectinload(Report.technician).selectinload(Technician.user),
            )
            .where(Report.deleted_at.is_(None))
        )  # type: ignore

        if current_user.role == UserRole.TECHNICIAN:
            technician = self._get_technician_by_user(current_user.user_id, session)
            statement = statement.where(Report.technician_id == technician.id)
        elif technician_id is not None:
            statement = statement.where(Report.technician_id == technician_id)

        if report_type is not None:
            statement = statement.where(Report.report_type == report_type)
        if status is not None:
            statement = statement.where(Report.status == status)

        statement = statement.offset(offset).limit(limit)
        reports = session.exec(statement).all()
        return [self.report_to_response(report) for report in reports]

    def update_report(
        self,
        report_id: UUID,
        data: ReportUpdate,
        session: Session,
        current_user: TokenData,
    ) -> ReportResponse:
        """
        Update a report with the provided data.
        Only allows updating: data, attachments, status

        Note: The broken audit_report_changes trigger must be dropped in Supabase
        before this will work. Run the fix_trigger.sql script.
        """
        max_lock_retries = 3

        for attempt in range(max_lock_retries + 1):
            try:
                require_report_write(
                    current_user,
                    "You do not have permission to update reports.",
                )

                # Step 1: Fetch the report
                report = self._get_report(report_id, session)
                self._assert_can_access_report(report, current_user, session, "update")
                update_data = data.model_dump(
                    exclude_none=True, exclude_defaults=True, exclude_unset=True
                )

                LOG.debug(
                    "Report update payload received for {}: {}",
                    report_id,
                    update_data,
                )

                # Step 2: Validate state
                if report.status in [ReportStatus.COMPLETED]:
                    raise ForbiddenException("Cannot update a completed report.")

                # Step 3: Early exit if no data
                if not update_data:
                    LOG.debug("No report update data provided for {}", report_id)
                    return self.report_to_response(report)

                # Step 4: Filter allowed fields only
                allowed_fields = {"data", "attachments", "status"}
                filtered_data = {
                    k: v for k, v in update_data.items() if k in allowed_fields
                }

                if not filtered_data:
                    LOG.debug(
                        "No allowed report update fields provided for {}", report_id
                    )
                    return self.report_to_response(report)

                # Step 5: Apply updates and touch timestamp
                if "attachments" in filtered_data:
                    filtered_data["attachments"] = self._normalize_attachments(
                        filtered_data.get("attachments")
                    )
                if "data" in filtered_data:
                    validate_report_data_schema(
                        report.report_type, filtered_data.get("data")
                    )

                has_changes = False
                for key, value in filtered_data.items():
                    current_value = getattr(report, key)
                    if current_value != value:
                        LOG.debug("Updating report field '{}' for {}", key, report_id)
                        setattr(report, key, value)
                        has_changes = True

                if not has_changes:
                    LOG.debug("No report changes detected for {}", report_id)
                    return self.report_to_response(report)

                report.touch()

                # Step 6: Commit changes
                session.add(report)
                # Fail reasonably fast on contention, then retry a few times.
                session.exec(text("SET LOCAL lock_timeout = '5s'"))
                session.exec(text("SET LOCAL statement_timeout = '20s'"))
                session.commit()

                # Step 7: Refresh and return
                session.refresh(report)
                LOG.info("Report {} updated successfully", report_id)
                return self.report_to_response(report)

            except ForbiddenException:
                raise
            except NotFoundException:
                raise
            except IntegrityError as e:
                session.rollback()
                LOG.error(
                    "report_update_failed report_id={} operation=update reason=integrity_error detail={}",
                    report_id,
                    e.orig,
                )
                raise ConflictException(f"Failed to update report: {e.orig}")
            except OperationalError as e:
                session.rollback()
                lock_or_timeout = self._is_lock_or_timeout_error(e)
                if lock_or_timeout and attempt < max_lock_retries:
                    backoff_seconds = 0.5 * (attempt + 1)
                    LOG.warning(
                        "report_update_retry report_id={} attempt={}/{} backoff_seconds={} detail={}",
                        report_id,
                        attempt + 1,
                        max_lock_retries,
                        backoff_seconds,
                        e,
                    )
                    time.sleep(backoff_seconds)
                    continue

                LOG.error(
                    "report_update_failed report_id={} operation=update reason=operational_error lock_or_timeout={} detail={}",
                    report_id,
                    lock_or_timeout,
                    e,
                )
                if lock_or_timeout:
                    raise ConflictException(
                        "Report is currently being updated by another request. Please retry."
                    )
                raise InternalServerErrorException(
                    "Report update failed due to a transient database connection issue."
                )
            except Exception as e:
                session.rollback()
                error_str = str(e)
                error_lower = error_str.lower()
                trigger_hints = (
                    "audit_report_changes",
                    "trg_audit_report_changes",
                    "trigger",
                    "plpgsql",
                )
                is_trigger_error = any(hint in error_lower for hint in trigger_hints)

                LOG.exception(
                    "report_update_failed report_id={} operation=update trigger_hint={} error_type={} detail={}",
                    report_id,
                    is_trigger_error,
                    type(e).__name__,
                    e,
                )

                if is_trigger_error:
                    raise InternalServerErrorException(
                        "Report update failed due to database trigger configuration. "
                        "Run scripts/fix_trigger.sql on the database, then retry."
                    )
                raise InternalServerErrorException(
                    "Report update failed due to an unexpected server-side database error."
                )

        raise ConflictException(
            "Report is currently being updated by another request. Please retry."
        )

    def delete_report(
        self,
        report_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> None:
        require_report_write(
            current_user,
            "You do not have permission to delete reports.",
        )

        report = self._get_report(report_id, session)
        self._assert_can_access_report(report, current_user, session, "delete")
        report.soft_delete()
        self._delete_self_started_field_work_task(report)
        session.commit()

    def _delete_self_started_field_work_task(self, report: Report) -> None:
        task = report.task
        if not task:
            return

        if report.status == ReportStatus.COMPLETED:
            return

        is_self_started_field_work = (
            task.task_type == TaskType.ROUTINE_MAINTENANCE
            and task.assigned_by_name == "Technician self-started"
            and task.technician_id == report.technician_id
        )
        if is_self_started_field_work:
            task.soft_delete()

    def start_report(
        self,
        report_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> ReportResponse:
        """"""
        require_report_write(
            current_user,
            "You do not have permission to start reports.",
        )

        report = self._get_report(report_id, session)
        self._assert_can_access_report(report, current_user, session, "start")
        report.start()
        try:
            session.commit()
            session.refresh(report)
            return self.report_to_response(report)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error starting report: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error starting report: {e}")

    def complete_report(
        self,
        report_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> ReportResponse:
        """"""
        require_report_write(
            current_user,
            "You do not have permission to complete reports.",
        )

        report = self._get_report(report_id, session)
        self._assert_can_access_report(report, current_user, session, "complete")
        report.complete()
        self._complete_field_work_context(report, session)
        try:
            session.commit()
            session.refresh(report)
            return self.report_to_response(report)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error completing report: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error completing report: {e}"
            )

    def _complete_field_work_context(self, report: Report, session: Session) -> None:
        task = report.task
        if not task or task.task_type != TaskType.ROUTINE_MAINTENANCE:
            return

        schedule_type = self.FIELD_REPORT_SCHEDULE_TYPES.get(report.report_type)
        if not schedule_type:
            return

        if task.status != "completed":
            task.complete()
            session.add(task)

        completed_at = report.updated_at or task.completed_at or task.end_time
        if completed_at and task.site_id:
            get_maintenance_schedule_service().mark_schedule_done_for_field_work(
                session=session,
                technician_id=report.technician_id,
                site_id=task.site_id,
                schedule_type=schedule_type,
                completed_at=completed_at,
            )

    def export_report_pdf(
        self,
        report_id: UUID,
        session: Session,
        current_user: TokenData | None = None,
    ) -> tuple[BytesIO, str]:
        """
        Export a completed report as a PDF document.

        Args:
            report_id: The UUID of the report to export
            session: Database session

        Returns:
            Tuple of (PDF buffer, filename)

        Raises:
            NotFoundException: If report not found
            ForbiddenException: If report is not completed
        """
        report = self._get_report(report_id, session)

        if current_user is not None:
            require_report_export(
                current_user,
                "You do not have permission to export reports.",
            )
            self._assert_can_access_report(report, current_user, session, "export")

        if report.status != ReportStatus.COMPLETED:
            raise ForbiddenException("Only completed reports can be exported as PDF")

        try:
            # Ensure relationships are loaded
            session.refresh(report)

            pdf_service = get_pdf_service()
            pdf_buffer = pdf_service.generate_report_pdf(report)

            # Verify buffer has content
            pdf_bytes = pdf_buffer.getvalue()
            if not pdf_bytes:
                raise InternalServerErrorException(
                    "Failed to generate PDF: empty buffer"
                )

            # Generate filename
            report_type = report.report_type.value.replace("-", "_")
            created_date = (
                report.created_at.strftime("%Y%m%d") if report.created_at else "unknown"
            )
            filename = f"report_{report_type}_{created_date}_{str(report.id)[:8]}.pdf"

            # Reset buffer for reading
            pdf_buffer.seek(0)
            return pdf_buffer, filename
        except ForbiddenException:
            raise
        except NotFoundException:
            raise
        except Exception as e:
            raise InternalServerErrorException(f"Failed to generate PDF: {str(e)}")

    def _get_report(self, report_id: UUID, session: Session) -> Report:
        statement = (
            select(Report)
            .options(
                selectinload(Report.task).selectinload(Task.site),
                selectinload(Report.technician).selectinload(Technician.user),
            )
            .where(Report.id == report_id, Report.deleted_at.is_(None))
        )  # type: ignore
        report: Report | None = session.exec(statement).first()
        if not report:
            raise NotFoundException("report not found")
        return report


def get_report_service() -> _ReportService:
    return _ReportService()


ReportService = Annotated[_ReportService, Depends(get_report_service)]
