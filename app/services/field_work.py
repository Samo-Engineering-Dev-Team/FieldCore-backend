from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.exceptions.http import (
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import Notification, Report, ReportResponse, Site, Task, Technician, User
from app.models.auth import TokenData
from app.models.field_work import FieldWorkCreate, FieldWorkResponse
from app.services.maintenance_schedule import get_maintenance_schedule_service
from app.services.authorization import get_technician_id_for_user
from app.services.notification import NotificationTemplates
from app.services.report import _ReportService
from app.utils.enums import ReportStatus, ReportType, TaskType, UserRole

REPORT_TO_SCHEDULE_TYPE = {
    ReportType.ROUTINE_DRIVE: "routine_drive",
    ReportType.REPEATER: "repeater_site_visit",
    ReportType.DIESEL: "generator_diesel_refill",
    ReportType.DATACENTER: "datacenter_inspection",
    ReportType.POP: "pop_inspection",
}

REPORT_TO_DESCRIPTION = {
    ReportType.ROUTINE_DRIVE: "Routine Drive",
    ReportType.REPEATER: "Repeater Site Visit",
    ReportType.DIESEL: "Generator Diesel Refill",
    ReportType.DATACENTER: "Datacenter Inspection",
    ReportType.POP: "POP Inspection",
}


def _ensure_technician(
    session: Session, current_user: TokenData, technician_id: str | None = None
) -> Technician:
    if current_user.role != UserRole.TECHNICIAN:
        raise ForbiddenException("Only technicians can submit field work reports.")

    resolved_id = get_technician_id_for_user(current_user.user_id, session)
    if technician_id and str(resolved_id) != str(technician_id):
        raise ForbiddenException("Technicians can only submit their own reports.")

    technician = session.exec(
        select(Technician).where(
            Technician.id == resolved_id,
            Technician.deleted_at.is_(None),  # type: ignore
        )
    ).first()
    if not technician:
        raise NotFoundException("technician not found")
    return technician


def _ensure_site(session: Session, site_id) -> Site:
    site = session.exec(
        select(Site).where(
            Site.id == site_id,
            Site.deleted_at.is_(None),  # type: ignore
        )
    ).first()
    if not site:
        raise NotFoundException("site not found")
    return site


def _mark_schedule_done(
    *,
    session: Session,
    technician_id,
    site_id,
    report_type: ReportType,
    completed_at: datetime,
) -> None:
    schedule_type = REPORT_TO_SCHEDULE_TYPE[report_type]
    get_maintenance_schedule_service().mark_schedule_done_for_field_work(
        session=session,
        technician_id=technician_id,
        site_id=site_id,
        schedule_type=schedule_type,
        completed_at=completed_at,
    )


def create_completed_field_report(
    *,
    session: Session,
    technician: Technician,
    site: Site,
    report_type: ReportType,
    seacom_ref: str,
    performed_at: datetime,
    data: dict[str, Any],
    attachments: dict[str, Any] | None = None,
) -> tuple[Task, Report]:
    description = REPORT_TO_DESCRIPTION[report_type]
    task = Task(
        seacom_ref=seacom_ref.strip(),
        description=description,
        start_time=performed_at,
        end_time=performed_at,
        task_type=TaskType.ROUTINE_MAINTENANCE,
        report_type=report_type.value,
        site_id=site.id,
        technician_id=technician.id,
        technician=technician,
        site=site,
        assigned_by_user_id=technician.user_id,
        assigned_by_name="Technician self-submitted",
    )
    task.complete()

    session.add(task)
    session.flush()

    report = Report(
        task_id=task.id,
        technician_id=technician.id,
        technician=technician,
        task=task,
        report_type=report_type,
        status=ReportStatus.COMPLETED,
        service_provider="SEACOM",
        seacom_ref=seacom_ref.strip(),
        data=data,
        attachments=attachments,
    )
    report.complete()
    session.add(report)

    _mark_schedule_done(
        session=session,
        technician_id=technician.id,
        site_id=site.id,
        report_type=report_type,
        completed_at=performed_at,
    )
    return task, report


def create_started_field_report(
    *,
    session: Session,
    technician: Technician,
    site: Site,
    report_type: ReportType,
    seacom_ref: str,
    performed_at: datetime,
    data: dict[str, Any],
) -> tuple[Task, Report]:
    description = REPORT_TO_DESCRIPTION[report_type]
    task = Task(
        seacom_ref=seacom_ref.strip(),
        description=description,
        start_time=performed_at,
        end_time=performed_at,
        task_type=TaskType.ROUTINE_MAINTENANCE,
        report_type=report_type.value,
        site_id=site.id,
        technician_id=technician.id,
        technician=technician,
        site=site,
        assigned_by_user_id=technician.user_id,
        assigned_by_name="Technician self-started",
    )
    task.start()
    session.add(task)
    session.flush()

    report = Report(
        task_id=task.id,
        technician_id=technician.id,
        technician=technician,
        task=task,
        report_type=report_type,
        status=ReportStatus.STARTED,
        service_provider="SEACOM",
        seacom_ref=seacom_ref.strip(),
        data=data,
        attachments={"files": []},
    )
    report.start()
    session.add(report)
    return task, report


def _notify_noc_started(
    *,
    session: Session,
    technician: Technician,
    site: Site,
) -> None:
    noc_users = session.exec(
        select(User).where(
            User.role == UserRole.NOC,
            User.deleted_at.is_(None),  # type: ignore
        )
    ).all()
    technician_name = (
        f"{technician.user.name} {technician.user.surname}"
        if technician.user
        else "Technician"
    )
    template = NotificationTemplates.task_started(technician_name, site.name)
    for noc_user in noc_users:
        session.add(
            Notification(
                user_id=noc_user.id,
                title=template.title,
                message=template.message,
                priority=template.priority,
            )
        )


class _FieldWorkService:
    def start(
        self,
        payload: FieldWorkCreate,
        report_type: ReportType,
        session: Session,
        current_user: TokenData,
    ) -> ReportResponse:
        technician = _ensure_technician(session, current_user)
        site = _ensure_site(session, payload.site_id)

        try:
            _, report = create_started_field_report(
                session=session,
                technician=technician,
                site=site,
                report_type=report_type,
                seacom_ref=payload.seacom_ref,
                performed_at=payload.performed_at,
                data=payload.data,
            )
            _notify_noc_started(session=session, technician=technician, site=site)
            session.commit()
            session.refresh(report)
            return _ReportService().report_to_response(report)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error starting field work: {e.orig}")
        except (ForbiddenException, NotFoundException):
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error starting field work: {e}"
            )

    def submit(
        self,
        payload: FieldWorkCreate,
        report_type: ReportType,
        session: Session,
        current_user: TokenData,
    ) -> FieldWorkResponse:
        technician = _ensure_technician(session, current_user)
        site = _ensure_site(session, payload.site_id)

        try:
            _, report = create_completed_field_report(
                session=session,
                technician=technician,
                site=site,
                report_type=report_type,
                seacom_ref=payload.seacom_ref,
                performed_at=payload.performed_at,
                data=payload.data,
                attachments=payload.attachments,
            )
            session.commit()
            session.refresh(report)
            return FieldWorkResponse(
                task_id=report.task_id,
                report_id=report.id,
                report_type=report.report_type,
            )
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error submitting field work: {e.orig}")
        except (ForbiddenException, NotFoundException):
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error submitting field work: {e}"
            )


def get_field_work_service() -> "_FieldWorkService":
    return _FieldWorkService()


FieldWorkService = Annotated[_FieldWorkService, Depends(get_field_work_service)]
