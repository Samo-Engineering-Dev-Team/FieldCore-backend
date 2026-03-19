"""
MaintenanceSchedule model — recurring site maintenance scheduling.

Three mandatory scheduled task types per SAMO/SEACOM agreement:
  routine_drive           — weekly Routine Drive / fibre route visual patrol (see route_patrol.py)
  repeater_site_visit     — Repeater site monthly inspection (generates Repeater report)
  generator_diesel_refill — Generator diesel refill (generates Diesel report)
"""

from uuid import UUID
from typing import TYPE_CHECKING
from datetime import datetime
from sqlmodel import SQLModel, Field, DateTime

from .base import BaseDB

# The three mandatory scheduled task types
SCHEDULE_TYPES = ["routine_drive", "repeater_site_visit", "generator_diesel_refill"]
FREQUENCIES    = ["weekly", "monthly", "quarterly"]

SCHEDULE_TYPE_LABELS = {
    "routine_drive":            "Routine Drive",
    "repeater_site_visit":      "Repeater Site Visit",
    "generator_diesel_refill":  "Generator Diesel Refill",
}


class BaseMaintenanceSchedule(SQLModel):
    site_id:                UUID = Field(foreign_key="sites.id", nullable=False)
    schedule_type:          str  = Field(max_length=30, nullable=False)
    frequency:              str  = Field(max_length=20, nullable=False)
    assigned_technician_id: UUID | None = Field(default=None, foreign_key="technicians.id")
    is_active:              bool = Field(default=True, nullable=False)
    last_run_at:            datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore
    next_due_at:            datetime = Field(sa_type=DateTime(timezone=True), nullable=False) # type: ignore
    # Date the technician has self-scheduled to complete this task
    scheduled_date:         datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore
    notes:                  str | None = Field(default=None, max_length=2000)


class MaintenanceSchedule(BaseDB, BaseMaintenanceSchedule, table=True):
    __tablename__ = "maintenance_schedules"  # type: ignore


class MaintenanceScheduleCreate(BaseMaintenanceSchedule): ...


class MaintenanceScheduleUpdate(SQLModel):
    schedule_type:          str | None = Field(default=None, max_length=30)
    frequency:              str | None = Field(default=None, max_length=20)
    assigned_technician_id: UUID | None = Field(default=None)
    is_active:              bool | None = Field(default=None)
    next_due_at:            datetime | None = Field(default=None)
    scheduled_date:         datetime | None = Field(default=None)
    last_run_at:            datetime | None = Field(default=None)
    notes:                  str | None = Field(default=None, max_length=2000)


class MaintenanceScheduleResponse(BaseDB, BaseMaintenanceSchedule):
    site_name:              str = Field(default="")
    technician_fullname:    str = Field(default="")
    is_overdue:             bool = Field(default=False)
    completed_this_week:    bool = Field(default=False)
