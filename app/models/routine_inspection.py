from uuid import UUID
from typing import Any, TYPE_CHECKING
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy.dialects.postgresql import JSONB

from .base import BaseDB

if TYPE_CHECKING:
    from .site import Site
    from .task import Task
    from .technician import Technician


class BaseRoutineInspection(SQLModel):
    """Base schema for routine generator inspections"""

    data: dict[str, Any] = Field(
        nullable=False, sa_type=JSONB, description="Structured inspection data"
    )
    attachments: dict[str, Any] | None = Field(
        default=None, sa_type=JSONB, description="Attachment metadata"
    )
    site_id: UUID = Field(foreign_key="sites.id")
    task_id: UUID = Field(foreign_key="tasks.id")
    technician_id: UUID = Field(foreign_key="technicians.id")
    status: str = Field(default="draft", description="draft or completed")


class RoutineInspection(BaseDB, BaseRoutineInspection, table=True):
    """Database model for routine generator inspections"""

    __tablename__ = "routine_inspections"  # type: ignore

    site: "Site" = Relationship(back_populates="routine_inspections")
    task: "Task" = Relationship(back_populates="routine_inspections")
    technician: "Technician" = Relationship(back_populates="routine_inspections")


class RoutineInspectionCreate(BaseRoutineInspection):
    """Create schema for routine inspections"""

    pass


class RoutineInspectionUpdate(SQLModel):
    """Update schema for routine inspections"""

    data: dict[str, Any] | None = Field(default=None)
    attachments: dict[str, Any] | None = Field(default=None)
    status: str | None = Field(default=None)


class RoutineInspectionResponse(BaseDB, BaseRoutineInspection):
    """Response schema for routine inspections"""

    site_name: str | None = Field(default=None)
    technician_fullname: str | None = Field(default=None)
    seacom_ref: str | None = Field(default=None)
