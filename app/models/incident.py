from uuid import UUID
from typing import TYPE_CHECKING, Any
from datetime import datetime
from sqlmodel import SQLModel, Field, DateTime, Column, Relationship
from sqlalchemy.dialects.postgresql import JSONB
from abc import ABC

from .base import BaseDB
from app.utils.enums import IncidentStatus, IncidentSeverity
from app.utils.funcs import utcnow

if TYPE_CHECKING:
    from .site import Site
    from .technician import Technician
    from .client import Client


class BaseIncident(SQLModel, ABC):
    client_id: UUID | None = Field(default=None, foreign_key="clients.id")  # Client (SEACOM, Vodacom, etc.)
    ref_no: str | None = Field(default=None, max_length=100)  # Reference number from client
    seacom_ref: str | None = Field(default=None, max_length=100)  # Keep for backwards compatibility
    description: str = Field(max_length=2000, nullable=False)
    severity: IncidentSeverity = Field(default=IncidentSeverity.MINOR, nullable=False)
    start_time: datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore
    attachments: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    site_id: UUID = Field(foreign_key="sites.id")
    technician_id: UUID = Field(foreign_key="technicians.id")


class Incident(BaseDB, BaseIncident, table=True):
    __tablename__ = "incidents" # type: ignore

    status: IncidentStatus = Field(default=IncidentStatus.OPEN, nullable=False)
    resolved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore

    # SLA milestone timestamps — tracked separately per Annexure H
    responded_at:            datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore
    arrived_on_site_at:      datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore
    temporarily_restored_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore
    permanently_restored_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore

    # Tracking who assigned the incident (NOC/Admin user)
    assigned_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")
    assigned_by_name: str | None = Field(default=None, max_length=200)

    site: 'Site' = Relationship(back_populates="incidents")
    technician: 'Technician' = Relationship(back_populates="incidents")
    client: 'Client' = Relationship(back_populates="incidents")

    def start(self) -> None:
        """"""
        self.status = IncidentStatus.IN_PROGRESS
        self.touch()

    def resolve(self) -> None:
        """"""
        self.status = IncidentStatus.RESOLVED
        self.resolved_at = utcnow()
        self.touch()

    def mark_responded(self) -> None:
        if not self.responded_at:
            self.responded_at = utcnow()
            self.touch()

    def mark_arrived_on_site(self) -> None:
        if not self.arrived_on_site_at:
            self.arrived_on_site_at = utcnow()
            self.touch()

    def mark_temporarily_restored(self) -> None:
        if not self.temporarily_restored_at:
            self.temporarily_restored_at = utcnow()
            self.touch()

    def mark_permanently_restored(self) -> None:
        if not self.permanently_restored_at:
            self.permanently_restored_at = utcnow()
            self.touch()


class IncidentCreate(BaseIncident): ...


class IncidentUpdate(SQLModel):
    client_id: UUID | None = Field(default=None, foreign_key="clients.id")
    ref_no: str | None = Field(default=None, max_length=100)
    seacom_ref: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000, nullable=False)
    severity: IncidentSeverity | None = Field(default=None)
    start_time: datetime | None = Field(default=None, sa_type=DateTime(timezone=True), nullable=False) # type: ignore
    attachments: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    site_id: UUID | None = Field(default=None, foreign_key="sites.id")
    technician_id: UUID | None = Field(default=None, foreign_key="technicians.id")


class IncidentResponse(BaseDB, SQLModel):
    client_id: UUID | None = Field(default=None)
    ref_no: str | None = Field(default=None, max_length=100)
    seacom_ref: str | None = Field(default=None, max_length=100)  # Keep for backwards compatibility
    description: str = Field(max_length=2000, nullable=False)
    severity: IncidentSeverity = Field(default=IncidentSeverity.MINOR)
    start_time: datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore
    attachments: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    site_id: UUID = Field(foreign_key="sites.id")
    technician_id: UUID = Field(foreign_key="technicians.id")
    status: IncidentStatus = Field(default=IncidentStatus.OPEN)
    resolved_at: datetime | None = Field(default=None)
    responded_at:            datetime | None = Field(default=None)
    arrived_on_site_at:      datetime | None = Field(default=None)
    temporarily_restored_at: datetime | None = Field(default=None)
    permanently_restored_at: datetime | None = Field(default=None)
    site_name: str = Field(default="", description="")
    site_latitude: float | None = Field(default=None, description="Site latitude coordinate for map links")
    site_longitude: float | None = Field(default=None, description="Site longitude coordinate for map links")
    technician_fullname: str = Field(default="", description="")
    client_name: str = Field(default="", description="Client name")
    # client_code field deprecated/removed
    num_attachments: int = Field(default=0, ge=0, description="")
    assigned_by_name: str = Field(default="", description="Full name of the NOC/Admin who assigned this incident")
