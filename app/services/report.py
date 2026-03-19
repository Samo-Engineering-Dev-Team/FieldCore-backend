from uuid import UUID
from io import BytesIO
from fastapi import Depends
from typing import List, Annotated, Any
from sqlmodel import Session, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy import and_
from sqlalchemy.orm import object_session
from sqlalchemy.orm import selectinload
from loguru import logger as LOG
import time

from app.utils.enums import ReportType, ReportStatus
from app.models import Report, ReportCreate, ReportUpdate, ReportResponse, Task, Technician
from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
    ForbiddenException
)
from app.services.pdf import get_pdf_service


class _ReportService:
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
        """Normalize a single attachment object to a frontend-friendly shape."""
        if isinstance(item, str):
            return {
                "url": item,
                "public_url": item,
                "file_path": None,
                "original_name": None,
                "content_type": None,
                "size": None,
            }

        if isinstance(item, dict):
            # Prefer stable public URL when available to avoid persisting expired signed URLs.
            url = item.get("public_url") or item.get("url") or item.get("signed_url")
            file_path = item.get("file_path")
            if not url and isinstance(file_path, str):
                from app.services.file import FileService
                url = FileService().get_public_url(file_path)

            return {
                "url": url,
                "signed_url": item.get("signed_url"),
                "public_url": item.get("public_url") or url,
                "file_path": file_path,
                "original_name": item.get("original_name") or item.get("name") or item.get("filename"),
                "content_type": item.get("content_type"),
                "size": item.get("size"),
            }

        return {
            "url": None,
            "public_url": None,
            "file_path": None,
            "original_name": None,
            "content_type": None,
            "size": None,
        }

    def _normalize_attachments(self, attachments: Any) -> dict[str, Any] | None:
        """Normalize attachments into canonical shape: {'files': [...]}."""
        if attachments is None:
            return None

        files: list[dict[str, Any]] = []

        if isinstance(attachments, list):
            files = [self._normalize_attachment_item(item) for item in attachments]
        elif isinstance(attachments, str):
            files = [self._normalize_attachment_item(attachments)]
        elif isinstance(attachments, dict):
            if isinstance(attachments.get("files"), list):
                files = [self._normalize_attachment_item(item) for item in attachments["files"]]
            elif any(k in attachments for k in ("url", "public_url", "file_path", "filename", "name")):
                files = [self._normalize_attachment_item(attachments)]
            else:
                # Legacy key/value attachment maps: {"before": "https://..."}
                for key, value in attachments.items():
                    normalized = self._normalize_attachment_item(value)
                    normalized["label"] = key
                    files.append(normalized)
        else:
            return None

        cleaned_files = [f for f in files if f.get("url") or f.get("file_path")]
        return {"files": cleaned_files}

    def report_to_response(self, report: Report) -> ReportResponse:
        technician_name = "Unknown Technician"
        if report.technician and report.technician.user:
            technician_name = f"{report.technician.user.name} {report.technician.user.surname}"

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
            seacom_ref=seacom_ref
            )

    def create_report(self, data: ReportCreate, session: Session) -> ReportResponse:
        report_data = data.model_dump()
        report_data["attachments"] = self._normalize_attachments(report_data.get("attachments"))
        report: Report = Report(**report_data)
        try:
            session.add(report)
            session.commit()
            session.refresh(report)
            
            # Get task and technician info for notification
            task = session.exec(select(Task).where(Task.id == data.task_id)).first()
            technician = session.exec(select(Technician).where(Technician.id == data.technician_id)).first()
            
            if task and technician:
                # Create notification for NOC operators about new report
                from app.services.notification import _NotificationService, NotificationTemplates
                from app.models import User
                from app.utils.enums import UserRole
                
                notification_service = _NotificationService()
                
                # Notify all NOC operators
                noc_users = session.exec(
                    select(User).where(
                        and_(
                            User.role == UserRole.NOC,
                            User.deleted_at.is_(None)
                        )
                    )
                ).all()
                
                # Get site name safely
                site_name = task.site.name if task.site else "Unknown Site"
                technician_name = technician.user.name if technician.user else "Unknown Technician"
                
                notification_service.create_notifications_from_template(
                    user_ids=(noc_user.id for noc_user in noc_users),
                    template=NotificationTemplates.report_submitted(
                        technician_name=technician_name,
                        report_type=data.report_type,
                        site_name=site_name,
                    ),
                    session=session,
                )
            
            return self.report_to_response(report)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating report: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating report: {e}")

    def read_report(self, report_id: UUID, session: Session) -> ReportResponse:
        report = self._get_report(report_id, session)
        return self.report_to_response(report)

    def read_reports(
        self,
        session: Session,
        report_type: ReportType | None = None,
        status: ReportStatus | None = None,
        technician_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[ReportResponse]:
        statement = (
            select(Report)
            .options(
                selectinload(Report.task).selectinload(Task.site),
                selectinload(Report.technician).selectinload(Technician.user),
            )
            .where(Report.deleted_at.is_(None))
        )  # type: ignore

        if report_type is not None:
            statement = statement.where(Report.report_type == report_type)
        if status is not None:
            statement = statement.where(Report.status == status)
        if technician_id is not None:
            statement = statement.where(Report.technician_id == technician_id)

        statement = statement.offset(offset).limit(limit)
        reports = session.exec(statement).all()
        return [self.report_to_response(report) for report in reports]

    def update_report(
        self, report_id: UUID, data: ReportUpdate, session: Session
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
                # Step 1: Fetch the report
                report = self._get_report(report_id, session)
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
                    LOG.debug("No allowed report update fields provided for {}", report_id)
                    return self.report_to_response(report)

                # Step 5: Apply updates and touch timestamp
                if "attachments" in filtered_data:
                    filtered_data["attachments"] = self._normalize_attachments(
                        filtered_data.get("attachments")
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

    def delete_report(self, report_id: UUID, session: Session) -> None:
        report = self._get_report(report_id, session)
        report.soft_delete()
        session.commit()
    
    def start_report(self, report_id: UUID, session: Session) -> ReportResponse:
        """"""
        report = self._get_report(report_id, session)
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
    
    def complete_report(self, report_id: UUID, session: Session) -> ReportResponse:
        """"""
        report = self._get_report(report_id, session)
        report.complete()
        try:
            session.commit()
            session.refresh(report)
            return self.report_to_response(report)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error completing report: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error completing report: {e}")

    def export_report_pdf(self, report_id: UUID, session: Session) -> tuple[BytesIO, str]:
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
                raise InternalServerErrorException("Failed to generate PDF: empty buffer")
            
            # Generate filename
            report_type = report.report_type.value.replace("-", "_")
            created_date = report.created_at.strftime("%Y%m%d") if report.created_at else "unknown"
            filename = f"report_{report_type}_{created_date}_{str(report.id)[:8]}.pdf"

            # Persist generated PDF to Supabase storage (best-effort, export still succeeds on storage failure).
            try:
                from app.services.file import FileService

                file_service = FileService()
                stored = file_service.upload_file_sync(
                    file_content=pdf_bytes,
                    filename=filename,
                    content_type="application/pdf",
                    folder=f"reports/{report.id}/exports",
                )
                LOG.info(
                    "report_pdf_stored report_id={} file_path={} public_url={}",
                    report_id,
                    stored.get("file_path"),
                    stored.get("public_url"),
                )
            except Exception as storage_error:
                LOG.warning(
                    "report_pdf_storage_failed report_id={} error_type={} detail={}",
                    report_id,
                    type(storage_error).__name__,
                    storage_error,
                )
            
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
