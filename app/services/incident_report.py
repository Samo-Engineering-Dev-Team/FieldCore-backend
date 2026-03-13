from io import BytesIO
from datetime import datetime
from typing import Annotated, List
from uuid import UUID

from fastapi import Depends
from loguru import logger as LOG
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.exceptions.http import (
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import Incident, Technician, User
from app.models.incident_report import (
    IncidentReport,
    IncidentReportCreate,
    IncidentReportResponse,
    IncidentReportUpdate,
)
from app.models.auth import TokenData
from app.utils.enums import IncidentStatus, UserRole
from app.utils.funcs import utcnow


class _IncidentReportService:

    # ─────────────────────────────────────── helpers ──────────────────────────

    def _to_response(self, report: IncidentReport) -> IncidentReportResponse:
        data = {
            "id": report.id,
            "created_at": report.created_at,
            "updated_at": report.updated_at,
            "deleted_at": report.deleted_at,
            "incident_id": report.incident_id,
            "technician_id": report.technician_id,
            "site_name": report.site_name,
            "report_date": report.report_date,
            "technician_name": report.technician_name,
            "introduction": report.introduction,
            "problem_statement": report.problem_statement,
            "findings": report.findings,
            "actions_taken": report.actions_taken,
            "root_cause_analysis": report.root_cause_analysis,
            "conclusion": report.conclusion,
            "attachments": report.attachments,
            "pdf_path": report.pdf_path,
            "pdf_url": report.pdf_url,
        }
        return IncidentReportResponse(**data)

    def _get_report(self, report_id: UUID, session: Session) -> IncidentReport:
        statement = select(IncidentReport).where(
            IncidentReport.id == report_id,
            IncidentReport.deleted_at.is_(None),  # type: ignore
        )
        report: IncidentReport | None = session.exec(statement).first()
        if not report:
            raise NotFoundException("incident report not found")
        return report

    def _get_incident(self, incident_id: UUID, session: Session) -> Incident:
        statement = select(Incident).where(
            Incident.id == incident_id,
            Incident.deleted_at.is_(None),  # type: ignore
        )
        incident: Incident | None = session.exec(statement).first()
        if not incident:
            raise NotFoundException("incident not found")
        return incident

    def _get_technician_by_user(self, user_id: UUID, session: Session) -> Technician:
        statement = select(Technician).where(
            Technician.user_id == user_id,
            Technician.deleted_at.is_(None),  # type: ignore
        )
        tech: Technician | None = session.exec(statement).first()
        if not tech:
            raise NotFoundException("technician profile not found for current user")
        return tech

    def _notify_noc(
        self,
        technician_name: str,
        site_name: str,
        session: Session,
        ref_no: str | None = None,
    ) -> None:
        try:
            from app.services.notification import _NotificationService, NotificationTemplates
            from app.services.email import EmailService
            from app.utils.funcs import utcnow

            notification_service = _NotificationService()
            noc_users = session.exec(
                select(User).where(
                    and_(User.role == UserRole.NOC, User.deleted_at.is_(None))  # type: ignore
                )
            ).all()
            notification_service.create_notifications_from_template(
                user_ids=(u.id for u in noc_users),
                template=NotificationTemplates.incident_report_submitted(
                    technician_name=technician_name,
                    site_name=site_name,
                ),
                session=session,
            )
            # Email NOC with report summary
            EmailService.send_incident_report_submitted(
                ref_no=ref_no or "N/A",
                site_name=site_name,
                technician_name=technician_name,
                submitted_at=utcnow().strftime("%d %b %Y %H:%M UTC"),
            )
        except Exception as exc:
            LOG.warning("Failed to send incident report notifications: {}", exc)

    # ─────────────────────────────────────── CRUD ─────────────────────────────

    def create_incident_report(
        self,
        data: IncidentReportCreate,
        session: Session,
        current_user: TokenData,
    ) -> IncidentReportResponse:
        incident = self._get_incident(data.incident_id, session)

        # Rule 1: Incident must be in-progress or resolved
        # (technicians submit the report and resolve in one action, so the
        #  incident is still in-progress at the point the report is created)
        if incident.status == IncidentStatus.OPEN:
            raise ForbiddenException("Cannot create a report for an open incident — start working on it first")

        # Rule 2: Technicians may only report their own incidents
        if current_user.role == UserRole.TECHNICIAN:
            technician = self._get_technician_by_user(current_user.user_id, session)
            if incident.technician_id != technician.id:
                raise ForbiddenException("Technicians can only create reports for their own incidents")
            # Auto-fill technician_id from JWT
            data = data.model_copy(update={"technician_id": technician.id})
        elif data.technician_id is None:
            # Admin/NOC creating – still need a valid technician_id
            raise ForbiddenException("technician_id is required when not submitted by a technician")

        # Rule 3: NOC operators cannot create reports
        if current_user.role == UserRole.NOC:
            raise ForbiddenException("NOC operators cannot create incident reports")

        # Rule 4: One report per incident
        existing = session.exec(
            select(IncidentReport).where(
                IncidentReport.incident_id == data.incident_id,
                IncidentReport.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if existing:
            LOG.warning("Rule 4 blocked: existing report id={} for incident_id={}", existing.id, data.incident_id)
            raise ConflictException("A report already exists for this incident")

        report_data = data.model_dump()
        if not report_data.get("report_date"):
            report_data["report_date"] = utcnow()
        if not report_data.get("technician_id"):
            report_data["technician_id"] = data.technician_id

        report = IncidentReport(**report_data)
        try:
            session.add(report)
            session.commit()
            session.refresh(report)

            # Notify NOC
            self._notify_noc(
                technician_name=data.technician_name,
                site_name=data.site_name,
                session=session,
                ref_no=getattr(data, "seacom_ref", None) or getattr(data, "ref_no", None),
            )

            return self._to_response(report)
        except IntegrityError as e:
            session.rollback()
            LOG.warning("IntegrityError creating incident report for incident_id={}: {}", data.incident_id, e.orig)
            raise ConflictException(f"A report already exists for this incident: {e.orig}")
        except Exception as e:
            LOG.exception("Unexpected error creating incident report")
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating report: {e}")

    def read_incident_report(
        self,
        report_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> IncidentReportResponse:
        report = self._get_report(report_id, session)
        self._assert_can_read(report, current_user, session)
        return self._to_response(report)

    def read_incident_reports(
        self,
        session: Session,
        current_user: TokenData,
        incident_id: UUID | None = None,
        technician_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[IncidentReportResponse]:
        statement = select(IncidentReport).where(IncidentReport.deleted_at.is_(None))  # type: ignore

        # Technicians can only see their own reports
        if current_user.role == UserRole.TECHNICIAN:
            tech = self._get_technician_by_user(current_user.user_id, session)
            statement = statement.where(IncidentReport.technician_id == tech.id)
        elif technician_id is not None:
            statement = statement.where(IncidentReport.technician_id == technician_id)

        if incident_id is not None:
            statement = statement.where(IncidentReport.incident_id == incident_id)

        statement = statement.order_by(IncidentReport.created_at.desc()).offset(offset).limit(limit)
        reports = session.exec(statement).all()
        return [self._to_response(r) for r in reports]

    def get_report_by_incident(
        self,
        incident_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> IncidentReportResponse | None:
        statement = select(IncidentReport).where(
            IncidentReport.incident_id == incident_id,
            IncidentReport.deleted_at.is_(None),  # type: ignore
        )
        report = session.exec(statement).first()
        if not report:
            return None
        self._assert_can_read(report, current_user, session)
        return self._to_response(report)

    def update_incident_report(
        self,
        report_id: UUID,
        data: IncidentReportUpdate,
        session: Session,
        current_user: TokenData,
    ) -> IncidentReportResponse:
        report = self._get_report(report_id, session)

        # NOC cannot edit
        if current_user.role == UserRole.NOC:
            raise ForbiddenException("NOC operators cannot edit incident reports")

        # Technician can only edit their own
        if current_user.role == UserRole.TECHNICIAN:
            tech = self._get_technician_by_user(current_user.user_id, session)
            if report.technician_id != tech.id:
                raise ForbiddenException("Technicians can only edit their own incident reports")

        update_data = data.model_dump(exclude_none=True, exclude_defaults=True, exclude_unset=True)
        if not update_data:
            return self._to_response(report)

        for key, value in update_data.items():
            setattr(report, key, value)

        report.touch()
        try:
            session.commit()
            session.refresh(report)
            return self._to_response(report)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating report: {e}")

    def delete_incident_report(
        self,
        report_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> None:
        report = self._get_report(report_id, session)

        if current_user.role not in (UserRole.ADMIN,):
            raise ForbiddenException("Only admins can delete incident reports")

        report.soft_delete()
        try:
            session.commit()
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error deleting report: {e}")

    # ─────────────────────────────────────── Photo upload ────────────────────

    def upload_report_photo(
        self,
        report_id: UUID,
        file_content: bytes,
        filename: str,
        content_type: str,
        session: Session,
        current_user: TokenData,
    ) -> IncidentReportResponse:
        report = self._get_report(report_id, session)

        if current_user.role == UserRole.NOC:
            raise ForbiddenException("NOC operators cannot upload photos to incident reports")
        if current_user.role == UserRole.TECHNICIAN:
            tech = self._get_technician_by_user(current_user.user_id, session)
            if report.technician_id != tech.id:
                raise ForbiddenException("Technicians can only upload photos to their own reports")

        from app.services.file import FileService

        unique_filename = f"{report_id}_{utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
        file_service = FileService()
        upload_result = file_service.upload_file_sync(
            file_content=file_content,
            filename=unique_filename,
            content_type=content_type,
            folder="incident-report-photos",
        )

        photo_entry = {
            "original_name": filename,
            "path": upload_result.get("file_path"),
            "url": upload_result.get("public_url"),
            "content_type": content_type,
            "uploaded_at": utcnow().isoformat(),
        }

        current_attachments: dict = report.attachments or {}
        photos: list = list(current_attachments.get("photos", []))
        if len(photos) >= 15:
            raise ForbiddenException("Maximum of 15 photos allowed per report")
        photos.append(photo_entry)
        report.attachments = {**current_attachments, "photos": photos}
        report.touch()

        try:
            session.commit()
            session.refresh(report)
            return self._to_response(report)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error uploading photo: {e}")

    # ─────────────────────────────────────── PDF export ───────────────────────

    def export_report_pdf(
        self,
        report_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> tuple[BytesIO, str]:
        report = self._get_report(report_id, session)

        # Technicians may only export their own report
        if current_user.role == UserRole.TECHNICIAN:
            tech = self._get_technician_by_user(current_user.user_id, session)
            if report.technician_id != tech.id:
                raise ForbiddenException("Technicians can only export their own incident reports")

        from app.services.pdf import get_pdf_service

        pdf_service = get_pdf_service()
        pdf_buffer = pdf_service.generate_incident_report_pdf(report)

        date_str = (report.report_date or utcnow()).strftime("%Y%m%d")
        filename = f"Incident_Report_{date_str}_{str(report.id)[:8]}.pdf"

        # Best-effort: store the PDF in Supabase and update the report record
        try:
            from app.services.file import FileService

            file_service = FileService()
            pdf_buffer.seek(0)
            pdf_bytes = pdf_buffer.read()
            upload_result = file_service.upload_file_sync(
                file_content=pdf_bytes,
                filename=filename,
                content_type="application/pdf",
                folder="incident-reports",
            )

            report.pdf_path = upload_result.get("file_path")
            report.pdf_url = upload_result.get("public_url")

            # Append PDF entry to attachments.files
            current_attachments: dict = report.attachments or {}
            files: list = list(current_attachments.get("files", []))
            files.append({
                "original_name": filename,
                "path": report.pdf_path,
                "public_url": report.pdf_url,
                "size": len(pdf_bytes),
                "content_type": "application/pdf",
                "uploaded_at": utcnow().isoformat(),
            })
            report.attachments = {**current_attachments, "files": files}
            report.touch()
            session.commit()
            session.refresh(report)

            pdf_buffer = BytesIO(pdf_bytes)
        except Exception as exc:
            LOG.warning("PDF upload to storage failed (report still returned): {}", exc)
            pdf_buffer.seek(0)

        pdf_buffer.seek(0)
        return pdf_buffer, filename

    # ─────────────────────────────────────── RBAC helper ──────────────────────

    def _assert_can_read(
        self,
        report: IncidentReport,
        current_user: TokenData,
        session: Session,
    ) -> None:
        if current_user.role == UserRole.TECHNICIAN:
            tech = self._get_technician_by_user(current_user.user_id, session)
            if report.technician_id != tech.id:
                raise ForbiddenException("Technicians can only view their own incident reports")


def get_incident_report_service() -> _IncidentReportService:
    return _IncidentReportService()


IncidentReportService = Annotated[_IncidentReportService, Depends(get_incident_report_service)]
