from typing import Any

from sqlalchemy import and_
from sqlmodel import Session, select

from app.models import Notification, User
from app.utils.enums import UserRole
from app.utils.funcs import utcnow


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
