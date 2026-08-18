from sqlmodel import SQLModel, Field, Relationship, Column
from typing import TYPE_CHECKING, List, Any, Tuple
from geoalchemy2 import Geometry
from sqlalchemy import Enum as SAEnum
from shapely import wkb
from shapely.geometry import Point

from .base import BaseDB
from app.utils.enums import Region, SiteType

if TYPE_CHECKING:
    from .task import Task
    from .access_request import AccessRequest
    from .incident import Incident
    from .routine_inspection import RoutineInspection
    from .generator import Generator


class BaseSite(SQLModel):
    name: str = Field(description="Site name", nullable=False, max_length=100)
    region: Region = Field(description="Geographic region", nullable=False)
    site_type: SiteType = Field(
        default=SiteType.TASK_SITE,
        sa_column=Column(
            SAEnum(
                SiteType,
                values_callable=lambda enum: [item.value for item in enum],
                native_enum=False,
            ),
            nullable=False,
            server_default=SiteType.TASK_SITE.value,
        ),
    )
    address: str | None = Field(
        default=None, max_length=500, description="Physical street address"
    )


class Site(BaseDB, BaseSite, table=True):
    __tablename__ = "sites"  # type: ignore

    model_config = {"arbitrary_types_allowed": True}

    # PostGIS location field - stores lat/long as POINT geometry
    # Using Any type hint to avoid Pydantic schema issues with WKBElement
    location: Any = Field(
        default=None, sa_column=Column(Geometry(geometry_type="POINT", srid=4326))
    )

    # Geofence radius in meters for access verification
    geofence_radius: int = Field(
        default=100, description="Geofence radius in meters for access verification"
    )

    tasks: List["Task"] = Relationship(back_populates="site")
    access_requests: List["AccessRequest"] = Relationship(back_populates="site")
    incidents: List["Incident"] = Relationship(back_populates="site")
    routine_inspections: List["RoutineInspection"] = Relationship(back_populates="site")
    generators: List["Generator"] = Relationship(back_populates="site")

    def set_location(self, latitude: float, longitude: float) -> None:
        """Set location from latitude and longitude."""
        from geoalchemy2.shape import from_shape

        point = Point(longitude, latitude)  # PostGIS uses (lon, lat) order
        self.location = from_shape(point, srid=4326)
        self.touch()

    def get_coordinates(self) -> Tuple[float, float] | None:
        """Get (latitude, longitude) tuple from location."""
        if self.location is None:
            return None
        try:
            point = wkb.loads(bytes(self.location.data))
            return (point.y, point.x)  # Return as (lat, lon)
        except Exception:
            return None


class SiteCreate(BaseSite):
    latitude: float | None = Field(
        default=None, ge=-90, le=90, description="Latitude coordinate"
    )
    longitude: float | None = Field(
        default=None, ge=-180, le=180, description="Longitude coordinate"
    )
    geofence_radius: int = Field(
        default=100, ge=10, le=5000, description="Geofence radius in meters"
    )


class SiteUpdate(SQLModel):
    name: str | None = Field(default=None, description="Site name", max_length=100)
    region: Region | None = Field(default=None, description="Geographic region")
    site_type: SiteType | None = Field(default=None)
    address: str | None = Field(
        default=None, max_length=500, description="Physical street address"
    )
    latitude: float | None = Field(
        default=None, ge=-90, le=90, description="Latitude coordinate"
    )
    longitude: float | None = Field(
        default=None, ge=-180, le=180, description="Longitude coordinate"
    )
    geofence_radius: int | None = Field(
        default=None, ge=10, le=5000, description="Geofence radius in meters"
    )


class SiteResponse(BaseDB, BaseSite):
    latitude: float | None = Field(default=None, description="Latitude coordinate")
    longitude: float | None = Field(default=None, description="Longitude coordinate")
    geofence_radius: int = Field(default=100, description="Geofence radius in meters")
    num_tasks: int = Field(default=0, description="Number of tasks", ge=0)
    num_incidents: int = Field(default=0, description="Number of incidents", ge=0)
    num_reports: int = Field(default=0, description="Number of reports", ge=0)


class SiteSearchResult(SiteResponse):
    match_score: float = Field(
        default=0,
        ge=0,
        description="Fuzzy match score returned by PostgreSQL trigram similarity",
    )
