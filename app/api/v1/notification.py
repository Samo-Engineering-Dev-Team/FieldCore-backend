from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models import NotificationCreate, NotificationResponse
from app.services import NotificationService, CurrentUser
from app.database import Session
from app.exceptions.http import ForbiddenException
from app.utils.enums import NotificationPriority, UserRole

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _is_management_user(current_user: CurrentUser) -> bool:
    return current_user.role in (UserRole.ADMIN, UserRole.MANAGER)


def _assert_can_access_user_notifications(target_user_id: UUID, current_user: CurrentUser) -> None:
    if current_user.user_id != target_user_id and not _is_management_user(current_user):
        raise ForbiddenException("You can only access your own notifications")


@router.post("/", response_model=NotificationResponse, status_code=201)
def create_notification(
    payload: NotificationCreate,
    service: NotificationService,
    session: Session,
    current_user: CurrentUser
) -> NotificationResponse:
    """Create a notification. Management users can create for other users."""
    if payload.user_id != current_user.user_id and not _is_management_user(current_user):
        raise ForbiddenException("Only management users can create notifications for other users")
    return service.create_notification(payload, session)


@router.get("/", response_model=List[NotificationResponse], status_code=200)
def read_notifications(
    service: NotificationService,
    session: Session,
    current_user: CurrentUser,
    priority: NotificationPriority | None = Query(None),
    user_id: UUID | None = Query(None),
    read: bool | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000)
) -> List[NotificationResponse]:
    """Get notifications for the current user (or target user if management)."""
    if user_id is None:
        user_id = current_user.user_id

    _assert_can_access_user_notifications(user_id, current_user)
    return service.read_notifications(session, priority, user_id, read, offset, limit)


@router.patch("/read-all", status_code=200)
def mark_all_as_read(
    service: NotificationService,
    session: Session,
    current_user: CurrentUser,
    user_id: UUID | None = Query(None),
) -> dict:
    target_user_id = user_id or current_user.user_id
    _assert_can_access_user_notifications(target_user_id, current_user)
    updated = service.mark_all_as_read(target_user_id, session)
    return {"updated": updated}


@router.get("/unread-count", status_code=200)
def unread_count(
    service: NotificationService,
    session: Session,
    current_user: CurrentUser,
    user_id: UUID | None = Query(None),
) -> dict:
    target_user_id = user_id or current_user.user_id
    _assert_can_access_user_notifications(target_user_id, current_user)
    return {"unread_count": service.get_unread_count(target_user_id, session)}


@router.get("/{notification_id}", response_model=NotificationResponse, status_code=200)
def read_notification(
    notification_id: UUID,
    service: NotificationService,
    session: Session,
    current_user: CurrentUser
) -> NotificationResponse:
    """Get a specific notification."""
    notification = service.read_notification(notification_id, session)
    _assert_can_access_user_notifications(notification.user_id, current_user)
    return notification


@router.delete("/{notification_id}", status_code=204)
def delete_notification(
    notification_id: UUID,
    service: NotificationService,
    session: Session,
    current_user: CurrentUser
) -> None:
    """Delete a notification."""
    notification = service.read_notification(notification_id, session)
    _assert_can_access_user_notifications(notification.user_id, current_user)
    service.delete_notification(notification_id, session)


@router.patch("/{notification_id}/read", response_model=NotificationResponse, status_code=200)
def mark_as_read(
    notification_id: UUID,
    service: NotificationService,
    session: Session,
    current_user: CurrentUser
) -> NotificationResponse:
    """Mark notification as read."""
    notification = service.read_notification(notification_id, session)
    _assert_can_access_user_notifications(notification.user_id, current_user)
    return service.read(notification_id, session)
