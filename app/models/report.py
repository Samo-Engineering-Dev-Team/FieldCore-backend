from uuid import UUID
from typing import TYPE_CHECKING, Any, List
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy.dialects.postgresql import JSONB

from .base import BaseDB
from app.utils.enums import ReportType, ReportStatus

if TYPE_CHECKING:
    from .technician import Technician
    from .task import Task
    from .routine_check import RoutineCheck
    from .routine_issues import RoutineIssue


class BaseReport(SQLModel):
    report_type: ReportType = Field(nullable=False, description="")
    data: dict[str, Any] = Field(nullable=False, sa_type=JSONB)
    attachments: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    service_provider: str = Field(max_length=100, nullable=False)
    seacom_ref: str | None = Field(default=None, max_length=100)
    technician_id: UUID = Field(foreign_key="technicians.id")
    task_id: UUID = Field(foreign_key="tasks.id")


class Report(BaseDB, BaseReport, table=True):
    __tablename__ = "reports" # type: ignore

    status: ReportStatus = Field(default=ReportStatus.PENDING, nullable=False)

    technician: 'Technician' = Relationship(back_populates="reports")
    task: 'Task' = Relationship(back_populates="reports")
    routine_check: 'RoutineCheck' = Relationship(back_populates="report")
    routine_issues: List['RoutineIssue'] = Relationship(back_populates="report")

    def start(self) -> None:
        self.status = ReportStatus.STARTED
        self.touch()
    
    def complete(self) -> None:
        self.status = ReportStatus.COMPLETED
        self.touch()


class ReportCreate(BaseReport): ...


class ReportUpdate(SQLModel):
    data: dict[str, Any] | None = Field(default=None)
    attachments: dict[str, Any] | None = Field(default=None)
    status: ReportStatus | None = Field(default=None)


class ReportResponse(BaseDB, BaseReport):
    status: ReportStatus = Field()
    technician_fullname: str = Field(default="")
    num_attachments: int = Field(default=0, ge=0)
