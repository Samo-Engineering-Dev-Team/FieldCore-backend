"""
RoutePatrol model — records weekly fibre route surveillance activities.

Per Annexure A of the SAMO/SEACOM agreement:
  - Weekly visual inspections along all fibre routes
  - Additional patrols required after 25mm+ rainfall events
  - Logbooks maintained and attested by SEACOM representative
  - Geo-tagged photos required as evidence
"""

from uuid import UUID
from typing import Any, TYPE_CHECKING
from datetime import datetime
from sqlmodel import SQLModel, Field, DateTime
from sqlalchemy.dialects.postgresql import JSONB

from .base import BaseDB


class BaseRoutePatrol(SQLModel):
    technician_id:     UUID = Field(foreign_key="technicians.id", nullable=False)
    site_id:           UUID | None = Field(default=None, foreign_key="sites.id")
    route_segment:     str  = Field(max_length=200, nullable=False)
    patrol_date:       datetime = Field(sa_type=DateTime(timezone=True), nullable=False) # type: ignore
    weather_conditions: str | None = Field(default=None, max_length=100)
    anomalies_found:   bool = Field(default=False, nullable=False)
    anomaly_details:   str | None = Field(default=None, max_length=4000)
    # Array of {url, lat, lon, altitude, speed, captured_at, address, index_number}
    photos:            dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    seacom_attested:   bool = Field(default=False, nullable=False)
    attested_at:       datetime | None = Field(default=None, sa_type=DateTime(timezone=True)) # type: ignore
    attested_by:       str | None = Field(default=None, max_length=200)


class RoutePatrol(BaseDB, BaseRoutePatrol, table=True):
    __tablename__ = "route_patrols"  # type: ignore


class RoutePatrolCreate(BaseRoutePatrol): ...


class RoutePatrolUpdate(SQLModel):
    weather_conditions: str | None = Field(default=None, max_length=100)
    anomalies_found:    bool | None = Field(default=None)
    anomaly_details:    str | None = Field(default=None, max_length=4000)
    photos:             dict[str, Any] | None = Field(default=None)
    seacom_attested:    bool | None = Field(default=None)
    attested_at:        datetime | None = Field(default=None)
    attested_by:        str | None = Field(default=None, max_length=200)


class RoutePatrolResponse(BaseDB, BaseRoutePatrol):
    technician_fullname: str = Field(default="")
    site_name:           str = Field(default="")
