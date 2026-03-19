"""
Email Service — MS Exchange / SMTP integration.

Sends automated HTML emails for key operational events:
  • Task completed (report ready)
  • Incident resolved
  • SLA breach / SLA at-risk warning
  • Technician escalation
  • Incident report submitted

Configuration (environment variables):
  SMTP_HOST        — Exchange server hostname (e.g. smtp.office365.com)
  SMTP_PORT        — 587 for STARTTLS (recommended), 465 for SSL
  SMTP_USER        — Sender mailbox address (e.g. noc-system@company.com)
  SMTP_PASSWORD    — Mailbox password or Exchange app password
  SMTP_FROM_NAME   — Display name (default: "SAMO NOC")
  SMTP_USE_TLS     — true for STARTTLS/port-587 (default: true)
  NOC_EMAIL_ADDRESSES — Comma-separated recipient list for NOC distribution
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Sequence

import aiosmtplib
from loguru import logger as LOG

from app.core.settings import app_settings

# ---------------------------------------------------------------------------
# HTML email template helpers
# ---------------------------------------------------------------------------

_HEADER_COLOUR = "#008181"  # SAMO brand teal


def _html_wrap(title: str, body_html: str) -> str:
    """Wrap body content in a minimal branded HTML shell."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;font-size:14px;color:#1f2937;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">
        <!-- Header -->
        <tr>
          <td style="background:{_HEADER_COLOUR};padding:24px 32px;">
            <h1 style="margin:0;color:#fff;font-size:20px;font-weight:700;">SAMO Field Operations</h1>
            <p style="margin:4px 0 0;color:#bfdbfe;font-size:13px;">Automated notification from the SEACOM management system</p>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:28px 32px;">
            {body_html}
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;">
            <p style="margin:0;font-size:11px;color:#9ca3af;">
              Sent by SAMO Field Operations System · {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')} ·
              Do not reply to this email.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _kv_row(label: str, value: str) -> str:
    return (
        f'<tr>'
        f'<td style="padding:6px 12px 6px 0;color:#6b7280;font-size:13px;white-space:nowrap;">{label}</td>'
        f'<td style="padding:6px 0;font-weight:600;">{value}</td>'
        f'</tr>'
    )


def _detail_table(*rows: str) -> str:
    inner = "".join(rows)
    return (
        f'<table cellpadding="0" cellspacing="0" style="margin-top:16px;width:100%;">'
        f'{inner}'
        f'</table>'
    )


def _badge(text: str, colour: str = "#008181") -> str:
    return (
        f'<span style="display:inline-block;background:{colour};color:#fff;'
        f'border-radius:4px;padding:2px 10px;font-size:12px;font-weight:700;">{text}</span>'
    )


def _severity_colour(severity: str) -> str:
    return {"critical": "#dc2626", "major": "#d97706", "minor": "#008181", "query": "#6b7280"}.get(
        severity.lower(), "#6b7280"
    )


# ---------------------------------------------------------------------------
# Individual email builders
# ---------------------------------------------------------------------------

def _build_task_completed(
    *,
    ref_no: str,
    site_name: str,
    technician_name: str,
    task_type: str,
    completed_at: str,
) -> tuple[str, str]:
    """Returns (subject, html_body)."""
    subject = f"Task Completed — {ref_no} at {site_name}"
    body = f"""
    <h2 style="margin:0 0 8px;font-size:18px;">Task Completed</h2>
    <p style="margin:0 0 16px;color:#6b7280;">The following task has been completed and the report is ready for review.</p>
    {_detail_table(
        _kv_row("Reference", ref_no),
        _kv_row("Site", site_name),
        _kv_row("Technician", technician_name),
        _kv_row("Task Type", task_type.replace("_", " ").title()),
        _kv_row("Completed At", completed_at),
    )}
    <p style="margin:24px 0 0;color:#6b7280;font-size:13px;">
      Log in to the NOC dashboard to review and export the report.
    </p>"""
    return subject, _html_wrap(subject, body)


def _build_incident_resolved(
    *,
    ref_no: str,
    site_name: str,
    technician_name: str,
    severity: str,
    resolved_at: str,
    description: str,
) -> tuple[str, str]:
    subject = f"Incident Resolved — {ref_no} at {site_name}"
    body = f"""
    <h2 style="margin:0 0 8px;font-size:18px;">Incident Resolved</h2>
    <p style="margin:0 0 16px;color:#6b7280;">{_badge(severity.upper(), _severity_colour(severity))} Incident has been resolved.</p>
    {_detail_table(
        _kv_row("Reference", ref_no),
        _kv_row("Site", site_name),
        _kv_row("Severity", severity.upper()),
        _kv_row("Resolved By", technician_name),
        _kv_row("Resolved At", resolved_at),
        _kv_row("Description", description[:200] + ("…" if len(description) > 200 else "")),
    )}
    <p style="margin:24px 0 0;color:#6b7280;font-size:13px;">
      Please confirm closure and check if an incident report has been submitted.
    </p>"""
    return subject, _html_wrap(subject, body)


def _build_sla_breach(
    *,
    ref_no: str | None,
    site_name: str,
    severity: str,
    milestone: str,
    time_overdue: str,
) -> tuple[str, str]:
    _MILESTONE_LABELS = {
        "respond": "Response time",
        "onsite": "On-site arrival",
        "temp_restore": "Temporary restoration",
    }
    milestone_label = _MILESTONE_LABELS.get(milestone, milestone.replace("_", " ").title())
    ref = ref_no or "N/A"
    subject = f"⚠ SLA BREACHED — {milestone_label} | {ref} | {site_name}"
    body = f"""
    <h2 style="margin:0 0 8px;font-size:18px;color:#dc2626;">SLA Milestone Breached</h2>
    <p style="margin:0 0 16px;color:#6b7280;">
      {_badge("BREACH", "#dc2626")} {_badge(severity.upper(), _severity_colour(severity))}
      The following SLA milestone has been missed. Penalty exposure applies per Annexure H.
    </p>
    {_detail_table(
        _kv_row("Reference", ref),
        _kv_row("Site", site_name),
        _kv_row("Severity", severity.upper()),
        _kv_row("Milestone", milestone_label),
        _kv_row("Time Overdue", time_overdue),
    )}
    <p style="margin:24px 0 0;padding:12px;background:#fef2f2;border-left:4px solid #dc2626;border-radius:4px;font-size:13px;">
      <strong>Action required:</strong> Escalate to management and log an incident update immediately.
      Three or more breaches in a quarter allows SEACOM to terminate the contract (Annexure H).
    </p>"""
    return subject, _html_wrap(subject, body)


def _build_sla_warning(
    *,
    ref_no: str | None,
    site_name: str,
    severity: str,
    milestone: str,
    time_remaining: str,
) -> tuple[str, str]:
    _MILESTONE_LABELS = {
        "respond": "Response time",
        "onsite": "On-site arrival",
        "temp_restore": "Temporary restoration",
    }
    milestone_label = _MILESTONE_LABELS.get(milestone, milestone.replace("_", " ").title())
    ref = ref_no or "N/A"
    subject = f"SLA At Risk — {milestone_label} | {ref} | {site_name}"
    body = f"""
    <h2 style="margin:0 0 8px;font-size:18px;color:#d97706;">SLA Milestone At Risk</h2>
    <p style="margin:0 0 16px;color:#6b7280;">
      {_badge("AT RISK", "#d97706")} {_badge(severity.upper(), _severity_colour(severity))}
      The following SLA deadline is approaching. Take action now to avoid a breach.
    </p>
    {_detail_table(
        _kv_row("Reference", ref),
        _kv_row("Site", site_name),
        _kv_row("Severity", severity.upper()),
        _kv_row("Milestone", milestone_label),
        _kv_row("Time Remaining", time_remaining),
    )}"""
    return subject, _html_wrap(subject, body)


def _build_incident_report_submitted(
    *,
    ref_no: str,
    site_name: str,
    technician_name: str,
    submitted_at: str,
) -> tuple[str, str]:
    subject = f"Incident Report Submitted — {ref_no} at {site_name}"
    body = f"""
    <h2 style="margin:0 0 8px;font-size:18px;">Incident Report Submitted</h2>
    <p style="margin:0 0 16px;color:#6b7280;">A technician has submitted an incident report. Please review and export it from the Incident Reports tab.</p>
    {_detail_table(
        _kv_row("Reference", ref_no),
        _kv_row("Site", site_name),
        _kv_row("Submitted By", technician_name),
        _kv_row("Submitted At", submitted_at),
    )}"""
    return subject, _html_wrap(subject, body)


def _build_technician_escalation(
    *,
    technician_name: str,
    priority: str,
    reason: str,
    escalated_by: str,
) -> tuple[str, str]:
    colour = "#dc2626" if priority.upper() == "HIGH" else "#d97706"
    subject = f"Technician Escalation — {priority.upper()} — {technician_name}"
    body = f"""
    <h2 style="margin:0 0 8px;font-size:18px;color:{colour};">Technician Escalation</h2>
    <p style="margin:0 0 16px;color:#6b7280;">{_badge(priority.upper(), colour)} An escalation request has been raised.</p>
    {_detail_table(
        _kv_row("Technician", technician_name),
        _kv_row("Priority", priority.upper()),
        _kv_row("Raised By", escalated_by),
        _kv_row("Reason", reason[:300] + ("…" if len(reason) > 300 else "")),
    )}"""
    return subject, _html_wrap(subject, body)


# ---------------------------------------------------------------------------
# Low-level send helper
# ---------------------------------------------------------------------------

async def _send_email_async(
    to: Sequence[str],
    subject: str,
    html: str,
) -> None:
    """Send a single HTML email via SMTP (async). Silently skips if SMTP is not configured."""
    if not app_settings.smtp_enabled:
        LOG.debug("SMTP not configured — skipping email: {}", subject)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{app_settings.SMTP_FROM_NAME} <{app_settings.SMTP_USER}>"
    msg["To"] = ", ".join(to)
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=app_settings.SMTP_HOST,
            port=app_settings.SMTP_PORT,
            username=app_settings.SMTP_USER,
            password=app_settings.SMTP_PASSWORD,
            start_tls=app_settings.SMTP_USE_TLS,
        )
        LOG.info("Email sent → {} | {}", to, subject)
    except Exception as exc:
        LOG.warning("Failed to send email '{}' → {}: {}", subject, to, exc)


