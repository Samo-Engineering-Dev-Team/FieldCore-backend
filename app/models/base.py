from uuid import UUID, uuid4
from sqlmodel import SQLModel, DateTime, Field
from abc import ABC
from datetime import datetime

from app.utils.funcs import utcnow


class BaseDB(SQLModel, ABC):
    """"""

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="unique database identifier",
        schema_extra={"examples": {str(uuid4())}},
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=False,
        description="date and time with a timezone the record was created.",
        schema_extra={"examples": {str(utcnow())}},
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=False,
        description="date and time with a timezone the record was last updated.",
        schema_extra={"examples": {str(utcnow())}},
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
        description="date and time with a timezone the record was deleted.",
        schema_extra={"examples": {str(utcnow()), None}},
    )

    def touch(self) -> None:
        """Update the updated_at to the current date and time."""
        self.updated_at = utcnow()

    def soft_delete(self) -> None:
        """Mark the record as deleted."""
        self.deleted_at = utcnow()
