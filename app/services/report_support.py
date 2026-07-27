from typing import Any

from loguru import logger as LOG
from pydantic import ValidationError
from sqlalchemy import and_
from sqlmodel import Session, select

from app.models import Notification, User
from app.models.report_data import (
    DieselReportData,
    RepeaterReportData,
    RoutePatrolReportData,
)
from app.utils.enums import ReportType, UserRole
from app.utils.funcs import utcnow

_REPORT_DATA_SCHEMAS = {
    ReportType.REPEATER: RepeaterReportData,
    ReportType.DIESEL: DieselReportData,
    ReportType.ROUTINE_DRIVE: RoutePatrolReportData,
}


def coerce_diesel_number(value: Any) -> float:
    """
    Best-effort float from a diesel numeric field, returning 0.0 rather than raising.

    Field data is dirty: litres and amounts arrive as `22`, `"22.51"`, `"R21.28"`,
    `"R563,30"` (comma decimal), `""`, and `"N/A"`. A history spanning years will
    hit all of them, so a total must never be able to 500 the request.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return 0.0

    cleaned = value.strip().upper().removeprefix("R").strip()
    if not cleaned or cleaned in {"N/A", "NA", "-"}:
        return 0.0
    # "563,30" is a decimal comma; "1,563.30" is a thousands separator.
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")

    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def coerce_diesel_gen_no(value: Any) -> tuple[int, bool]:
    """
    Resolve a fill-up's generator number to 1 or 2.

    Returns `(gen_no, inferred)`. `inferred` is True when the entry carried no
    usable `gen_no` and was defaulted to generator 1 — a site with one generator
    frequently omits the field entirely.
    """
    if isinstance(value, bool):
        return 1, True
    if isinstance(value, (int, float)):
        return (2, False) if int(value) == 2 else (1, int(value) != 1)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits == "2":
            return 2, False
        if digits == "1":
            return 1, False
    return 1, True


def validate_report_data_schema(report_type: ReportType, data: Any) -> None:
    """Warn (never raise) when `data` drifts from the canonical schema for its
    report type (see docs/report-schemas.md). This is the Phase 4 regression
    guard for the mobile/web/backend key mismatches fixed in
    REPORT_PDF_ISSUES.md — a mismatch here means a report will render with
    blank sections or missing fields in the exported PDF, so it should show
    up in logs immediately rather than being discovered by a technician
    reading a broken PDF weeks later.

    Deliberately non-blocking: a schema edge case must never stop a
    technician's field submission from saving.
    """
    schema = _REPORT_DATA_SCHEMAS.get(report_type)
    if schema is None or not isinstance(data, dict):
        return
    try:
        schema.model_validate(data)
    except ValidationError as e:
        LOG.warning(
            "report_data_schema_drift report_type={} errors={}",
            report_type,
            e.errors(include_url=False, include_context=False),
        )


def get_noc_user_ids(session: Session) -> list:
    """Return active NOC user ids for shared report notifications."""
    noc_users = session.exec(
        select(User).where(
            and_(
                User.role == UserRole.NOC,
                User.deleted_at.is_(None),
            )
        )
    ).all()
    return [user.id for user in noc_users]


def create_noc_notifications(session: Session, template: Any) -> None:
    """Send notification template to all active NOC users."""
    for user_id in get_noc_user_ids(session):
        session.add(
            Notification(
                user_id=user_id,
                title=template.title,
                message=template.message,
                priority=template.priority,
            )
        )
    session.commit()


def upload_storage_file(
    *,
    file_content: bytes,
    filename: str,
    content_type: str,
    folder: str,
) -> dict[str, Any]:
    """Upload a file via shared storage service."""
    from app.services.file import FileService

    return FileService().upload_file_sync(
        file_content=file_content,
        filename=filename,
        content_type=content_type,
        folder=folder,
    )


def normalize_attachment_item(item: Any) -> dict[str, Any]:
    """Normalize single attachment object to shared frontend-friendly shape."""
    if isinstance(item, str):
        return {
            "url": item,
            "public_url": item,
            "signed_url": None,
            "file_path": None,
            "path": None,
            "original_name": None,
            "content_type": None,
            "size": None,
        }

    if isinstance(item, dict):
        file_path = item.get("file_path") or item.get("path")
        url = item.get("public_url") or item.get("url") or item.get("signed_url")
        if not url and isinstance(file_path, str):
            from app.services.file import FileService

            url = FileService().get_public_url(file_path)

        normalized = {
            "url": url,
            "public_url": item.get("public_url") or url,
            "signed_url": item.get("signed_url"),
            "file_path": file_path,
            "path": file_path,
            "original_name": item.get("original_name")
            or item.get("name")
            or item.get("filename"),
            "content_type": item.get("content_type") or item.get("mime_type"),
            "size": item.get("size"),
        }
        if item.get("uploaded_at"):
            normalized["uploaded_at"] = item.get("uploaded_at")
        if item.get("label"):
            normalized["label"] = item.get("label")
        return normalized

    return {
        "url": None,
        "public_url": None,
        "signed_url": None,
        "file_path": None,
        "path": None,
        "original_name": None,
        "content_type": None,
        "size": None,
    }


def normalize_attachments(attachments: Any) -> dict[str, Any] | None:
    """Normalize attachments into canonical {'files': [...]} shape."""
    if attachments is None:
        return None

    files: list[dict[str, Any]] = []

    if isinstance(attachments, list):
        files = [normalize_attachment_item(item) for item in attachments]
    elif isinstance(attachments, str):
        files = [normalize_attachment_item(attachments)]
    elif isinstance(attachments, dict):
        if isinstance(attachments.get("files"), list):
            files = [normalize_attachment_item(item) for item in attachments["files"]]
        elif any(
            key in attachments
            for key in ("url", "public_url", "file_path", "path", "filename", "name")
        ):
            files = [normalize_attachment_item(attachments)]
        else:
            for key, value in attachments.items():
                normalized = normalize_attachment_item(value)
                normalized["label"] = key
                files.append(normalized)
    else:
        return None

    cleaned_files = [
        entry for entry in files if entry.get("url") or entry.get("file_path")
    ]
    return {"files": cleaned_files}


def build_storage_attachment(
    *,
    upload_result: dict[str, Any],
    original_name: str,
    content_type: str,
    size: int | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Build shared attachment entry from storage upload result."""
    attachment = normalize_attachment_item(
        {
            "file_path": upload_result.get("file_path"),
            "path": upload_result.get("file_path"),
            "public_url": upload_result.get("public_url"),
            "url": upload_result.get("public_url"),
            "signed_url": upload_result.get("signed_url"),
            "original_name": original_name,
            "content_type": content_type,
            "size": size,
            "uploaded_at": utcnow().isoformat(),
            "label": label,
        }
    )
    return attachment


def append_attachment_entry(
    attachments: dict[str, Any] | None,
    bucket: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Append attachment entry into target bucket while preserving other buckets."""
    payload = dict(attachments or {})
    current_bucket = payload.get(bucket)
    items = list(current_bucket) if isinstance(current_bucket, list) else []
    items.append(entry)
    payload[bucket] = items
    return payload
