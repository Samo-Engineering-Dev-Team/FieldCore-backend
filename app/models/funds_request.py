"""
The funds ledger — one row per request, whatever its type.

Single table with a `type` discriminator rather than one table per request type,
resolving spec §7 Q5. Trip, refuel and misc requests share an identical
lifecycle, an identical approval chain and a single reconciliation rule; the
only real difference is which input fields are populated.

Money is `Numeric`, never `float`. Nothing else in this codebase uses Decimal
yet (legacy diesel JSON stores `amount_used` as a float) — these are the first
exact-money columns, and the two must never be accumulated together without an
explicit conversion. Response shapes expose `float` for display; storage and
arithmetic stay in Decimal.
"""

from abc import ABC
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID

from sqlalchemy import Numeric, text
from sqlmodel import Column, DateTime, Field, Index, Relationship, SQLModel

from app.utils.enums import FundsPriority, FundsRequestStatus, FundsRequestType
from app.utils.funcs import utcnow

if TYPE_CHECKING:
    from .disbursement import Disbursement
    from .generator import Generator
    from .site import Site
    from .technician import Technician

from .base import BaseDB


class InvalidFundsTransition(ValueError):
    """
    Raised when a caller attempts a status change the chain does not allow.

    A plain ValueError subclass rather than an HTTPException so the state
    machine stays testable without a DB or a request context — the API layer
    translates this into a 409.
    """


class BaseFundsRequest(SQLModel, ABC):
    technician_id: UUID = Field(
        foreign_key="technicians.id", description="Requesting technician"
    )
    type: FundsRequestType = Field(description="Category of funded expense")

    # ── Weekly trip inputs (spec §3.1) ────────────────────────────────────
    # Captured for audit, not recomputed as a gate. The technician calculates
    # the figure externally (AA trip calculator); we store every input beside
    # the submitted amount so an approver can see a variance, which is a
    # stronger control than a server-side rejection built on our guess at
    # their formula.
    distance_km: float | None = Field(
        default=None, ge=0, description="Trip distance in km"
    )
    vehicle_efficiency_l_per_100km: float | None = Field(
        default=None,
        gt=0,
        description="Vehicle fuel consumption in litres per 100 km (SA convention)",
    )

    # ── Generator refuel inputs (spec §3.2) ───────────────────────────────
    site_id: UUID | None = Field(
        default=None, foreign_key="sites.id", description="Site being refuelled"
    )
    generator_id: UUID | None = Field(
        default=None,
        foreign_key="generators.id",
        description="Specific generator unit — required for SECOM invoicing "
        "traceability (spec §3.2.6)",
    )
    requested_liters: float | None = Field(
        default=None, gt=0, description="Litres of diesel requested"
    )
    gen_runtime_hours: float | None = Field(
        default=None,
        ge=0,
        description="Hours the generator has been running, as reported on site",
    )

    # ── Misc (spec §3.3) ──────────────────────────────────────────────────
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="What the funds are for. Required for MISC requests.",
    )


