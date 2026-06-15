from datetime import datetime

from sqlmodel import SQLModel, Index, Field, Relationship, Column
from abc import ABC
from pydantic import EmailStr, field_validator

from .auth import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    validate_password_strength,
)
from typing import TYPE_CHECKING, List
from sqlalchemy import DateTime, Enum as SAEnum, func

from .base import BaseDB
from app.utils.enums import UserRole, UserStatus
from app.utils.funcs import utcnow

if TYPE_CHECKING:
    from .notification import Notification


class BaseUser(SQLModel, ABC):
    """"""

    name: str = Field(
        max_length=100,
        nullable=False,
        description="The first name of the user",
        schema_extra={"examples": {"Moses", "Bongani"}},
    )
    surname: str = Field(
        max_length=100,
        nullable=False,
        description="The last name of the user",
        schema_extra={"examples": {"Kubeka", "Smith"}},
    )
    email: EmailStr = Field(
        nullable=False,
        index=True,
        schema_extra={"examples": {"moses@samotelecoms.co.za"}},
    )
    role: UserRole = Field(
        sa_column=Column(
            SAEnum(UserRole, name="userrole", omit_aliases=False),
            nullable=False,
        ),
        description="The role of the user in the system",
    )


class User(BaseDB, BaseUser, table=True):
    """"""

    __tablename__ = "users"  # type: ignore
    __table_args__ = (
        Index(
            "uq_active_users",
            "email",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
    )

    password_hash: str = Field(nullable=False)
    credentials_updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
    must_change_password: bool = Field(default=False, nullable=False)
    sessions_revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
        description="Tokens issued before this time are rejected (logout / revoke-all)",
    )
    status: UserStatus = Field(default=UserStatus.ACTIVE, nullable=False, index=True)

    notifications: List["Notification"] = Relationship(back_populates="user")

    def activate(self) -> None:
        """"""
        self.status = UserStatus.ACTIVE
        self.touch()

    def disable(self) -> None:
        """"""
        self.status = UserStatus.DISABLED
        self.touch()

    def is_active(self) -> bool:
        """"""
        return self.status == UserStatus.ACTIVE


class UserCreate(BaseUser):
    """"""

    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH
    )

    _password_strength = field_validator("password")(validate_password_strength)


class UserUpdate(SQLModel):
    """"""

    name: str | None = Field(
        default=None,
        max_length=100,
        description="The first name of the user",
        schema_extra={"examples": {"Moses", "Bongani"}},
    )
    surname: str | None = Field(
        default=None,
        max_length=100,
        description="The last name of the user",
        schema_extra={"examples": {"Kubeka", "Smith"}},
    )
    email: EmailStr | None = None


class UserRoleUpdate(SQLModel):
    """"""

    new_role: UserRole


class UserStatusUpdate(SQLModel):
    """"""

    new_status: UserStatus


class UserResponse(BaseDB, BaseUser):
    """"""

    must_change_password: bool = False
    status: UserStatus
