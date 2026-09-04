"""
Generators as a first-class asset, one row per physical unit.

This table began life as a finance-only registry (spec §4 of
FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md): just `site_id` + `gen_no` + `label`,
enough for a refuel funds request to name the unit being filled. It is now the
asset register — name, model, serial, run hours and service history — and the
generator data-grid is the only place units are created.

The table name and `funds_requests.generator_id` are deliberately unchanged
(decision D1 of docs/GENERATOR_IMPROVEMENT_PLAN.md): dropping and recreating
would strip unit attribution from every refuel already recorded.

Two fields carry history rather than intent:

`site_id` is now nullable — a unit can sit in a yard unassigned. A refuel still
requires an assigned unit; that rule lives in the funds-request service, not
here, because it is a refuel rule and not a property of the asset.

`legacy_gen_no` is the old `gen_no`, kept non-user-facing. Historical diesel
report JSON identifies a unit only by that free-text number and is never
rewritten (additive-only constraint), so it is the only way to resolve a legacy
fill to a unit — for both the Finance Dashboard breakdown and this unit's refuel
history. New units created through the grid leave it NULL and simply have no
legacy fills. Removable once diesel payloads are retired or backfilled.
"""

from abc import ABC
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Field, Index, Relationship, SQLModel

from app.utils.funcs import format_hour_meter

from .base import BaseDB

if TYPE_CHECKING:
    from .site import Site


class BaseGenerator(SQLModel, ABC):
    name: str = Field(
        max_length=100,
        description="Unit name, e.g. 'Gen 1' or 'East yard Cummins'",
    )
    model: str | None = Field(
        default=None, max_length=100, description="Manufacturer model, e.g. 'Cummins C60D5'"
    )
    serial_no: str | None = Field(
        default=None,
        max_length=100,
        description="Plate serial. Optional — a unit may be registered before "
        "anyone has walked the site to read it — but unique among live rows "
        "when present, so report matching stays unambiguous.",
    )
    current_run_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Latest hour-meter reading, in seconds. Captured as HHMM:SS "
        "and stored as seconds so readings subtract exactly.",
    )
    last_service_date: date | None = Field(default=None)
    run_seconds_at_service: int | None = Field(
        default=None,
        ge=0,
        description="Hour-meter reading at the last service, in seconds.",
    )
    site_id: UUID | None = Field(
        default=None,
        foreign_key="sites.id",
        description="Site this unit is assigned to. Null when unassigned.",
    )


class Generator(BaseDB, BaseGenerator, table=True):
    __tablename__ = "generators"  # type: ignore

    __table_args__ = (
        # Partial unique: a soft-deleted unit must not block re-registering the
        # same serial. BaseDB.soft_delete only sets deleted_at, so every
        # uniqueness rule here has to exclude deleted rows itself.
        Index(
            "uq_generators_serial_no",
            "serial_no",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND serial_no IS NOT NULL"),
        ),
        Index(
            "ix_generators_site_id",
            "site_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    is_active: bool = Field(
        default=True,
        description="False for a decommissioned unit. Kept rather than deleted "
        "so historical refuel records stay attributable.",
    )

    legacy_gen_no: int | None = Field(
        default=None,
        description="Pre-asset-register gen_no. Not user-facing, not editable — "
        "resolves legacy diesel report JSON only. See the module docstring.",
    )

    site: "Site" = Relationship(back_populates="generators")

    @property
    def seconds_since_last_service(self) -> int | None:
        """
        Run time accumulated since the last service, in seconds.

        Derived rather than stored: it cannot drift out of step with the two
        readings it comes from. None when either reading is missing, and never
        negative — a current reading below the service reading means the meter
        was replaced or mis-keyed, and a negative "hours since service" would
        read as a service that has not happened yet.
        """
        if self.current_run_seconds is None or self.run_seconds_at_service is None:
            return None
        return max(0, self.current_run_seconds - self.run_seconds_at_service)


class GeneratorCreate(BaseGenerator):
    is_active: bool = Field(default=True)


class GeneratorUpdate(SQLModel):
    name: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    serial_no: str | None = Field(default=None, max_length=100)
    current_run_seconds: int | None = Field(default=None, ge=0)
    last_service_date: date | None = Field(default=None)
    run_seconds_at_service: int | None = Field(default=None, ge=0)
    is_active: bool | None = Field(default=None)


class GeneratorAssignSite(SQLModel):
    """Assign or unassign in one operation — null unassigns."""

    site_id: UUID | None = Field(default=None)


class GeneratorResponse(BaseDB, BaseGenerator):
    is_active: bool = Field(default=True)
    seconds_since_last_service: int | None = Field(default=None)
    site_name: str = Field(default="", description="Denormalised for grid display")

    # Formatted alongside the raw seconds so no client re-implements HHMM:SS.
    current_run_display: str | None = Field(default=None)
    run_at_service_display: str | None = Field(default=None)
    since_service_display: str | None = Field(default=None)

    @classmethod
    def from_generator(cls, generator: Generator) -> "GeneratorResponse":
        since = generator.seconds_since_last_service
        return cls(
            id=generator.id,
            created_at=generator.created_at,
            updated_at=generator.updated_at,
            deleted_at=generator.deleted_at,
            name=generator.name,
            model=generator.model,
            serial_no=generator.serial_no,
            current_run_seconds=generator.current_run_seconds,
            last_service_date=generator.last_service_date,
            run_seconds_at_service=generator.run_seconds_at_service,
            site_id=generator.site_id,
            is_active=generator.is_active,
            seconds_since_last_service=since,
            site_name=generator.site.name if generator.site else "",
            current_run_display=format_hour_meter(generator.current_run_seconds),
            run_at_service_display=format_hour_meter(generator.run_seconds_at_service),
            since_service_display=format_hour_meter(since),
        )