def _fire_and_forget(to: Sequence[str], subject: str, html: str) -> None:
    """Run email sending in a daemon thread so it never blocks the request."""
    def _run() -> None:
        asyncio.run(_send_email_async(to, subject, html))

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class EmailService:
    """Convenience wrappers — call these from service/API layers."""

    @staticmethod
    def send_task_completed(
        *,
        ref_no: str,
        site_name: str,
        technician_name: str,
        task_type: str,
        completed_at: str,
        recipients: Sequence[str] | None = None,
    ) -> None:
        to = list(recipients or app_settings.noc_email_list)
        if not to:
            return
        subject, html = _build_task_completed(
            ref_no=ref_no, site_name=site_name, technician_name=technician_name,
            task_type=task_type, completed_at=completed_at,
        )
        _fire_and_forget(to, subject, html)

    @staticmethod
    def send_incident_resolved(
        *,
        ref_no: str,
        site_name: str,
        technician_name: str,
        severity: str,
        resolved_at: str,
        description: str,
        recipients: Sequence[str] | None = None,
    ) -> None:
        to = list(recipients or app_settings.noc_email_list)
        if not to:
            return
        subject, html = _build_incident_resolved(
            ref_no=ref_no, site_name=site_name, technician_name=technician_name,
            severity=severity, resolved_at=resolved_at, description=description,
        )
        _fire_and_forget(to, subject, html)

    @staticmethod
    def send_sla_breach(
        *,
        ref_no: str | None,
        site_name: str,
        severity: str,
        milestone: str,
        time_overdue: str,
        recipients: Sequence[str] | None = None,
    ) -> None:
        to = list(recipients or app_settings.noc_email_list)
        if not to:
            return
        subject, html = _build_sla_breach(
            ref_no=ref_no, site_name=site_name, severity=severity,
            milestone=milestone, time_overdue=time_overdue,
        )
        _fire_and_forget(to, subject, html)

    @staticmethod
    def send_sla_warning(
        *,
        ref_no: str | None,
        site_name: str,
        severity: str,
        milestone: str,
        time_remaining: str,
        recipients: Sequence[str] | None = None,
    ) -> None:
        to = list(recipients or app_settings.noc_email_list)
        if not to:
            return
        subject, html = _build_sla_warning(
            ref_no=ref_no, site_name=site_name, severity=severity,
            milestone=milestone, time_remaining=time_remaining,
        )
        _fire_and_forget(to, subject, html)

    @staticmethod
    def send_incident_report_submitted(
        *,
        ref_no: str,
        site_name: str,
        technician_name: str,
        submitted_at: str,
        recipients: Sequence[str] | None = None,
    ) -> None:
        to = list(recipients or app_settings.noc_email_list)
        if not to:
            return
        subject, html = _build_incident_report_submitted(
            ref_no=ref_no, site_name=site_name,
            technician_name=technician_name, submitted_at=submitted_at,
        )
        _fire_and_forget(to, subject, html)

    @staticmethod
    def send_technician_escalation(
        *,
        technician_name: str,
        priority: str,
        reason: str,
        escalated_by: str,
        recipients: Sequence[str] | None = None,
    ) -> None:
        to = list(recipients or app_settings.noc_email_list)
        if not to:
            return
        subject, html = _build_technician_escalation(
            technician_name=technician_name, priority=priority,
            reason=reason, escalated_by=escalated_by,
        )
        _fire_and_forget(to, subject, html)
