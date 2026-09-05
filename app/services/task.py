from uuid import UUID
from fastapi import Depends
from typing import List, Annotated
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_
from sqlalchemy.orm import selectinload
from loguru import logger as LOG

from app.utils.enums import TaskType, TaskStatus, ReportType, ReportStatus, UserRole
from app.models import (
    Task,
    TaskCreate,
    TaskUpdate,
    TaskResponse,
    Site,
    Technician,
    User,
    Report,
    Notification,
)
from app.models.auth import TokenData
from app.exceptions.http import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from app.services.authorization import get_technician_id_for_user, is_management
from app.services.notification import NotificationTemplates


class _TaskService:
    _REPORT_TYPE_ALIASES: dict[str, str] = {
        "rhs": ReportType.DIESEL,
        "corrective": ReportType.DIESEL,
        "routine-maintenance": ReportType.ROUTINE_DRIVE,
        "datacenter-inspection": ReportType.DATACENTER,
        "pop-inspection": ReportType.POP,
    }

    def task_to_response(self, task: Task, session: Session) -> TaskResponse:
        user = task.technician.user
        additional_names: list[str] = []
        if task.additional_technician_ids:
            ids: list[UUID] = []
            for id_str in task.additional_technician_ids:
                try:
                    ids.append(UUID(id_str))
                except ValueError:
                    pass
            if ids:
                extra_techs = session.exec(
                    select(Technician)
                    .options(selectinload(Technician.user))
                    .where(Technician.id.in_(ids), Technician.deleted_at.is_(None))  # type: ignore
                ).all()
                for tech in extra_techs:
                    if tech.user:
                        additional_names.append(f"{tech.user.name} {tech.user.surname}")
        task_data = task.model_dump()
        # Coerce None → "" for str fields that are computed or may be NULL for older rows
        task_data["assigned_by_name"] = task_data.get("assigned_by_name") or ""
        # Strip extra fields not present in TaskResponse (e.g. client_id from old records)
        task_data.pop("client_id", None)
        # Extract site GPS coordinates for map links (may be None if site has no location)
        coords = task.site.get_coordinates() if task.site else None
        # Count attachments correctly: attachments is {"files": [...]} not a flat list
        attachments_dict: dict = task.attachments or {}
        num_attachments = (
            len(attachments_dict.get("files", []))
            if isinstance(attachments_dict, dict)
            else 0
        )
        return TaskResponse(
            **task_data,
            site_name=task.site.name,
            technician_fullname=f"{user.name} {user.surname}",
            num_attachments=num_attachments,
            site_region=task.site.region,
            site_type=task.site.site_type if task.site else None,
            site_geofence_radius=task.site.geofence_radius if task.site else None,
            additional_technician_names=additional_names,
            site_latitude=coords[0] if coords else None,
            site_longitude=coords[1] if coords else None,
        )

    def _resolve_report_type(self, task: Task) -> ReportType:
        """Resolve report type from task.report_type with legacy alias support."""
        raw_report_type = (task.report_type or "").strip().lower()
        valid_values = {item.value for item in ReportType}

        if raw_report_type:
            if raw_report_type in valid_values:
                return ReportType(raw_report_type)

            alias = self._REPORT_TYPE_ALIASES.get(raw_report_type)
            if alias in valid_values:
                return ReportType(alias)

        if task.task_type == TaskType.ROUTINE_MAINTENANCE:
            return ReportType.ROUTINE_DRIVE
        return ReportType.DIESEL

    def _get_active_report_for_task(
        self, task_id: UUID, session: Session
    ) -> Report | None:
        statement = (
            select(Report)
            .where(
                Report.task_id == task_id,
                Report.deleted_at.is_(None),
            )
            .order_by(
                Report.updated_at.desc().nullslast(),
                Report.created_at.desc().nullslast(),
                Report.id.desc(),
            )
        )
        return session.exec(statement).first()

    def _assert_task_owner_or_management(
        self,
        task: Task,
        session: Session,
        current_user: TokenData,
        action: str,
    ) -> None:
        if is_management(current_user):
            return

        technician_id = get_technician_id_for_user(current_user.user_id, session)
        if task.technician_id != technician_id:
            raise ForbiddenException(
                f"You do not have permission to {action} this task."
            )

    def _ensure_auto_report_for_task(
        self,
        task: Task,
        session: Session,
        source: str,
    ) -> tuple[Report, bool]:
        existing = self._get_active_report_for_task(task.id, session)
        if existing:
            return existing, False

        resolved_report_type = self._resolve_report_type(task)
        site_name = task.site.name if task.site else "Unknown Site"

        report = Report(
            task_id=task.id,
            technician_id=task.technician_id,
            report_type=resolved_report_type,
            status=ReportStatus.PENDING,
            service_provider="SEACOM",
            seacom_ref=task.seacom_ref,
            data={
                "auto_generated": True,
                "auto_generated_source": source,
                "task_description": task.description,
                "site_name": site_name,
                "task_type": str(task.task_type),
                "report_type_resolved": str(resolved_report_type),
                "task_status_at_generation": str(task.status),
                "task_completed_at": task.completed_at.isoformat()
                if task.completed_at
                else None,
            },
        )

        try:
            session.add(report)
            session.commit()
            session.refresh(report)
            return report, True
        except IntegrityError:
            # Handles uniqueness races if another process created the report first.
            session.rollback()
            existing_after_race = self._get_active_report_for_task(task.id, session)
            if existing_after_race:
                return existing_after_race, False
            raise

    def create_task(
        self, data: TaskCreate, session: Session, current_user: TokenData
    ) -> TaskResponse:
        if not is_management(current_user):
            raise ForbiddenException("Only NOC, managers, or admins can create tasks.")

        # Handle site
        statement = select(Site).where(
            Site.id == data.site_id, Site.deleted_at.is_(None)
        )  # type: ignore
        site: Site | None = session.exec(statement).first()
        if not site:
            raise NotFoundException("site not found")

        # Handle technician
        statement = select(Technician).where(
            Technician.id == data.technician_id, Technician.deleted_at.is_(None)
        )  # type: ignore
        technician: Technician | None = session.exec(statement).first()
        if not technician:
            raise NotFoundException("technician not found")

        # Look up assigning user name
        assigned_by_user_id = None
        assigned_by_name = None
        if current_user:
            assigner = session.get(User, current_user.user_id)
            if assigner:
                assigned_by_user_id = assigner.id
                assigned_by_name = f"{assigner.name} {assigner.surname}"

        task: Task = Task(
            **data.model_dump(),
            site=site,
            technician=technician,
            assigned_by_user_id=assigned_by_user_id,
            assigned_by_name=assigned_by_name,
        )
        try:
            session.add(task)
            session.commit()
            session.refresh(task)

            # Notify the technician only when someone else assigned the task.
            # Skip when the technician created the task themselves (e.g. via an access request).
            is_self_assigned = (
                current_user and current_user.user_id == technician.user_id
            )
            if not is_self_assigned:
                template = NotificationTemplates.task_assigned(
                    site.name, data.description
                )
                session.add(
                    Notification(
                        user_id=technician.user_id,
                        title=template.title,
                        message=template.message,
                        priority=template.priority,
                    )
                )
                session.commit()

            return self.task_to_response(task, session)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating task: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating task: {e}")

    def read_task(
        self, task_id: UUID, session: Session, current_user: TokenData
    ) -> TaskResponse:
        task = self._get_task(task_id, session)
        self._assert_task_owner_or_management(task, session, current_user, "view")
        return self.task_to_response(task, session)

    def read_tasks(
        self,
        session: Session,
        current_user: TokenData,
        technician_id: UUID | None = None,
        task_type: TaskType | None = None,
        status: TaskStatus | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[TaskResponse]:
        statement = (
            select(Task)
            .options(
                selectinload(Task.site),
                selectinload(Task.technician).selectinload(Technician.user),
            )
            .where(Task.deleted_at.is_(None))
        )  # type: ignore

        if is_management(current_user):
            if technician_id is not None:
                statement = statement.where(Task.technician_id == technician_id)
        else:
            technician_id = get_technician_id_for_user(current_user.user_id, session)
            statement = statement.where(Task.technician_id == technician_id)
        if task_type is not None:
            statement = statement.where(Task.task_type == task_type)
        if status is not None:
            statement = statement.where(Task.status == status)

        statement = statement.offset(offset).limit(limit)
        tasks = session.exec(statement).all()
        return [self.task_to_response(task, session) for task in tasks]

    def update_task(
        self, task_id: UUID, data: TaskUpdate, session: Session, current_user: TokenData
    ) -> TaskResponse:
        if not is_management(current_user):
            raise ForbiddenException("Only NOC, managers, or admins can update tasks.")

        task = self._get_task(task_id, session)
        update_data = data.model_dump(
            exclude_none=True, exclude_defaults=True, exclude_unset=True
        )

        if not update_data:
            return self.task_to_response(task, session)

        for k, v in update_data.items():
            setattr(task, k, v)

        task.touch()

        try:
            session.commit()
            session.refresh(task)
            return self.task_to_response(task, session)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating task: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating task: {e}")

    def delete_task(
        self, task_id: UUID, session: Session, current_user: TokenData
    ) -> None:
        if not is_management(current_user):
            raise ForbiddenException("Only NOC, managers, or admins can delete tasks.")

        task = self._get_task(task_id, session)

        # Notify the assigned technician before deleting so they don't travel to the site.
        try:
            site_name = task.site.name if task.site else "Unknown Site"
            template = NotificationTemplates.task_cancelled(site_name, task.seacom_ref)
            session.add(
                Notification(
                    user_id=task.technician.user_id,
                    title=template.title,
                    message=template.message,
                    priority=template.priority,
                )
            )
        except Exception as exc:
            LOG.warning(
                "Could not send cancellation notification for task {}: {}", task_id, exc
            )

        task.soft_delete()
        session.commit()

    def start_task(
        self, task_id: UUID, session: Session, current_user: TokenData
    ) -> TaskResponse:
        """Start a task, ensure auto-report exists (skipped for RHS), and notify NOC operators."""
        task = self._get_task(task_id, session)
        self._assert_task_owner_or_management(task, session, current_user, "start")
        task.start()
        try:
            session.commit()
            session.refresh(task)

            # RHS tasks do not require a formal report — feedback is captured at completion
            report_created = False
            if task.task_type != TaskType.RHS:
                _, report_created = self._ensure_auto_report_for_task(
                    task=task,
                    session=session,
                    source="task_start",
                )
            report = self._get_active_report_for_task(task.id, session)

            # Notify NOC operators that task has started
            noc_users = session.exec(
                select(User).where(
                    and_(User.role == UserRole.NOC, User.deleted_at.is_(None))
                )
            ).all()

            site_name = task.site.name if task.site else "Unknown Site"
            tech_name = (
                f"{task.technician.user.name} {task.technician.user.surname}"
                if task.technician
                else "Unknown"
            )

            started_template = NotificationTemplates.task_started(
                tech_name, site_name
            )
            for noc_user in noc_users:
                session.add(
                    Notification(
                        user_id=noc_user.id,
                        title=started_template.title,
                        message=started_template.message,
                        priority=started_template.priority,
                    )
                )

            if report_created and task.technician and task.technician.user_id:
                report_template = NotificationTemplates.report_auto_created(
                    report.report_type, site_name
                )
                session.add(
                    Notification(
                        user_id=task.technician.user_id,
                        title=report_template.title,
                        message=report_template.message,
                        priority=report_template.priority,
                    )
                )

            session.commit()

            return self.task_to_response(task, session)
        except IntegrityError as e:
            session.rollback()
            LOG.error("Task start failed. task_id={} reason={}", task_id, e.orig)
            raise ConflictException(f"Error starting task: {e.orig}")
        except Exception as e:
            session.rollback()
            LOG.exception(
                "Unexpected error starting task. task_id={} error={}", task_id, e
            )
            raise InternalServerErrorException(f"Unexpected error starting task: {e}")

    def submit_feedback(
        self,
        task_id: UUID,
        feedback: str,
        session: Session,
        current_user: TokenData,
    ) -> TaskResponse:
        """Submit RHS feedback and complete the task. No report is created."""
        task = self._get_task(task_id, session)
        self._assert_task_owner_or_management(task, session, current_user, "complete")
        task.feedback = feedback
        task.complete()
        try:
            session.commit()
            session.refresh(task)

            noc_users = session.exec(
                select(User).where(
                    and_(User.role == UserRole.NOC, User.deleted_at.is_(None))
                )
            ).all()
            site_name = task.site.name if task.site else "Unknown Site"
            tech_name = (
                f"{task.technician.user.name} {task.technician.user.surname}"
                if task.technician
                else "Unknown"
            )
            ref_no = task.seacom_ref or None
            template = NotificationTemplates.task_completed(
                tech_name, site_name, ref_no=ref_no
            )
            for noc_user in noc_users:
                session.add(
                    Notification(
                        user_id=noc_user.id,
                        title=template.title,
                        message=template.message,
                        priority=template.priority,
                    )
                )
            session.commit()
            from app.services.email import EmailService
            from app.utils.funcs import utcnow

            EmailService.send_task_completed(
                ref_no=ref_no or "N/A",
                site_name=site_name,
                technician_name=tech_name,
                task_type="RHS — "
                + (feedback[:80] + "…" if len(feedback) > 80 else feedback),
                completed_at=utcnow().strftime("%d %b %Y %H:%M UTC"),
            )
            return self.task_to_response(task, session)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error submitting feedback: {e}"
            )

    def complete_task(
        self, task_id: UUID, session: Session, current_user: TokenData
    ) -> TaskResponse:
        """Complete a task, self-heal missing report if needed, and notify NOC operators."""
        task = self._get_task(task_id, session)
        self._assert_task_owner_or_management(task, session, current_user, "complete")
        task.complete()
        try:
            session.commit()
            session.refresh(task)

            # RHS tasks use submit_feedback instead — skip auto-report
            report_created = False
            report = None
            if task.task_type != TaskType.RHS:
                report, report_created = self._ensure_auto_report_for_task(
                    task=task,
                    session=session,
                    source="task_complete_self_heal",
                )

            # Notify NOC operators that task is completed
            noc_users = session.exec(
                select(User).where(
                    and_(User.role == UserRole.NOC, User.deleted_at.is_(None))
                )
            ).all()

            site_name = task.site.name if task.site else "Unknown Site"
            tech_name = (
                f"{task.technician.user.name} {task.technician.user.surname}"
                if task.technician
                else "Unknown"
            )

            ref_no = task.seacom_ref or None
            completed_template = NotificationTemplates.task_completed(
                tech_name, site_name, ref_no=ref_no
            )
            for noc_user in noc_users:
                session.add(
                    Notification(
                        user_id=noc_user.id,
                        title=completed_template.title,
                        message=completed_template.message,
                        priority=completed_template.priority,
                    )
                )
            session.commit()

            # Email NOC distribution list
            from app.services.email import EmailService
            from app.utils.funcs import utcnow

            EmailService.send_task_completed(
                ref_no=ref_no or "N/A",
                site_name=site_name,
                technician_name=tech_name,
                task_type=str(task.task_type),
                completed_at=utcnow().strftime("%d %b %Y %H:%M UTC"),
            )

            # Self-heal path notification: report was missing and auto-created at completion.
            if (
                report_created
                and report
                and task.technician
                and task.technician.user_id
            ):
                report_template = NotificationTemplates.report_auto_created(
                    report.report_type, site_name
                )
                session.add(
                    Notification(
                        user_id=task.technician.user_id,
                        title=report_template.title,
                        message=report_template.message,
                        priority=report_template.priority,
                    )
                )
                session.commit()

            return self.task_to_response(task, session)
        except IntegrityError as e:
            session.rollback()
            LOG.error("Task completion failed. task_id={} reason={}", task_id, e.orig)
            raise ConflictException(f"Error completing task: {e.orig}")
        except Exception as e:
            session.rollback()
            LOG.exception(
                "Unexpected error completing task. task_id={} error={}", task_id, e
            )
            raise InternalServerErrorException(f"Unexpected error completing task: {e}")

    def fail_task(
        self, task_id: UUID, session: Session, current_user: TokenData
    ) -> TaskResponse:
        """Mark a task as failed and notify NOC operators."""
        task = self._get_task(task_id, session)
        self._assert_task_owner_or_management(task, session, current_user, "fail")
        task.fail()
        try:
            session.commit()
            session.refresh(task)

            # Notify NOC operators that task has failed
            noc_users = session.exec(
                select(User).where(
                    and_(User.role == UserRole.NOC, User.deleted_at.is_(None))
                )
            ).all()

            site_name = task.site.name if task.site else "Unknown Site"
            tech_name = (
                f"{task.technician.user.name} {task.technician.user.surname}"
                if task.technician
                else "Unknown"
            )

            template = NotificationTemplates.task_failed(tech_name, site_name)
            for noc_user in noc_users:
                session.add(
                    Notification(
                        user_id=noc_user.id,
                        title=template.title,
                        message=template.message,
                        priority=template.priority,
                    )
                )
            session.commit()

            return self.task_to_response(task, session)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error failing task: {e}")

    def hold_task(
        self,
        task_id: UUID,
        reason: str | None,
        session: Session,
        current_user: TokenData,
    ) -> TaskResponse:
        """Put a started task on hold so the technician can continue the next day."""
        task = self._get_task(task_id, session)
        self._assert_task_owner_or_management(task, session, current_user, "hold")
        if task.status != TaskStatus.STARTED:
            raise BadRequestException(
                f"Cannot hold a task that is not started (current status: {task.status})"
            )
        task.hold(reason)
        try:
            session.commit()
            session.refresh(task)
            return self.task_to_response(task, session)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error holding task: {e}")

    def resume_task(
        self, task_id: UUID, session: Session, current_user: TokenData
    ) -> TaskResponse:
        """Resume an on-hold task, restoring it to started status."""
        task = self._get_task(task_id, session)
        self._assert_task_owner_or_management(task, session, current_user, "resume")
        if task.status != TaskStatus.ON_HOLD:
            raise BadRequestException(
                f"Cannot resume a task that is not on hold (current status: {task.status})"
            )
        task.resume()
        try:
            session.commit()
            session.refresh(task)
            return self.task_to_response(task, session)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error resuming task: {e}")

    def _get_task(self, task_id: UUID, session: Session) -> Task:
        statement = (
            select(Task)
            .options(
                selectinload(Task.site),
                selectinload(Task.technician).selectinload(Technician.user),
            )
            .where(Task.id == task_id, Task.deleted_at.is_(None))
        )  # type: ignore
        task: Task | None = session.exec(statement).first()
        if not task:
            raise NotFoundException("task not found")
        return task


def get_task_service() -> _TaskService:
    return _TaskService()


TaskService = Annotated[_TaskService, Depends(get_task_service)]
