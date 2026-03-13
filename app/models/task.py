from uuid import UUID
from typing import TYPE_CHECKING, List
from datetime import datetime
from sqlmodel import SQLModel, Field, DateTime, Column, Relationship
from sqlalchemy.dialects.postgresql import JSONB
from abc import ABC

from .base import BaseDB
from app.utils.enums import TaskStatus, TaskType, Region
from app.utils.funcs import utcnow

if TYPE_CHECKING:
    from .site import Site
    from technician import Technician
    from .report import Report
    from .routine_inspection import RoutineInspection


class BaseTask(SQLModel, ABC):
    seacom_ref: str | None = Field(default=None, max_length=100)
    description: str = Field(max_length=2000, nullable=False)
    start_time: datetime = Field(sa_type=DateTime(timezone=True), nullable=False) # type: ignore
    end_time: datetime = Field(sa_type=DateTime(timezone=True), nullable=False) # type: ignore
    task_type: TaskType = Field(nullable=False)
    report_type: str | None = Field(default=None)
    attachments: dict | None = Field(default=None, sa_type=JSONB)
    site_id: UUID = Field(foreign_key="sites.id")
    technician_id: UUID = Field(foreign_key="technicians.id")
    # Additional technicians for large/shared jobs (stored as list of UUID strings)
    additional_technician_ids: list[str] | None = Field(default=None, sa_type=JSONB)
    # On-hold state — set when a technician defers work to the next day
    hold_reason: str | None = Field(default=None, max_length=500)
    held_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore


class Task(BaseDB, BaseTask, table=True):
    __tablename__ = "tasks" # type: ignore

    status: TaskStatus = Field(default=TaskStatus.PENDING, nullable=False)
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore

    # RHS completion feedback (short summary of what was done — no formal report required for RHS)
    feedback: str | None = Field(default=None, max_length=2000)

    # Tracking who assigned the task (NOC/Admin user)
    assigned_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")
    assigned_by_name: str | None = Field(default=None, max_length=200)

    site: 'Site' = Relationship(back_populates="tasks")
    technician: 'Technician' = Relationship(back_populates="tasks")
    reports: List['Report'] = Relationship(back_populates="task")
    routine_inspections: List['RoutineInspection'] = Relationship(back_populates="task")

    def start(self) -> None:
        """"""
        self.status = TaskStatus.STARTED
        self.touch()
    
    def complete(self) -> None:
        """"""
        self.status = TaskStatus.COMPLETED
        self.completed_at = utcnow()
        self.touch()
    
    def fail(self) -> None:
        """"""
        self.status = TaskStatus.FAILED
        self.touch()

    def hold(self, reason: str | None = None) -> None:
        """"""
        self.status = TaskStatus.ON_HOLD
        self.hold_reason = reason
        self.held_at = utcnow()
        self.touch()

    def resume(self) -> None:
        """Resume a held task — restores STARTED status and clears hold fields."""
        self.status = TaskStatus.STARTED
        self.hold_reason = None
        self.held_at = None
        self.touch()


class TaskCreate(BaseTask): ...


class TaskUpdate(SQLModel):
    seacom_ref: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000, nullable=False)
    start_time: datetime | None = Field(default=None, sa_type=DateTime(timezone=True), nullable=False) # type: ignore
    end_time: datetime | None = Field(default=None, sa_type=DateTime(timezone=True), nullable=False) # type: ignore
    task_type: TaskType | None = Field(default=None, nullable=False)
    report_type: str | None = Field(default=None)
    attachments: dict | None = Field(default=None, sa_column=Column(JSONB))
    site_id: UUID | None = Field(default=None, foreign_key="sites.id")
    technician_id: UUID | None = Field(default=None, foreign_key="technicians.id")
    additional_technician_ids: list[str] | None = Field(default=None)


class TaskResponse(BaseDB, BaseTask):
    status: TaskStatus
    completed_at: datetime | None = Field(default=None) # type: ignore
    feedback: str | None = Field(default=None)
    report_type: str | None = Field(default=None)
    site_name: str = Field(default="", description="")
    site_region: Region = Field(default="", description="")
    site_latitude: float | None = Field(default=None, description="Site latitude coordinate for map links")
    site_longitude: float | None = Field(default=None, description="Site longitude coordinate for map links")
    technician_fullname: str = Field(default="", description="")
    additional_technician_names: list[str] = Field(default_factory=list, description="Names of additional technicians")
    num_attachments: int = Field(default=0, ge=0, description="")
    assigned_by_name: str = Field(default="", description="Full name of the NOC/Admin who assigned this task")
