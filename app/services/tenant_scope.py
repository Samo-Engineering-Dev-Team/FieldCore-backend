from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import and_
from sqlmodel import Session, select

from app.core.settings import app_settings
from app.exceptions.http import BadRequestException, ForbiddenException
from app.utils.enums import UserRole

TENANT_STORAGE_ROOT = "tenants"
TENANT_UPLOAD_ROOT = "uploads"
TENANT_NOC_EMAIL_SETTING_KEY = "noc_email_addresses"


def normalize_tenant_id(tenant_id: str | None) -> str | None:
    if tenant_id is None:
        return None
    normalized = tenant_id.strip()
    return normalized or None


def require_tenant_id(
    tenant_id: str | None,
    message: str = "tenant scope is required for this operation",
) -> str:
    normalized = normalize_tenant_id(tenant_id)
    if normalized is None:
        raise ForbiddenException(message)
    return normalized


def _split_storage_segments(path: str) -> list[str]:
    if not isinstance(path, str):
        raise BadRequestException("storage path must be a string")

    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        raise BadRequestException("storage path cannot be empty")

    segments = [segment for segment in normalized.split("/") if segment and segment != "."]
    if any(segment == ".." for segment in segments):
        raise BadRequestException("storage path cannot contain '..'")
    return segments


def sanitize_storage_folder(folder: str | None) -> str:
    if folder is None:
        return ""
    return "/".join(_split_storage_segments(folder))


def build_tenant_storage_key(
    tenant_id: str,
    object_name: str,
    folder: str | None = None,
) -> str:
    scoped_tenant_id = require_tenant_id(tenant_id)
    segments = [TENANT_STORAGE_ROOT, scoped_tenant_id, TENANT_UPLOAD_ROOT]
    if folder:
        segments.extend(_split_storage_segments(folder))
    segments.extend(_split_storage_segments(object_name))
    return "/".join(segments)


def is_storage_path_in_tenant(file_path: str, tenant_id: str | None) -> bool:
    scoped_tenant_id = normalize_tenant_id(tenant_id)
    if scoped_tenant_id is None:
        return False

    segments = _split_storage_segments(file_path)
    required_prefix = [TENANT_STORAGE_ROOT, scoped_tenant_id, TENANT_UPLOAD_ROOT]
    return segments[: len(required_prefix)] == required_prefix


def assert_storage_path_in_tenant(file_path: str, tenant_id: str | None) -> None:
    scoped_tenant_id = require_tenant_id(tenant_id)
    if not is_storage_path_in_tenant(file_path, scoped_tenant_id):
        raise ForbiddenException("file path is outside current tenant scope")


def _tenant_user_scope_clause(user_model: Any, tenant_id: str | None) -> Any:
    scoped_tenant_id = normalize_tenant_id(tenant_id)
    if scoped_tenant_id is None:
        return user_model.tenant_id.is_(None)
    return user_model.tenant_id == scoped_tenant_id


def list_tenant_user_ids(
    session: Session,
    roles: Sequence[UserRole],
    tenant_id: str | None,
) -> list[UUID]:
    from app.models import User

    statement = select(User.id).where(
        and_(
            User.role.in_(list(roles)),  # type: ignore[arg-type]
            User.deleted_at.is_(None),
            _tenant_user_scope_clause(User, tenant_id),
        )
    )
    return list(session.exec(statement).all())


def list_tenant_user_emails(
    session: Session,
    roles: Sequence[UserRole],
    tenant_id: str | None,
) -> list[str]:
    from app.models import User

    statement = select(User.email).where(
        and_(
            User.role.in_(list(roles)),  # type: ignore[arg-type]
            User.deleted_at.is_(None),
            _tenant_user_scope_clause(User, tenant_id),
        )
    )
    return [email for email in session.exec(statement).all() if email]


def get_tenant_noc_user_ids(session: Session, tenant_id: str | None) -> list[UUID]:
    return list_tenant_user_ids(session, (UserRole.NOC,), tenant_id)


def get_tenant_management_user_ids(session: Session, tenant_id: str | None) -> list[UUID]:
    return list_tenant_user_ids(session, (UserRole.NOC, UserRole.MANAGER), tenant_id)


def _parse_email_setting_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [entry.strip() for entry in value.split(",") if entry.strip()]
    if isinstance(value, dict):
        nested = value.get("emails") or value.get("recipients")
        if nested is not None:
            return _parse_email_setting_value(nested)
    if isinstance(value, Iterable):
        parsed: list[str] = []
        for entry in value:
            if isinstance(entry, str) and entry.strip():
                parsed.append(entry.strip())
        return parsed
    return []


def get_tenant_setting_value(
    session: Session,
    key: str,
    tenant_id: str | None,
) -> Any | None:
    from app.models import SystemSetting

    scoped_tenant_id = normalize_tenant_id(tenant_id)
    if scoped_tenant_id is None:
        return None

    statement = select(SystemSetting.value).where(
        SystemSetting.key == key,
        SystemSetting.tenant_id == scoped_tenant_id,
    )
    return session.exec(statement).first()


def get_tenant_notification_recipients(
    session: Session,
    tenant_id: str | None,
) -> list[str]:
    scoped_tenant_id = normalize_tenant_id(tenant_id)
    if scoped_tenant_id is None:
        return list(app_settings.noc_email_list)

    configured = get_tenant_setting_value(session, TENANT_NOC_EMAIL_SETTING_KEY, scoped_tenant_id)
    parsed = _parse_email_setting_value(configured)
    if parsed:
        return parsed

    return list_tenant_user_emails(
        session,
        (UserRole.NOC, UserRole.MANAGER),
        scoped_tenant_id,
    )


def get_technician_tenant_id(session: Session, technician_id: UUID | None) -> str | None:
    if technician_id is None:
        return None

    from app.models import Technician, User

    statement = (
        select(User.tenant_id)
        .join(Technician, Technician.user_id == User.id)
        .where(
            Technician.id == technician_id,
            Technician.deleted_at.is_(None),  # type: ignore[arg-type]
            User.deleted_at.is_(None),  # type: ignore[arg-type]
        )
    )
    return normalize_tenant_id(session.exec(statement).first())


def get_task_tenant_id(session: Session, task: Any) -> str | None:
    technician_id = getattr(task, "technician_id", None)
    if technician_id is None:
        return None
    return get_technician_tenant_id(session, technician_id)


def get_incident_tenant_id(session: Session, incident: Any) -> str | None:
    technician_id = getattr(incident, "technician_id", None)
    if technician_id is None:
        return None
    return get_technician_tenant_id(session, technician_id)
