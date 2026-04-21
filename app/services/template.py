from __future__ import annotations

import copy
import re
from typing import Annotated, Any

from fastapi import Depends
from sqlmodel import Session, select

from app.exceptions.http import BadRequestException, NotFoundException
from app.models import (
    TenantTemplate,
    TenantTemplatePreviewResponse,
    TenantTemplateResolved,
)
from app.services.tenant_scope import normalize_tenant_id


PDF_BRANDING_TEMPLATE = "pdf.branding"

DEFAULT_TEMPLATES: dict[str, Any] = {
    PDF_BRANDING_TEMPLATE: {
        "brand": "FIELD CORE",
        "report_label": "Field Report - FIELD CORE",
        "confidential": "CONFIDENTIAL - FOR FIELD CORE INTERNAL USE ONLY",
        "incident_service_label": "Field Core incident services",
        "incident_footer_label": "Samo Engineering // Incident Report",
        "internal_use_label": "Confidential, Field Core internal use.",
        "mark_asset": "fieldcore-logo-mark.png",
        "lockup_asset": "fieldcore-logo-lockup.png",
    },
    "email.task_completed": {
        "subject": "Task Completed - {{ref_no}} at {{site_name}}",
        "body_html": (
            "<h2>Task Completed</h2>"
            "<p>{{technician_name}} completed {{task_type_label}}.</p>"
            "{{details_html}}"
        ),
    },
    "email.incident_resolved": {
        "subject": "Incident Resolved - {{ref_no}} at {{site_name}}",
        "body_html": (
            "<h2>Incident Resolved</h2>"
            "<p>{{severity_badge_html}} Incident has been resolved.</p>"
            "{{details_html}}"
        ),
    },
    "email.sla_breach": {
        "subject": "SLA BREACHED - {{milestone_label}} | {{ref_no}} | {{site_name}}",
        "body_html": (
            "<h2>SLA Milestone Breached</h2>"
            "<p>{{breach_badge_html}} {{severity_badge_html}}</p>"
            "{{details_html}}"
        ),
    },
    "email.sla_warning": {
        "subject": "SLA At Risk - {{milestone_label}} | {{ref_no}} | {{site_name}}",
        "body_html": (
            "<h2>SLA Milestone At Risk</h2>"
            "<p>{{risk_badge_html}} {{severity_badge_html}}</p>"
            "{{details_html}}"
        ),
    },
    "email.incident_report_submitted": {
        "subject": "Incident Report Submitted - {{ref_no}} at {{site_name}}",
        "body_html": (
            "<h2>Incident Report Submitted</h2>"
            "<p>{{technician_name}} submitted an incident report.</p>"
            "{{details_html}}"
        ),
    },
    "email.technician_escalation": {
        "subject": "Technician Escalation - {{priority_upper}} - {{technician_name}}",
        "body_html": (
            "<h2>Technician Escalation</h2>"
            "<p>{{priority_badge_html}} An escalation request has been raised.</p>"
            "{{details_html}}"
        ),
    },
}

_MISSING = object()
_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")


def _normalize_template_name(template_name: str) -> str:
    normalized = (template_name or "").strip()
    if not normalized:
        raise BadRequestException("template_name is required")
    return normalized


def _copy_content(content: Any) -> Any:
    return copy.deepcopy(content)


def _context_value(context: dict[str, Any], key: str) -> Any:
    current: Any = context
    for part in key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return _MISSING
    return current


def render_template_content(content: Any, context: dict[str, Any] | None = None) -> Any:
    """Render simple {{placeholder}} tokens in strings, dicts, and lists."""
    values = context or {}

    if isinstance(content, str):
        def replace(match: re.Match[str]) -> str:
            value = _context_value(values, match.group(1))
            if value is _MISSING:
                return match.group(0)
            return "" if value is None else str(value)

        return _PLACEHOLDER_RE.sub(replace, content)

    if isinstance(content, dict):
        return {
            key: render_template_content(value, values)
            for key, value in content.items()
        }

    if isinstance(content, list):
        return [render_template_content(value, values) for value in content]

    return content


class _TemplateService:
    """Resolve tenant template overrides with platform/default fallback."""

    def resolve_template(
        self,
        session: Session,
        tenant_id: str | None,
        template_name: str,
        *,
        default_content: Any = _MISSING,
    ) -> TenantTemplateResolved:
        normalized_name = _normalize_template_name(template_name)
        scoped_tenant_id = normalize_tenant_id(tenant_id)

        if scoped_tenant_id is not None:
            tenant_template = self._latest_row(session, scoped_tenant_id, normalized_name)
            if tenant_template is not None:
                return self._to_resolved(tenant_template, "tenant")

        platform_template = self._latest_row(session, None, normalized_name)
        if platform_template is not None:
            return self._to_resolved(platform_template, "platform")

        if default_content is not _MISSING:
            return TenantTemplateResolved(
                template_name=normalized_name,
                tenant_id=None,
                source="default",
                version=None,
                content=_copy_content(default_content),
            )

        if normalized_name in DEFAULT_TEMPLATES:
            return TenantTemplateResolved(
                template_name=normalized_name,
                tenant_id=None,
                source="default",
                version=None,
                content=_copy_content(DEFAULT_TEMPLATES[normalized_name]),
            )

        raise NotFoundException(f"Template '{normalized_name}' not found")

    def preview_template(
        self,
        session: Session,
        *,
        tenant_id: str | None,
        template_name: str,
        context: dict[str, Any] | None = None,
    ) -> TenantTemplatePreviewResponse:
        resolved = self.resolve_template(session, tenant_id, template_name)
        rendered = render_template_content(resolved.content, context or {})
        return TenantTemplatePreviewResponse(
            **resolved.model_dump(),
            rendered_content=rendered,
        )

    def _latest_row(
        self,
        session: Session,
        tenant_id: str | None,
        template_name: str,
    ) -> TenantTemplate | None:
        statement = (
            select(TenantTemplate)
            .where(
                TenantTemplate.template_name == template_name,
                TenantTemplate.deleted_at.is_(None),  # type: ignore[arg-type]
            )
            .order_by(TenantTemplate.version.desc(), TenantTemplate.created_at.desc())
        )
        if tenant_id is None:
            statement = statement.where(TenantTemplate.tenant_id.is_(None))  # type: ignore[arg-type]
        else:
            statement = statement.where(TenantTemplate.tenant_id == tenant_id)

        return session.exec(statement).first()

    def _to_resolved(self, template: TenantTemplate, source: str) -> TenantTemplateResolved:
        return TenantTemplateResolved(
            template_name=template.template_name,
            tenant_id=template.tenant_id,
            source=source,
            version=template.version,
            content=_copy_content(template.content),
        )


_template_service = _TemplateService()


def get_template_service() -> _TemplateService:
    return _template_service


def resolve_template(
    session: Session,
    tenant_id: str | None,
    template_name: str,
    *,
    default_content: Any = _MISSING,
) -> TenantTemplateResolved:
    return _template_service.resolve_template(
        session,
        tenant_id,
        template_name,
        default_content=default_content,
    )


TemplateServiceDep = Annotated[_TemplateService, Depends(get_template_service)]
