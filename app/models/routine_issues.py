from uuid import UUID
from typing import TYPE_CHECKING
from sqlmodel import SQLModel, Field, Relationship

from .base import BaseDB
from app.utils.enums import RoutineIssueSeverity

if TYPE_CHECKING:
    from .report import Report


class BaseRoutineIssue(SQLModel):
    report_id: UUID = Field(foreign_key="reports.id")
    severity: RoutineIssueSeverity = Field(default=RoutineIssueSeverity.LOW)
    comments: str | None = Field(default=None, max_length=2000)


class RoutineIssue(BaseDB, BaseRoutineIssue, table=True):
    __tablename__ = "routine_issues"  # type: ignore

    report: "Report" = Relationship(back_populates="routine_issues")


class RoutineIssueCreate(BaseRoutineIssue): ...


class RoutineIssueUpdate(SQLModel):
    severity: RoutineIssueSeverity | None = Field(default=None)
    comments: str | None = Field(default=None, max_length=2000)


class RoutineIssueResponse(BaseDB, BaseRoutineIssue): ...
