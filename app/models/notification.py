from sqlmodel import SQLModel, Field, Relationship
from typing import TYPE_CHECKING
from uuid import UUID

from .base import BaseDB
from app.utils.enums import NotificationPriority

if TYPE_CHECKING:
    from .user import User


class BaseNotification(SQLModel):
    title: str = Field(description="", nullable=False, max_length=100)
    message: str = Field(description="", nullable=False, max_length=2000)
    user_id: UUID = Field(foreign_key="users.id")
    priority: NotificationPriority = Field(
        default=NotificationPriority.NORMAL, nullable=False
    )


class Notification(BaseDB, BaseNotification, table=True):
    __tablename__ = "notifications"  # type: ignore

    read: bool = Field(default=False, nullable=False)

    user: "User" = Relationship(back_populates="notifications")


class NotificationCreate(BaseNotification): ...


class NotificationResponse(BaseDB, BaseNotification):
    read: bool = Field(default=False)
