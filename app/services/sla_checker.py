"""
SLA Breach Checker Service — Phase 1 rewrite.

Checks three independent SLA milestones per fault (Annexure H):
  1. Respond       — technician acknowledges the fault
  2. On-site       — technician arrives at the fault location
  3. Temp restore  — service is temporarily restored

Notifications are fired per-milestone, not per-incident, so each breach
is flagged independently even if prior milestones were met.
"""

from datetime import timedelta
from typing import List, Tuple

from sqlalchemy import and_
from sqlmodel import Session, select

from app.models import Incident, User
from app.utils.enums import IncidentStatus
from app.utils.funcs import utcnow
from app.utils.sla_utils import calculate_sla_deadlines, get_milestone_status


def get_sla_status_for_incident(incident: Incident) -> dict:
    """
    Return the full three-milestone SLA status for a single incident.

    The returned dict has the shape:
        {
            "severity": "critical" | "major" | "minor" | "query",
            "overall":  "ok" | "at_risk" | "breached" | "resolved",
            "respond":       {status, deadline, actual, ...},
            "onsite":        {status, deadline, actual, ...},
            "temp_restore":  {status, deadline, actual, ...},
        }
    """
    now = utcnow()
    severity = str(incident.severity) if incident.severity else "minor"
    start    = incident.start_time or incident.created_at
    deadlines = calculate_sla_deadlines(severity, start)

    respond_status      = get_milestone_status(deadlines["respond_deadline"],      getattr(incident, "responded_at", None),            now)
    onsite_status       = get_milestone_status(deadlines["onsite_deadline"],        getattr(incident, "arrived_on_site_at", None),      now)
    temp_restore_status = get_milestone_status(deadlines["temp_restore_deadline"],  getattr(incident, "temporarily_restored_at", None), now)

    statuses = [
        respond_status["status"],
        onsite_status["status"],
        temp_restore_status["status"],
    ]

    if incident.status == IncidentStatus.RESOLVED:
        overall = "resolved"
    elif "breached" in statuses:
        overall = "breached"
    elif "at_risk" in statuses:
        overall = "at_risk"
    else:
        overall = "ok"

    return {
        "severity":    severity,
        "overall":     overall,
        "respond":     respond_status,
        "onsite":      onsite_status,
        "temp_restore": temp_restore_status,
    }