class FundsRequest(BaseDB, BaseFundsRequest, table=True):
    __tablename__ = "funds_requests"  # type: ignore

    __table_args__ = (
        Index(
            "ix_funds_requests_technician_period",
            "technician_id",
            "period_start",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_funds_requests_status",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_funds_requests_type_status",
            "type",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_funds_requests_site",
            "site_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    # ── The chain (spec §2) ───────────────────────────────────────────────
    # Three fixed stages, in order. Only *who* holds a stage is configurable
    # (see funds_capabilities); the shape itself is not.
    ALLOWED_TRANSITIONS: ClassVar[dict[FundsRequestStatus, tuple[FundsRequestStatus, ...]]] = {
        FundsRequestStatus.PENDING: (
            FundsRequestStatus.APPROVED,
            FundsRequestStatus.REJECTED,
            FundsRequestStatus.CANCELLED,
        ),
        FundsRequestStatus.APPROVED: (
            FundsRequestStatus.LOADED,
            FundsRequestStatus.REJECTED,
        ),
        FundsRequestStatus.LOADED: (
            FundsRequestStatus.RELEASED,
            FundsRequestStatus.REJECTED,
        ),
        # Terminal. Once money has reached the technician the record is closed
        # to status changes — the correction path is a reconciliation, not a
        # rewrite of how the funds were released.
        FundsRequestStatus.RELEASED: (),
        FundsRequestStatus.REJECTED: (),
        FundsRequestStatus.CANCELLED: (),
    }

    status: FundsRequestStatus = Field(
        default=FundsRequestStatus.PENDING, description="Position in the release chain"
    )
    priority: FundsPriority = Field(
        default=FundsPriority.NORMAL,
        description="Always HIGH for GENERATOR_REFUEL, forced server-side (spec §2)",
    )

    requested_amount: Decimal = Field(
        sa_column=Column(Numeric(12, 2), nullable=False),
        description="Rand amount requested, as submitted by the technician",
    )
    diesel_price_per_liter: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(10, 4), nullable=True),
        description="Diesel price in Rand per litre, ALWAYS as manually entered on "
        "this request and stored here for audit (spec §3.1 rule, §4). Never "
        "derived from a lookup table, a location or a date — stations and regions "
        "differ materially on the same day, and a stale flat rate has previously "
        "caused disputes over suspected fund misuse. 4 decimal places because "
        "pump prices are quoted to the tenth of a cent.",
    )

    # Snapshotted at creation from app.utils.funcs.funds_period so a request
    # keeps reporting against the period it was raised in, even if Finance
    # later views a different window.
    period_start: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore
        description="Friday 00:00 SAST of the period this request belongs to",
    )
    period_end: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore
        description="Thursday 23:59:59.999999 SAST of that period (inclusive)",
    )

    submitted_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    rejected_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    rejected_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")
    rejection_reason: str | None = Field(default=None, max_length=1000)
    cancelled_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    technician: "Technician" = Relationship(back_populates="funds_requests")
    site: "Site" = Relationship()
    generator: "Generator" = Relationship()
    disbursement: "Disbursement" = Relationship(
        back_populates="funds_request", sa_relationship_kwargs={"uselist": False}
    )

    # ── Behaviour ─────────────────────────────────────────────────────────
    # Pure status mutation only, mirroring AccessRequest.approve/reject. The
    # capability check, the Disbursement write and the notification fan-out
    # belong to app/services/funds_request.py. Keeping this pure is what lets
    # the chain be tested without a live DB, which matters because the
    # db_session fixture in tests/conftest.py currently skips every test that
    # touches one.

    def can_transition_to(self, target: FundsRequestStatus) -> bool:
        return target in self.ALLOWED_TRANSITIONS.get(self.status, ())

    def transition_to(self, target: FundsRequestStatus) -> None:
        if not self.can_transition_to(target):
            allowed = ", ".join(s.value for s in self.ALLOWED_TRANSITIONS.get(self.status, ())) or "none"
            raise InvalidFundsTransition(
                f"Cannot move a {self.status.value} request to {target.value}. "
                f"Allowed from {self.status.value}: {allowed}."
            )
        self.status = target
        self.touch()

    def mark_rejected(self, by_user_id: UUID, reason: str) -> None:
        self.transition_to(FundsRequestStatus.REJECTED)
        self.rejected_at = utcnow()
        self.rejected_by_user_id = by_user_id
        self.rejection_reason = reason

    def mark_cancelled(self) -> None:
        self.transition_to(FundsRequestStatus.CANCELLED)
        self.cancelled_at = utcnow()

    @property
    def is_high_priority(self) -> bool:
        return self.priority is FundsPriority.HIGH

    @property
    def expected_amount(self) -> Decimal | None:
        """
        Rand the trip inputs imply: distance ÷ 100 × litres-per-100km × price.

        None for any request that is not a fully-specified weekly trip. Shown to
        the approver next to `requested_amount` as a variance, never enforced —
        the external calculator is the source of truth (see BaseFundsRequest).
        """
        if self.type is not FundsRequestType.WEEKLY_TRIP:
            return None
        if (
            self.distance_km is None
            or self.vehicle_efficiency_l_per_100km is None
            or self.diesel_price_per_liter is None
        ):
            return None
        litres = Decimal(str(self.distance_km)) / Decimal(100) * Decimal(
            str(self.vehicle_efficiency_l_per_100km)
        )
        return (litres * self.diesel_price_per_liter).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @property
    def amount_variance(self) -> Decimal | None:
        """Requested minus expected. Positive means the technician asked for more
        than their stated inputs imply."""
        expected = self.expected_amount
        if expected is None:
            return None
        return self.requested_amount - expected


class FundsRequestCreate(BaseFundsRequest):
    """
    `technician_id` is optional on the wire — a technician submitting for
    themselves has it resolved from their token, mirroring AccessRequestCreate.
    `priority`, `status` and the period bounds are all set server-side and are
    deliberately absent here.
    """

    technician_id: UUID | None = Field(default=None)
    requested_amount: Decimal = Field(
        gt=0, max_digits=12, decimal_places=2, description="Rand amount requested"
    )
    diesel_price_per_liter: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=10,
        decimal_places=4,
        description="Manually entered Rand per litre. Required for WEEKLY_TRIP.",
    )


class FundsRequestUpdate(SQLModel):
    """Only editable while PENDING; the service enforces that."""

    requested_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    diesel_price_per_liter: Decimal | None = Field(
        default=None, gt=0, max_digits=10, decimal_places=4
    )
    distance_km: float | None = Field(default=None, ge=0)
    vehicle_efficiency_l_per_100km: float | None = Field(default=None, gt=0)
    requested_liters: float | None = Field(default=None, gt=0)
    gen_runtime_hours: float | None = Field(default=None, ge=0)
    site_id: UUID | None = Field(default=None)
    generator_id: UUID | None = Field(default=None)
    description: str | None = Field(default=None, max_length=2000)


class FundsRequestRejection(SQLModel):
    reason: str = Field(min_length=3, max_length=1000)


class FundsRequestResponse(BaseDB, BaseFundsRequest):
    """Money is exposed as float for display. Storage and arithmetic stay Decimal."""

    status: FundsRequestStatus
    priority: FundsPriority
    requested_amount: float = 0.0
    diesel_price_per_liter: float | None = None
    expected_amount: float | None = Field(
        default=None, description="Computed from the trip inputs; advisory only"
    )
    amount_variance: float | None = Field(
        default=None, description="requested_amount - expected_amount"
    )
    period_start: datetime | None = None
    period_end: datetime | None = None
    submitted_at: datetime | None = None
    rejection_reason: str | None = None

    # Denormalised for grids, so the frontend needs no second round trip.
    technician_name: str = Field(default="")
    technician_region: str | None = Field(default=None)
    site_name: str | None = Field(default=None)
    generator_display_name: str | None = Field(default=None)
    # Exposed so a client can open a reconciliation against this request without
    # a second lookup. Null until an approver has acted, since the disbursement is
    # created at approval.
    disbursement_id: UUID | None = Field(default=None)
    amount_issued: float | None = Field(
        default=None, description="From the linked disbursement, once one exists"
    )
    reconciliation_id: UUID | None = Field(default=None)
    reconciliation_status: str | None = Field(default=None)
