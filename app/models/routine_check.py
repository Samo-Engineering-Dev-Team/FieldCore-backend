from uuid import UUID
from typing import TYPE_CHECKING
from sqlmodel import SQLModel, Field, Relationship

from .base import BaseDB
from app.utils.enums import RoutineCheckStatus

if TYPE_CHECKING:
    from .report import Report


class BaseRoutineCheck(SQLModel):
    report_id: UUID = Field(foreign_key="reports.id")
    check_item: str = Field(max_length=200, nullable=False)
    status: RoutineCheckStatus = Field(default=RoutineCheckStatus.NA)
    comments: str | None = Field(default=None, max_length=2000)


class RoutineCheck(BaseDB, BaseRoutineCheck, table=True):
    __tablename__ = "routine_checks"  # type: ignore

    report: "Report" = Relationship(back_populates="routine_check")


class RoutineCheckCreate(BaseRoutineCheck): ...


class RoutineCheckUpdate(SQLModel):
    check_item: str | None = Field(default=None, max_length=200)
    status: RoutineCheckStatus | None = Field(default=None)
    comments: str | None = Field(default=None, max_length=2000)


class RoutineCheckResponse(BaseDB, BaseRoutineCheck): ...
