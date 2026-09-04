from uuid import UUID
from typing import Any, TYPE_CHECKING
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy.dialects.postgresql import JSONB

from app.utils.funcs import format_hour_meter

from .base import BaseDB

if TYPE_CHECKING:
    from .generator import Generator
    from .site import Site
    from .task import Task
    from .technician import Technician


class BaseRoutineInspection(SQLModel):
    """Base schema for routine generator inspections"""

    data: dict[str, Any] = Field(
        nullable=False, sa_type=JSONB, description="Structured inspection data"
    )
    attachments: dict[str, Any] | None = Field(
        default=None, sa_type=JSONB, description="Attachment metadata"
    )
    site_id: UUID = Field(foreign_key="sites.id")
    task_id: UUID = Field(foreign_key="tasks.id")
    technician_id: UUID = Field(foreign_key="technicians.id")
    status: str = Field(default="draft", description="draft or completed")

    # Which registered unit each section was filled in against. Nullable: every
    # inspection recorded before the asset register existed has none, and the
    # payload's free-text `serialNumber` stays as captured for those. Real
    # columns rather than a key inside `data` so "every inspection for this
    # unit" is a plain join instead of a JSONB dig.
    gen1_generator_id: UUID | None = Field(
        default=None, foreign_key="generators.id", description="Unit inspected in gen1"
    )
    gen2_generator_id: UUID | None = Field(
        default=None, foreign_key="generators.id", description="Unit inspected in gen2"
    )


class RoutineInspection(BaseDB, BaseRoutineInspection, table=True):
    """Database model for routine generator inspections"""

    __tablename__ = "routine_inspections"  # type: ignore

    site: "Site" = Relationship(back_populates="routine_inspections")
    task: "Task" = Relationship(back_populates="routine_inspections")
    technician: "Technician" = Relationship(back_populates="routine_inspections")

    # Two foreign keys into the same table, so the join condition has to be
    # spelled out — SQLAlchemy cannot pick between them on its own. Deliberately
    # one-directional: Generator does not back-populate an inspections list,
    # because a unit's inspections are read through a query, not an attribute.
    gen1_generator: "Generator" = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[RoutineInspection.gen1_generator_id]",
            "lazy": "selectin",
        }
    )
    gen2_generator: "Generator" = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[RoutineInspection.gen2_generator_id]",
            "lazy": "selectin",
        }
    )


class RoutineInspectionCreate(BaseRoutineInspection):
    """Create schema for routine inspections"""

    pass


class RoutineInspectionUpdate(SQLModel):
    """Update schema for routine inspections"""

    data: dict[str, Any] | None = Field(default=None)
    attachments: dict[str, Any] | None = Field(default=None)
    status: str | None = Field(default=None)
    gen1_generator_id: UUID | None = Field(default=None)
    gen2_generator_id: UUID | None = Field(default=None)


class InspectionGeneratorSummary(SQLModel):
    """The registered unit a section was filled in against, resolved for display.

    Denormalised onto the response so the report renders the unit's real
    identity without a second request, and so a pre-register inspection (no
    link) is visibly distinct from one that simply has no serial recorded.
    """

    id: UUID
    name: str
    model: str | None = None
    serial_no: str | None = None
    current_run_display: str | None = None

    @classmethod
    def from_generator(cls, generator: "Generator | None") -> "InspectionGeneratorSummary | None":
        if generator is None:
            return None
        return cls(
            id=generator.id,
            name=generator.name,
            model=generator.model,
            serial_no=generator.serial_no,
            current_run_display=format_hour_meter(generator.current_run_seconds),
        )


class RoutineInspectionResponse(BaseDB, BaseRoutineInspection):
    """Response schema for routine inspections"""

    site_name: str | None = Field(default=None)
    technician_fullname: str | None = Field(default=None)
    seacom_ref: str | None = Field(default=None)
    gen1_generator: InspectionGeneratorSummary | None = Field(default=None)
    gen2_generator: InspectionGeneratorSummary | None = Field(default=None)
