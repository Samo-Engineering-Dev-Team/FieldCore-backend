"""
FaultUpdate model — tracks mandatory communication log during active incidents.

Per Annexure H of the SAMO/SEACOM agreement:
  - Critical faults: hourly updates by phone before restore; daily by email after
  - Major faults:    every 2 hours; twice-weekly after restore
"""

from uuid import UUID
from typing import TYPE_CHECKING
from datetime import datetime
from sqlmodel import SQLModel, Field, DateTime, Relationship

from .base import BaseDB

if TYPE_CHECKING:
    from .user import User
    from .incident import Incident


UPDATE_TYPES = ["phone_call", "email", "app_update"]


class BaseFaultUpdate(SQLModel):
    incident_id: UUID = Field(foreign_key="incidents.id", nullable=False)
    update_type: str  = Field(max_length=20, nullable=False)   # phone_call | email | app_update
    message:     str  = Field(max_length=4000, nullable=False)
    sent_by:     UUID = Field(foreign_key="users.id", nullable=False)
    is_overdue:  bool = Field(default=False, nullable=False)


class FaultUpdate(BaseDB, BaseFaultUpdate, table=True):
    __tablename__ = "incident_updates"  # type: ignore

    sent_by_name: str = Field(default="", max_length=200)  # denormalized for quick display


class FaultUpdateCreate(SQLModel):
    update_type: str = Field(max_length=20)
    message:     str = Field(max_length=4000)


class FaultUpdateResponse(BaseDB, BaseFaultUpdate):
    sent_by_name: str = Field(default="")
