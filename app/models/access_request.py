from uuid import UUID
from typing import TYPE_CHECKING
from sqlmodel import SQLModel, Field, DateTime, Relationship
from abc import ABC
from datetime import datetime

from app.utils.enums import AccessRequestStatus
from app.utils.funcs import utcnow
from .base import BaseDB

if TYPE_CHECKING:
    from .technician import Technician
    from .site import Site


class BaseAccessRequest(SQLModel, ABC):
    technician_id: UUID = Field(foreign_key="technicians.id")
    site_id: UUID = Field(foreign_key="sites.id")
    description: str = Field(nullable=False, max_length=2000)
    start_time: datetime = Field(nullable=False, sa_type=DateTime(timezone=True))  # type: ignore
    end_time: datetime = Field(nullable=False, sa_type=DateTime(timezone=True))  # type: ignore
    report_type: str | None = Field(default="general")


class AccessRequest(BaseDB, BaseAccessRequest, table=True):
    __tablename__ = "access_requests"  # type: ignore

    status: AccessRequestStatus = Field(default=AccessRequestStatus.REQUESTED)
    access_code: str | None = Field(default=None)
    seacom_ref: str | None = Field(
        default=None, max_length=100, description="SEACOM Reference Number from client"
    )
    approved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))  # type: ignore
    task_id: UUID | None = Field(default=None, foreign_key="tasks.id")
    report_type: str | None = Field(default="general", nullable=True)

    technician: "Technician" = Relationship(back_populates="access_requests")
    site: "Site" = Relationship(back_populates="access_requests")

    def approve(self, seacom_ref: str) -> None:
        self.status = AccessRequestStatus.APPROVED
        self.approved_at = utcnow()
        self.seacom_ref = seacom_ref
        self.access_code = seacom_ref  # Keep for backwards compatibility
        self.touch()

    def reject(self) -> None:
        self.status = AccessRequestStatus.REJECTED
        self.touch()


class AccessRequestCreate(BaseAccessRequest): ...


class AccessRequestUpdate(SQLModel):
    description: str | None = Field(default=None, max_length=2000)
    start_time: datetime | None = Field(default=None)
    task_id: UUID | None = Field(default=None)


class AccessRequestResponse(BaseDB, BaseAccessRequest):
    status: AccessRequestStatus = Field(default=AccessRequestStatus.REQUESTED)
    access_code: str | None = Field(default=None)
    seacom_ref: str | None = Field(default=None)
    task_id: UUID | None = Field(default=None)
    technician_name: str = Field(default="")
    technician_id_no: str = Field(default="")
    site_name: str = Field(default="")
