from dataclasses import dataclass
from typing import Annotated, Iterable, List
from uuid import UUID

from fastapi import Depends
from loguru import logger as LOG
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlmodel import Session, select

from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import Notification, NotificationCreate, NotificationResponse, User
from app.utils.enums import NotificationPriority


@dataclass(frozen=True)
class NotificationTemplate:
    title: str
    message: str
    priority: NotificationPriority = NotificationPriority.NORMAL


class NotificationTemplates:
    @staticmethod
    def _clean(text: str | None) -> str:
        if not text:
            return ""
        return " ".join(text.split())

    @staticmethod
    def _preview(text: str | None, limit: int = 110) -> str:
        cleaned = NotificationTemplates._clean(text)
        if not cleaned:
            return "No details provided."
        if len(cleaned) <= limit:
            return cleaned
        return f"{cleaned[:limit - 3].rstrip()}..."

    @staticmethod
    def _label(value: str | object) -> str:
        text = str(value).replace("-", " ").replace("_", " ").strip()
        return text.title()

    @staticmethod
    def _priority_from_text(priority: str | None) -> NotificationPriority:
        normalized = (priority or "").strip().lower()
        if normalized == "critical":
            return NotificationPriority.CRITICAL
        if normalized == "high":
            return NotificationPriority.HIGH
        return NotificationPriority.NORMAL

    @staticmethod
    def task_assigned(site_name: str, description: str | None, ref_no: str | None = None) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        return NotificationTemplate(
            title=f"Task assigned{ref}",
            message=(
                f"You have been assigned task{ref} at {site_name}. "
                f"{NotificationTemplates._preview(description)}"
            ),
            priority=NotificationPriority.HIGH,
        )

    @staticmethod
    def task_started(technician_name: str, site_name: str, ref_no: str | None = None) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        return NotificationTemplate(
            title=f"Task in progress{ref}",
            message=f"{technician_name} has started work on task{ref} at {site_name}.",
            priority=NotificationPriority.NORMAL,
        )

    @staticmethod
    def task_completed(technician_name: str, site_name: str, ref_no: str | None = None) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        return NotificationTemplate(
            title=f"Task completed{ref}",
            message=(
                f"{technician_name} completed task{ref} at {site_name}. "
                "The report is ready for review."
            ),
            priority=NotificationPriority.HIGH,
        )

    @staticmethod
    def task_failed(technician_name: str, site_name: str, ref_no: str | None = None) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        return NotificationTemplate(
            title=f"Task failed{ref}",
            message=(
                f"{technician_name} could not complete task{ref} at {site_name}. "
                "Immediate review and reassignment may be required."
            ),
            priority=NotificationPriority.CRITICAL,
        )

    @staticmethod
    def report_auto_created(report_type: str | object, site_name: str) -> NotificationTemplate:
        report_label = NotificationTemplates._label(report_type)
        return NotificationTemplate(
            title="Report ready for update",
            message=(
                f"A {report_label} report was created for {site_name}. "
                "Open it and add your findings."
            ),
            priority=NotificationPriority.NORMAL,
        )

    @staticmethod
    def report_submitted(
        technician_name: str,
        report_type: str | object,
        site_name: str,
    ) -> NotificationTemplate:
        report_label = NotificationTemplates._label(report_type)
        return NotificationTemplate(
            title="New report submitted",
            message=f"{technician_name} submitted a {report_label} report for {site_name}.",
            priority=NotificationPriority.NORMAL,
        )

    @staticmethod
    def incident_assigned_to_technician(
        site_name: str,
        description: str | None,
        ref_no: str | None = None,
        severity: str | None = None,
    ) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        sev = f" — Severity: {severity.upper()}" if severity else ""
        return NotificationTemplate(
            title=f"Incident assigned{ref}{sev}",
            message=(
                f"You have been assigned incident{ref} at {site_name}{sev}. "
                f"SLA clock is running — respond immediately. "
                f"{NotificationTemplates._preview(description)}"
            ),
            priority=NotificationPriority.CRITICAL,
        )

    @staticmethod
    def incident_created_for_noc(
        site_name: str,
        technician_name: str,
        description: str | None,
        ref_no: str | None = None,
        severity: str | None = None,
    ) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        sev = f" ({severity.upper()})" if severity else ""
        return NotificationTemplate(
            title=f"New incident{ref}{sev}",
            message=(
                f"Incident{ref}{sev} at {site_name} assigned to {technician_name}. "
                f"{NotificationTemplates._preview(description)}"
            ),
            priority=NotificationPriority.HIGH,
        )

    @staticmethod
    def incident_in_progress(technician_name: str, site_name: str, ref_no: str | None = None) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        return NotificationTemplate(
            title=f"Incident in progress{ref}",
            message=f"{technician_name} has started working on incident{ref} at {site_name}.",
            priority=NotificationPriority.NORMAL,
        )

    @staticmethod
    def incident_resolved(technician_name: str, site_name: str, ref_no: str | None = None) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        return NotificationTemplate(
            title=f"Incident resolved{ref}",
            message=(
                f"{technician_name} resolved incident{ref} at {site_name}. "
                "Review the incident report and confirm closure."
            ),
            priority=NotificationPriority.HIGH,
        )

    @staticmethod
    def access_request_created(
        site_name: str,
        technician_name: str,
        description: str | None,
    ) -> NotificationTemplate:
        return NotificationTemplate(
            title="Access request submitted",
            message=(
                f"{technician_name} requested site access for {site_name}. "
                f"Details: {NotificationTemplates._preview(description)}"
            ),
            priority=NotificationPriority.HIGH,
        )

    @staticmethod
    def access_request_approved(site_name: str, seacom_ref: str) -> NotificationTemplate:
        return NotificationTemplate(
            title="Access request approved",
            message=f"Access to {site_name} was approved. Reference: {seacom_ref}.",
            priority=NotificationPriority.HIGH,
        )

    @staticmethod
    def access_request_rejected(site_name: str) -> NotificationTemplate:
        return NotificationTemplate(
            title="Access request rejected",
            message=f"Access to {site_name} was rejected. Contact NOC for next steps.",
            priority=NotificationPriority.NORMAL,
        )

    @staticmethod
    def inspection_started(site_name: str, technician_name: str) -> NotificationTemplate:
        return NotificationTemplate(
            title="Routine inspection started",
            message=f"{technician_name} started a generator inspection at {site_name}.",
            priority=NotificationPriority.NORMAL,
        )

    @staticmethod
    def inspection_completed(site_name: str) -> NotificationTemplate:
        return NotificationTemplate(
            title="Routine inspection completed",
            message=f"The generator inspection for {site_name} has been submitted.",
            priority=NotificationPriority.NORMAL,
        )

    @staticmethod
    def incident_report_submitted(technician_name: str, site_name: str) -> NotificationTemplate:
        return NotificationTemplate(
            title="Incident report submitted",
            message=(
                f"{technician_name} submitted an incident report for {site_name}. "
                "Review and export the report from the Incident Reports tab."
            ),
            priority=NotificationPriority.HIGH,
        )

    _MILESTONE_LABELS: dict[str, str] = {
        "respond":      "Response time",
        "onsite":       "On-site arrival",
        "temp_restore": "Temporary restoration",
    }

    @staticmethod
    def sla_warning(
        site_name: str,
        severity: str,
        time_remaining: str,
        milestone: str | None = None,
        ref_no: str | None = None,
    ) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        milestone_label = NotificationTemplates._MILESTONE_LABELS.get(milestone or "", milestone or "SLA milestone")
        return NotificationTemplate(
            title=f"SLA at risk{ref} — {milestone_label}",
            message=(
                f"Incident{ref} at {site_name} ({severity.upper()}) — "
                f"{milestone_label} deadline in {time_remaining}. "
                "Act now to avoid an SLA breach."
            ),
            priority=NotificationPriority.HIGH,
        )

    @staticmethod
    def sla_breached(
        site_name: str,
        severity: str,
        milestone: str | None = None,
        ref_no: str | None = None,
        time_overdue: str | None = None,
    ) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        milestone_label = NotificationTemplates._MILESTONE_LABELS.get(milestone or "", milestone or "SLA milestone")
        overdue_str = f" ({time_overdue} overdue)" if time_overdue else ""
        return NotificationTemplate(
            title=f"SLA BREACHED{ref} — {milestone_label}",
            message=(
                f"Incident{ref} at {site_name} ({severity.upper()}) has breached the "
                f"{milestone_label} SLA{overdue_str}. "
                "Immediate escalation and penalty exposure applies."
            ),
            priority=NotificationPriority.CRITICAL,
        )

    @staticmethod
    def maintenance_overdue(site_name: str, schedule_type: str, days_overdue: int) -> NotificationTemplate:
        type_label = schedule_type.replace("_", " ").title()
        return NotificationTemplate(
            title=f"Maintenance overdue — {type_label} at {site_name}",
            message=(
                f"The {type_label} maintenance at {site_name} is {days_overdue} day(s) overdue. "
                "Assign a technician or reschedule to remain compliant."
            ),
            priority=NotificationPriority.HIGH if days_overdue >= 7 else NotificationPriority.NORMAL,
        )

    @staticmethod
    def task_cancelled(site_name: str, ref_no: str | None = None) -> NotificationTemplate:
        ref = f" [{ref_no}]" if ref_no else ""
        return NotificationTemplate(
            title=f"RHS task cancelled{ref}",
            message=(
                f"The RHS task{ref} at {site_name} has been cancelled by NOC. "
                "Do not travel to the site — contact NOC if you have any questions."
            ),
            priority=NotificationPriority.HIGH,
        )

    @staticmethod
    def technician_escalation(technician_name: str, priority: str, reason: str) -> NotificationTemplate:
        return NotificationTemplate(
            title="Technician escalation",
            message=(
                f"{technician_name} escalation ({priority.upper()}): "
                f"{NotificationTemplates._preview(reason, 180)}"
            ),
            priority=NotificationTemplates._priority_from_text(priority),
        )

    @staticmethod
    def technician_help_alert(technician_name: str, priority: str, reason: str) -> NotificationTemplate:
        return NotificationTemplate(
            title="Technician help alert",
            message=(
                f"{technician_name} needs help ({priority.upper()}): "
                f"{NotificationTemplates._preview(reason, 180)}"
            ),
            priority=NotificationTemplates._priority_from_text(priority),
        )