def check_sla_breaches(session: Session) -> Tuple[List[dict], List[dict]]:
    """
    Scan all open/in-progress incidents and check each of the three SLA
    milestones independently.

    Returns:
        (warnings, breaches) — lists of dicts describing each triggered event.
    """
    from app.services.notification import _NotificationService, NotificationTemplates
    from app.services.email import EmailService
    from app.services.webhook import WebhookService
    import threading

    notification_service = _NotificationService()
    now      = utcnow()
    warnings: list[dict] = []
    breaches: list[dict] = []

    # Pre-fetch NOC and manager user IDs to notify on breaches
    noc_user_ids = [
        u.id for u in session.exec(
            select(User).where(
                User.role.in_(["noc", "manager"]),  # type: ignore
                User.deleted_at.is_(None),
            )
        ).all()
    ]

    statement = select(Incident).where(
        and_(
            Incident.status != IncidentStatus.RESOLVED,
            Incident.deleted_at.is_(None),
        )
    )
    active_incidents = session.exec(statement).all()

    for incident in active_incidents:
        severity = str(incident.severity) if incident.severity else "minor"
        start    = incident.start_time or incident.created_at
        deadlines = calculate_sla_deadlines(severity, start)

        site_name = incident.site.name if incident.site else "Unknown Site"
        ref_no    = incident.ref_no or incident.seacom_ref or None
        tech_user_id = (
            incident.technician.user_id if incident.technician else None
        )

        milestone_checks = [
            ("respond",      deadlines["respond_deadline"],      getattr(incident, "responded_at", None)),
            ("onsite",       deadlines["onsite_deadline"],        getattr(incident, "arrived_on_site_at", None)),
            ("temp_restore", deadlines["temp_restore_deadline"],  getattr(incident, "temporarily_restored_at", None)),
        ]

        for milestone_name, deadline, actual_time in milestone_checks:
            if deadline is None:
                continue
            # Skip milestones already met
            if actual_time is not None and actual_time <= deadline:
                continue

            if actual_time is None and now < deadline:
                # Not yet breached — check if at-risk (≤30 min remaining)
                time_remaining = (deadline - now).total_seconds()
                if time_remaining <= 1800:
                    minutes_remaining = int(time_remaining / 60)
                    time_str = (
                        f"{minutes_remaining} minutes"
                        if minutes_remaining < 60
                        else f"{minutes_remaining // 60}h {minutes_remaining % 60}m"
                    )
                    event = {
                        "incident_id":            str(incident.id),
                        "ref_no":                 ref_no,
                        "site_name":              site_name,
                        "severity":               severity,
                        "milestone":              milestone_name,
                        "deadline":               deadline.isoformat(),
                        "time_remaining_minutes": minutes_remaining,
                        "event_type":             "sla_warning",
                        "timestamp":              now.isoformat(),
                    }
                    warnings.append(event)

                    # Warn the assigned technician
                    if tech_user_id:
                        notification_service.create_notification_from_template(
                            user_id=tech_user_id,
                            template=NotificationTemplates.sla_warning(
                                site_name, severity, time_str,
                                milestone=milestone_name, ref_no=ref_no,
                            ),
                            session=session,
                        )
                    # Also warn NOC so they can follow up
                    for noc_id in noc_user_ids:
                        if noc_id != tech_user_id:
                            notification_service.create_notification_from_template(
                                user_id=noc_id,
                                template=NotificationTemplates.sla_warning(
                                    site_name, severity, time_str,
                                    milestone=milestone_name, ref_no=ref_no,
                                ),
                                session=session,
                            )

                    # Email NOC distribution list
                    EmailService.send_sla_warning(
                        ref_no=ref_no,
                        site_name=site_name,
                        severity=severity,
                        milestone=milestone_name,
                        time_remaining=time_str,
                    )

                    def _send_warning(data: dict = event) -> None:
                        import asyncio
                        asyncio.run(WebhookService.send_webhook("sla_warning", data))

                    t = threading.Thread(target=_send_warning)
                    t.daemon = True
                    t.start()

            elif now >= deadline and actual_time is None:
                # Milestone deadline passed with no actual time — breached
                time_overdue_delta = now - deadline
                total_minutes_overdue = int(time_overdue_delta.total_seconds() / 60)
                overdue_str = (
                    f"{total_minutes_overdue} min"
                    if total_minutes_overdue < 60
                    else f"{total_minutes_overdue // 60}h {total_minutes_overdue % 60}m"
                )
                event = {
                    "incident_id":  str(incident.id),
                    "ref_no":       ref_no,
                    "site_name":    site_name,
                    "severity":     severity,
                    "milestone":    milestone_name,
                    "deadline":     deadline.isoformat(),
                    "time_overdue": str(time_overdue_delta),
                    "event_type":   "sla_breach",
                    "timestamp":    now.isoformat(),
                }
                breaches.append(event)

                breach_template = NotificationTemplates.sla_breached(
                    site_name, severity,
                    milestone=milestone_name, ref_no=ref_no, time_overdue=overdue_str,
                )

                # Notify the technician
                if tech_user_id:
                    notification_service.create_notification_from_template(
                        user_id=tech_user_id,
                        template=breach_template,
                        session=session,
                    )
                # Notify NOC and managers
                for noc_id in noc_user_ids:
                    if noc_id != tech_user_id:
                        notification_service.create_notification_from_template(
                            user_id=noc_id,
                            template=breach_template,
                            session=session,
                        )

                # Email NOC distribution list immediately on breach
                EmailService.send_sla_breach(
                    ref_no=ref_no,
                    site_name=site_name,
                    severity=severity,
                    milestone=milestone_name,
                    time_overdue=overdue_str,
                )

                def _send_breach(data: dict = event) -> None:
                    import asyncio
                    asyncio.run(WebhookService.send_webhook("sla_breach", data))

                t = threading.Thread(target=_send_breach)
                t.daemon = True
                t.start()

    return warnings, breaches
