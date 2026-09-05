"""
MaintenanceSchedule model — recurring site maintenance scheduling.

Three mandatory scheduled task types per SAMO/SEACOM agreement, plus
Datacenter/POP inspections:
  routine_drive           — weekly Routine Drive / fibre route visual patrol (see route_patrol.py)
  repeater_site_visit     — Repeater site monthly inspection (generates Repeater report)
  generator_diesel_refill — Generator diesel refill (generates Diesel report)
  datacenter_inspection   — Datacenter hosted-site routine inspection (generates Datacenter report)
  pop_inspection          — POP hosted-site routine inspection (generates POP report)
"""

from uuid import UUID
from datetime import datetime
from sqlmodel import SQLModel, Field, DateTime

from .base import BaseDB

SCHEDULE_TYPES = [
    "routine_drive",
    "repeater_site_visit",
    "generator_diesel_refill",
    "datacenter_inspection",
    "pop_inspection",
]
FREQUENCIES = ["weekly", "monthly", "quarterly"]

SCHEDULE_TYPE_LABELS = {
    "routine_drive": "Routine Drive",
    "repeater_site_visit": "Repeater Site Visit",
    "generator_diesel_refill": "Generator Diesel Refill",
    "datacenter_inspection": "Datacenter Inspection",
    "pop_inspection": "POP Inspection",
}


class BaseMaintenanceSchedule(SQLModel):
    site_id: UUID = Field(foreign_key="sites.id", nullable=False)
    schedule_type: str = Field(max_length=30, nullable=False)
    frequency: str = Field(max_length=20, nullable=False)
    assigned_technician_id: UUID | None = Field(
        default=None, foreign_key="technicians.id"
    )
    is_active: bool = Field(default=True, nullable=False)
    last_run_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))  # type: ignore
    next_due_at: datetime = Field(sa_type=DateTime(timezone=True), nullable=False)  # type: ignore
    # Date the technician has self-scheduled to complete this task
    scheduled_date: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )  # type: ignore
    notes: str | None = Field(default=None, max_length=2000)


class MaintenanceSchedule(BaseDB, BaseMaintenanceSchedule, table=True):
    __tablename__ = "maintenance_schedules"  # type: ignore


class BaseMaintenanceScheduleCoverage(SQLModel):
    schedule_id: UUID = Field(foreign_key="maintenance_schedules.id", nullable=False)
    week_start_at: datetime = Field(sa_type=DateTime(timezone=True), nullable=False)  # type: ignore
    week_end_at: datetime = Field(sa_type=DateTime(timezone=True), nullable=False)  # type: ignore
    original_technician_id: UUID | None = Field(
        default=None, foreign_key="technicians.id"
    )
    assigned_technician_id: UUID = Field(foreign_key="technicians.id", nullable=False)
    assigned_by_user_id: UUID = Field(foreign_key="users.id", nullable=False)
    reason: str | None = Field(default=None, max_length=2000)
    cancelled_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )  # type: ignore
    completed_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )  # type: ignore


class MaintenanceScheduleCoverage(
    BaseDB, BaseMaintenanceScheduleCoverage, table=True
):
    __tablename__ = "maintenance_schedule_coverages"  # type: ignore


class MaintenanceScheduleCreate(BaseMaintenanceSchedule): ...


class MaintenanceScheduleUpdate(SQLModel):
    schedule_type: str | None = Field(default=None, max_length=30)
    frequency: str | None = Field(default=None, max_length=20)
    assigned_technician_id: UUID | None = Field(default=None)
    is_active: bool | None = Field(default=None)
    next_due_at: datetime | None = Field(default=None)
    scheduled_date: datetime | None = Field(default=None)
    last_run_at: datetime | None = Field(default=None)
    notes: str | None = Field(default=None, max_length=2000)


class MaintenanceScheduleResponse(BaseDB, BaseMaintenanceSchedule):
    site_name: str = Field(default="")
    site_region: str | None = Field(default=None)
    site_type: str | None = Field(default=None)
    site_geofence_radius: int | None = Field(default=None)
    site_latitude: float | None = Field(
        default=None, description="Site latitude coordinate for map links"
    )
    site_longitude: float | None = Field(
        default=None, description="Site longitude coordinate for map links"
    )
    technician_fullname: str = Field(default="")
    is_overdue: bool = Field(default=False)
    completed_this_week: bool = Field(default=False)
    effective_technician_id: UUID | None = Field(default=None)
    effective_technician_fullname: str = Field(default="")
    original_technician_id: UUID | None = Field(default=None)
    original_technician_fullname: str = Field(default="")
    coverage_id: UUID | None = Field(default=None)
    coverage_reason: str | None = Field(default=None)
    coverage_week_start_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )  # type: ignore
    coverage_completed_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )  # type: ignore


class MaintenanceScheduleCoverageCreate(SQLModel):
    assigned_technician_id: UUID
    reason: str | None = Field(default=None, max_length=2000)


class MaintenanceScheduleCoverageResponse(BaseDB, BaseMaintenanceScheduleCoverage):
    assigned_technician_fullname: str = Field(default="")
    original_technician_fullname: str = Field(default="")
