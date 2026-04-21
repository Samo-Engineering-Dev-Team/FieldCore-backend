from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func
from sqlmodel import Session, select

from app.exceptions.http import BadRequestException
from app.models import AuditLog, AuditLogListResponse, AuditLogResponse


def request_id_from_headers(request: Request) -> str | None:
    """Return caller-provided request/correlation id, if present."""
    return (
        request.headers.get("x-request-id")
        or request.headers.get("x-correlation-id")
        or request.headers.get("request-id")
    )


def _normalize_required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise BadRequestException(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _json_or_none(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    encoded = jsonable_encoder(value)
    if isinstance(encoded, dict):
        return encoded
    return {"value": encoded}


class AuditService:
    """Append-only audit writer and read-only query service."""

    def write_event(
        self,
        session: Session,
        *,
        action_type: str,
        resource: str,
        actor_user_id: UUID | None = None,
        tenant_id: str | None = None,
        before: Any = None,
        after: Any = None,
        request_id: str | None = None,
    ) -> AuditLog:
        event = AuditLog(
            actor_user_id=actor_user_id,
            tenant_id=_normalize_optional(tenant_id),
            action_type=_normalize_required(action_type, "action_type"),
            resource=_normalize_required(resource, "resource"),
            before=_json_or_none(before),
            after=_json_or_none(after),
            request_id=_normalize_optional(request_id),
        )
        session.add(event)
        return event

    def list_logs(
        self,
        session: Session,
        *,
        tenant_id: str | None = None,
        action_type: str | None = None,
        resource: str | None = None,
        actor_user_id: UUID | None = None,
        request_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> AuditLogListResponse:
        criteria = []

        normalized_tenant_id = _normalize_optional(tenant_id)
        if normalized_tenant_id is not None:
            criteria.append(AuditLog.tenant_id == normalized_tenant_id)

        normalized_action_type = _normalize_optional(action_type)
        if normalized_action_type is not None:
            criteria.append(AuditLog.action_type == normalized_action_type)

        normalized_resource = _normalize_optional(resource)
        if normalized_resource is not None:
            criteria.append(AuditLog.resource == normalized_resource)

        if actor_user_id is not None:
            criteria.append(AuditLog.actor_user_id == actor_user_id)

        normalized_request_id = _normalize_optional(request_id)
        if normalized_request_id is not None:
            criteria.append(AuditLog.request_id == normalized_request_id)

        if created_from is not None:
            criteria.append(AuditLog.created_at >= created_from)

        if created_to is not None:
            criteria.append(AuditLog.created_at <= created_to)

        count_statement = select(func.count()).select_from(AuditLog)
        statement = select(AuditLog)
        for criterion in criteria:
            count_statement = count_statement.where(criterion)
            statement = statement.where(criterion)

        total = int(session.exec(count_statement).one())
        rows = list(
            session.exec(
                statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )

        return AuditLogListResponse(
            data=[AuditLogResponse.model_validate(row) for row in rows],
            total=total,
        )


def write_audit_event(
    session: Session,
    *,
    action_type: str,
    resource: str,
    actor_user_id: UUID | None = None,
    tenant_id: str | None = None,
    before: Any = None,
    after: Any = None,
    request_id: str | None = None,
) -> AuditLog:
    return AuditService().write_event(
        session,
        action_type=action_type,
        resource=resource,
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        before=before,
        after=after,
        request_id=request_id,
    )


def get_audit_service() -> AuditService:
    return AuditService()


AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]