class _NotificationService:
    def notification_to_response(self, notification: Notification) -> NotificationResponse:
        return NotificationResponse(**notification.model_dump())

    def create_notification(self, data: NotificationCreate, session: Session) -> NotificationResponse:
        try:
            statement = select(User).where(User.id == data.user_id, User.deleted_at.is_(None))  # type: ignore
            user: User | None = session.exec(statement).first()

            if not user:
                raise NotFoundException("user not found")

            notification = Notification(**data.model_dump(), user=user)
            session.add(notification)
            session.commit()
            session.refresh(notification)
            return self.notification_to_response(notification)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating notification: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating notification: {e}")

    def create_notification_for_user(
        self,
        user_id: UUID,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        session: Session | None = None,
    ) -> NotificationResponse | None:
        if session is None:
            LOG.warning("Notification skipped for user {} because session was None.", user_id)
            return None

        try:
            notification_data = NotificationCreate(
                user_id=user_id,
                title=title.strip(),
                message=message.strip(),
                priority=priority,
            )
            return self.create_notification(notification_data, session)
        except Exception as exc:
            LOG.warning("Notification creation failed for user {}: {}", user_id, exc)
            return None

    def create_notification_from_template(
        self,
        user_id: UUID,
        template: NotificationTemplate,
        session: Session,
    ) -> NotificationResponse | None:
        return self.create_notification_for_user(
            user_id=user_id,
            title=template.title,
            message=template.message,
            priority=template.priority,
            session=session,
        )

    def create_notifications_from_template(
        self,
        user_ids: Iterable[UUID],
        template: NotificationTemplate,
        session: Session,
    ) -> int:
        sent = 0
        for user_id in user_ids:
            if self.create_notification_from_template(user_id, template, session):
                sent += 1
        return sent

    def read_notification(self, notification_id: UUID, session: Session) -> NotificationResponse:
        notification = self._get_notification(notification_id, session)
        return self.notification_to_response(notification)

    def read_notifications(
        self,
        session: Session,
        priority: NotificationPriority | None = None,
        user_id: UUID | None = None,
        read: bool | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[NotificationResponse]:
        statement = select(Notification).where(Notification.deleted_at.is_(None))  # type: ignore

        if priority is not None:
            statement = statement.where(Notification.priority == priority)
        if user_id is not None:
            statement = statement.where(Notification.user_id == user_id)
        if read is not None:
            statement = statement.where(Notification.read == read)

        statement = statement.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
        notifications = session.exec(statement).all()
        return [self.notification_to_response(notification) for notification in notifications]

    def get_unread_count(self, user_id: UUID, session: Session) -> int:
        statement = select(func.count(Notification.id)).where(
            Notification.deleted_at.is_(None),  # type: ignore
            Notification.user_id == user_id,
            Notification.read.is_(False),
        )
        return int(session.exec(statement).one())

    def delete_notification(self, notification_id: UUID, session: Session) -> None:
        notification = self._get_notification(notification_id, session)
        notification.soft_delete()
        session.commit()

    def read(self, notification_id: UUID, session: Session) -> NotificationResponse:
        notification = self._get_notification(notification_id, session)
        notification.read = True
        notification.touch()
        try:
            session.commit()
            session.refresh(notification)
            return self.notification_to_response(notification)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error marking notification as read: {e}")

    def mark_all_as_read(self, user_id: UUID, session: Session) -> int:
        statement = select(Notification).where(
            Notification.deleted_at.is_(None),  # type: ignore
            Notification.user_id == user_id,
            Notification.read.is_(False),
        )
        unread_items = session.exec(statement).all()
        if not unread_items:
            return 0

        for notification in unread_items:
            notification.read = True
            notification.touch()

        try:
            session.commit()
            return len(unread_items)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error marking all notifications as read: {e}")

    def _get_notification(self, notification_id: UUID, session: Session) -> Notification:
        statement = select(Notification).where(
            Notification.id == notification_id,
            Notification.deleted_at.is_(None),  # type: ignore
        )
        notification: Notification | None = session.exec(statement).first()
        if not notification:
            raise NotFoundException("notification not found")
        return notification


def get_notification_service() -> _NotificationService:
    return _NotificationService()


NotificationService = Annotated[_NotificationService, Depends(get_notification_service)]
