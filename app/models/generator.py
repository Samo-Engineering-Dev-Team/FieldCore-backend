"""
Generator units, one row per physical unit at a site.

Spec §4 requires refuel records to reference site + unit so Finance can trace
generator usage through to customer invoicing. Today the unit is free-text
`gen_no` buried in diesel report JSON (`app/models/report_data.py`), which is
untraceable and unnameable.

This table is **forward-only** (decision 2 of
FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md): new funds and reconciliation
records carry a real FK, while existing diesel report payloads are left exactly
as they are. Rewriting live report JSON would mutate production data, which the
additive-only migration constraint rules out. `scripts/seed_generators.py`
creates rows for the units already visible in historical reports so the
dashboard can resolve legacy fills by (site_id, gen_no) without touching them.
"""

from abc import ABC
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Field, Index, Relationship, SQLModel

from .base import BaseDB

if TYPE_CHECKING:
    from .site import Site


class BaseGenerator(SQLModel, ABC):
    site_id: UUID = Field(
        foreign_key="sites.id", description="Site this generator unit belongs to"
    )
    gen_no: int = Field(
        ge=1,
        description="Unit number at the site — 1 for 'Gen 1', 2 for 'Gen 2'. "
        "Matches the legacy gen_no in diesel report JSON so historical fills "
        "can be resolved without rewriting them.",
    )
    label: str | None = Field(
        default=None,
        max_length=100,
        description="Optional human label, e.g. 'Cummins 60kVA (east yard)'",
    )


class Generator(BaseDB, BaseGenerator, table=True):
    __tablename__ = "generators"  # type: ignore

    __table_args__ = (
        # Partial unique: a soft-deleted unit must not block re-creating the
        # same gen_no at that site. BaseDB.soft_delete only sets deleted_at,
        # so every uniqueness rule here has to exclude deleted rows itself.
        Index(
            "uq_generators_site_gen_no",
            "site_id",
            "gen_no",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    is_active: bool = Field(
        default=True,
        description="False for a decommissioned unit. Kept rather than deleted "
        "so historical refuel records stay attributable.",
    )

    site: "Site" = Relationship(back_populates="generators")

    @property
    def display_name(self) -> str:
        return self.label or f"Gen {self.gen_no}"


class GeneratorCreate(BaseGenerator):
    is_active: bool = Field(default=True)


class GeneratorUpdate(SQLModel):
    gen_no: int | None = Field(default=None, ge=1)
    label: str | None = Field(default=None, max_length=100)
    is_active: bool | None = Field(default=None)


class GeneratorResponse(BaseDB, BaseGenerator):
    is_active: bool = Field(default=True)
    display_name: str = Field(default="", description="label, or 'Gen {gen_no}'")
    site_name: str = Field(default="", description="Denormalised for grid display")
