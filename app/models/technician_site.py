from uuid import UUID
from datetime import datetime
from sqlmodel import SQLModel, Field

from app.utils.funcs import utcnow


class TechnicianSite(SQLModel, table=True):
    """Join table: many-to-many between technicians and their assigned service sites."""

    __tablename__ = "technician_sites"  # type: ignore

    technician_id: UUID = Field(foreign_key="technicians.id", primary_key=True)
    site_id: UUID = Field(foreign_key="sites.id", primary_key=True)
    created_at: datetime = Field(default_factory=utcnow)