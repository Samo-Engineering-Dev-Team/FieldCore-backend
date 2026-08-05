from uuid import UUID
from pydantic import StringConstraints
from typing import Annotated, TYPE_CHECKING, List, Any, Tuple
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship, Column, DateTime
from geoalchemy2 import Geometry
from shapely import wkb
from shapely.geometry import Point
from abc import ABC

from .base import BaseDB
from app.utils.funcs import utcnow

# ID number constraint - supports various formats (SA ID, passport, work permit, etc.)
ID_NUMBER = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=5, max_length=20)
]

if TYPE_CHECKING:
    from .user import User
    from .task import Task
    from .access_request import AccessRequest
    from .report import Report
    from .incident import Incident
    from .routine_inspection import RoutineInspection
    from .sheq_submission import SheqSubmission


class BaseTechnician(SQLModel, ABC):
    phone: str = Field(
        nullable=False,
        max_length=13,
        min_length=10,
        description="Phone number",
    )
    id_no: str = Field(nullable=False, description="ID/Passport number")
    user_id: UUID = Field(nullable=False, foreign_key="users.id")


class Technician(BaseDB, BaseTechnician, table=True):
    __tablename__ = "technicians"  # type: ignore

    model_config = {"arbitrary_types_allowed": True}

    # PostGIS location fields for real-time tracking
    current_location: Any = Field(
        default=None, sa_column=Column(Geometry(geometry_type="POINT", srid=4326))
    )
    last_location_update: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        description="When the technician's location was last updated",
    )

    # Home base for route optimization
    home_base: Any = Field(
        default=None, sa_column=Column(Geometry(geometry_type="POINT", srid=4326))
    )

    # Availability status
    is_available: bool = Field(
        default=True, description="Whether technician is available for dispatch"
    )

    user: "User" = Relationship()
    tasks: List["Task"] = Relationship(back_populates="technician")
    access_requests: List["AccessRequest"] = Relationship(back_populates="technician")
    reports: List["Report"] = Relationship(back_populates="technician")
    incidents: List["Incident"] = Relationship(back_populates="technician")
    routine_inspections: List["RoutineInspection"] = Relationship(
        back_populates="technician"
    )
    sheq_submissions: List["SheqSubmission"] = Relationship(back_populates="technician")

    def update_location(self, latitude: float, longitude: float) -> None:
        """Update current location from mobile app."""
        from geoalchemy2.shape import from_shape

        point = Point(longitude, latitude)
        self.current_location = from_shape(point, srid=4326)
        self.last_location_update = utcnow()
        self.touch()

    def set_home_base(self, latitude: float, longitude: float) -> None:
        """Set home base location."""
        from geoalchemy2.shape import from_shape

        point = Point(longitude, latitude)
        self.home_base = from_shape(point, srid=4326)
        self.touch()

    def get_current_coordinates(self) -> Tuple[float, float] | None:
        """Get current (latitude, longitude) tuple."""
        if self.current_location is None:
            return None
        try:
            point = wkb.loads(bytes(self.current_location.data))
            return (point.y, point.x)
        except Exception:
            return None

    def get_home_base_coordinates(self) -> Tuple[float, float] | None:
        """Get home base (latitude, longitude) tuple."""
        if self.home_base is None:
            return None
        try:
            point = wkb.loads(bytes(self.home_base.data))
            return (point.y, point.x)
        except Exception:
            return None


class TechnicianCreate(BaseTechnician):
    home_latitude: float | None = Field(
        default=None, ge=-90, le=90, description="Home base latitude"
    )
    home_longitude: float | None = Field(
        default=None, ge=-180, le=180, description="Home base longitude"
    )


class TechnicianUpdate(SQLModel):
    phone: str | None = Field(
        default=None, max_length=13, min_length=10, description="Phone number"
    )
    is_available: bool | None = Field(default=None, description="Availability status")
    home_latitude: float | None = Field(
        default=None, ge=-90, le=90, description="Home base latitude"
    )
    home_longitude: float | None = Field(
        default=None, ge=-180, le=180, description="Home base longitude"
    )


class TechnicianLocationUpdate(SQLModel):
    """Schema for updating technician's current location from mobile app."""

    latitude: float = Field(ge=-90, le=90, description="Current latitude")
    longitude: float = Field(ge=-180, le=180, description="Current longitude")


class TechnicianResponse(BaseDB, BaseTechnician):
    fullname: str = Field(default="", description="Full name")
    is_available: bool = Field(default=True, description="Availability status")
    current_latitude: float | None = Field(default=None, description="Current latitude")
    current_longitude: float | None = Field(
        default=None, description="Current longitude"
    )
    last_location_update: datetime | None = Field(
        default=None, description="Last location update time"
    )
    home_latitude: float | None = Field(default=None, description="Home base latitude")
    home_longitude: float | None = Field(
        default=None, description="Home base longitude"
    )
    distance_km: float | None = Field(
        default=None, description="Distance to target in km (for dispatch queries)"
    )


class TechnicianDataIssue(SQLModel):
    reason: str
    user_id: UUID | None = None
    technician_id: UUID | None = None
    name: str = ""
    surname: str = ""
    email: str = ""
    status: str | None = None


class TechnicianDataIssuesResponse(SQLModel):
    technician_users_without_profiles: List[TechnicianDataIssue] = Field(
        default_factory=list
    )
    profiles_without_active_technician_users: List[TechnicianDataIssue] = Field(
        default_factory=list
    )
    total: int = 0
