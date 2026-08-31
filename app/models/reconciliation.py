"""
Financial proof that a disbursement was spent as intended.

Deliberately separate from the diesel/generator report (decision agreed against
spec §3.2.5): the report is *operational* proof — litres, generator runtime, site
photos — while a reconciliation is *financial* proof — itemised spend, slips, and
the resulting balance. They link by FK and are never merged, because Finance and
Operations sign off on different things and on different cadences.

One reconciliation per disbursement (spec §4), not one per technician per week.
That keeps a clean per-request balance, which is what makes "was this particular
refuel fully accounted for" answerable when a SECOM invoicing query arrives. The
dashboard's per-technician "Recon Received" is derived from the period's set of
disbursements rather than stored here.
"""

from abc import ABC
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List
from uuid import UUID

from sqlalchemy import Numeric, text
from sqlmodel import Column, DateTime, Field, Index, Relationship, SQLModel

from app.utils.enums import ExpenseCategory, ReconciliationStatus
from app.utils.funcs import utcnow

from .base import BaseDB

if TYPE_CHECKING:
    from .disbursement import Disbursement

_ZERO = Decimal("0.00")


class BaseReconciliationLine(SQLModel, ABC):
    category: ExpenseCategory = Field(description="Fuel, toll or miscellaneous")
    description: str | None = Field(default=None, max_length=500)
    incurred_on: date = Field(description="Date the expense was incurred")


class ReconciliationLine(BaseDB, BaseReconciliationLine, table=True):
    """One itemised expense, with its slip (spec §3.1.5–6)."""

    __tablename__ = "reconciliation_lines"  # type: ignore

    __table_args__ = (
        Index(
            "ix_reconciliation_lines_reconciliation",
            "reconciliation_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    reconciliation_id: UUID = Field(foreign_key="reconciliations.id")
    amount: Decimal = Field(
        sa_column=Column(Numeric(12, 2), nullable=False),
        description="Rand spent on this line",
    )
    slip_file_path: str | None = Field(
        default=None,
        max_length=500,
        description="Supabase Storage path for the slip image, via the existing "
        "signed-upload flow in app/api/v1/file.py. Nullable because a line may be "
        "entered before its photo finishes uploading on a poor connection; the "
        "service requires one on every line before a reconciliation may be submitted.",
    )

    reconciliation: "Reconciliation" = Relationship(back_populates="lines")


class BaseReconciliation(SQLModel, ABC):
    # Nullable: a reconciliation may stand alone, accounting for leftover funds a
    # technician still holds from an earlier disbursement rather than a specific
    # released request (e.g. R250 left over from a R700 trip, spent elsewhere).
    disbursement_id: UUID | None = Field(
        default=None,
        foreign_key="disbursements.id",
        description="The disbursement being accounted for, or null for a standalone recon",
    )


class Reconciliation(BaseDB, BaseReconciliation, table=True):
    __tablename__ = "reconciliations"  # type: ignore

    __table_args__ = (
        Index(
            "uq_reconciliations_disbursement",
            "disbursement_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_reconciliations_status",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_reconciliations_period",
            "period_start",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Scoped per technician, not global: two technicians can each have "FR-01".
        Index(
            "uq_reconciliations_technician_reference",
            "technician_id",
            "reference_no",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    status: ReconciliationStatus = Field(default=ReconciliationStatus.DRAFT)

    technician_id: UUID = Field(
        foreign_key="technicians.id",
        description="Set directly at creation for every recon (standalone or "
        "disbursement-linked) so ownership and the reference sequence don't "
        "depend on walking disbursement -> funds_request -> technician.",
    )
    reference_no: str = Field(
        max_length=20,
        description='Per-technician sequence, e.g. "FR-01", "FR-02" — human-friendly '
        "reference for Finance conversations, independent of the UUID.",
    )
    declared_amount: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(12, 2), nullable=True),
        description="Self-declared amount being accounted for on a standalone "
        "recon (no disbursement_id). Plays the role amount_issued plays for a "
        "disbursement-linked recon.",
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="What a standalone recon is accounting for, e.g. leftover "
        "cash from a prior disbursement spent on a different job.",
    )

    total_used: Decimal = Field(
        default=_ZERO,
        sa_column=Column(Numeric(12, 2), nullable=False, server_default="0"),
        description="Sum of the line amounts. Stored rather than computed on read so "
        "the dashboard can aggregate without walking every line.",
    )
    outstanding_balance: Decimal = Field(
        default=_ZERO,
        sa_column=Column(Numeric(12, 2), nullable=False, server_default="0"),
        description="amount_issued - total_used. Positive means the technician holds "
        "unspent funds; negative means they overspent and are owed.",
    )

    period_start: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore
        description="Copied from the funds request so a recon reports in the period "
        "the money was raised in",
    )
    period_end: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    submitted_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
        description="When the technician submitted. Compared against period_end to "
        "distinguish Outstanding from Overdue on the dashboard.",
    )
    finance_approved_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")
    finance_approved_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    rejection_reason: str | None = Field(default=None, max_length=1000)

    # Typed non-optional per SQLModel/SQLAlchemy convention elsewhere in this
    # codebase (the ORM attribute is genuinely None at runtime for a standalone
    # recon; a "X | None" string annotation breaks SQLAlchemy's string-based
    # relationship class resolution, which does a literal name lookup).
    disbursement: "Disbursement" = Relationship(back_populates="reconciliation")
    lines: List["ReconciliationLine"] = Relationship(
        back_populates="reconciliation",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    # ── Behaviour ─────────────────────────────────────────────────────────
    # Pure arithmetic and status mutation, no session access, so the balance
    # maths is testable without a DB.

    @property
    def reference_amount(self) -> Decimal:
        """The figure a recon is measured against: the disbursement's issued
        amount when linked to one, otherwise the technician's own declaration."""
        if self.disbursement_id is not None:
            return self.disbursement.amount_issued if self.disbursement else _ZERO
        return self.declared_amount if self.declared_amount is not None else _ZERO

    def recompute(self, amount_issued: Decimal) -> None:
        """
        Refresh the stored totals from the current lines. Call after any line
        add/edit/remove. Soft-deleted lines are excluded — BaseDB.soft_delete
        only sets deleted_at, so every read has to filter for itself.
        """
        self.total_used = sum(
            (line.amount for line in self.lines if line.deleted_at is None),
            start=_ZERO,
        )
        self.outstanding_balance = amount_issued - self.total_used
        self.touch()

    def mark_submitted(self) -> None:
        self.status = ReconciliationStatus.SUBMITTED
        self.submitted_at = utcnow()
        self.touch()

    def mark_approved(self, by_user_id: UUID) -> None:
        """Approval is what clears the technician for their next request (spec §3.1.7)."""
        self.status = ReconciliationStatus.APPROVED
        self.finance_approved_by_user_id = by_user_id
        self.finance_approved_at = utcnow()
        self.rejection_reason = None
        self.touch()

    def mark_rejected(self, by_user_id: UUID, reason: str) -> None:
        """Back to the technician. Stays REJECTED, not DRAFT, so the dashboard can
        tell an untouched recon from one that came back."""
        self.status = ReconciliationStatus.REJECTED
        self.finance_approved_by_user_id = by_user_id
        self.finance_approved_at = None
        self.rejection_reason = reason
        self.touch()

    @property
    def is_overdue(self) -> bool:
        """Past the period's Thursday deadline with nothing submitted (spec §3.1.6)."""
        if self.submitted_at is not None:
            return False
        return utcnow() > self.period_end

    @property
    def is_settled(self) -> bool:
        return self.status is ReconciliationStatus.APPROVED


# ── Wire shapes ───────────────────────────────────────────────────────────


class ReconciliationLineCreate(BaseReconciliationLine):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    slip_file_path: str | None = Field(default=None, max_length=500)


class ReconciliationLineUpdate(SQLModel):
    category: ExpenseCategory | None = Field(default=None)
    description: str | None = Field(default=None, max_length=500)
    incurred_on: date | None = Field(default=None)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    slip_file_path: str | None = Field(default=None, max_length=500)


class ReconciliationLineResponse(BaseDB, BaseReconciliationLine):
    reconciliation_id: UUID
    amount: float = 0.0
    slip_file_path: str | None = None


class ReconciliationCreate(BaseReconciliation):
    """Opens a DRAFT. Lines are added afterwards so a technician can capture slips
    incrementally over the week rather than in one sitting.

    Exactly one of `disbursement_id` or `declared_amount` must be supplied: the
    former is the normal "account for this released disbursement" flow, the
    latter opens a standalone recon for funds the technician holds that were
    never re-requested (e.g. leftover from an earlier trip, spent on something
    else). `technician_id` lets management open one on a technician's behalf,
    the same convention as FundsRequestCreate; a technician always reconciles
    their own.
    """

    technician_id: UUID | None = Field(default=None)
    declared_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    description: str | None = Field(default=None, max_length=500)
    lines: List[ReconciliationLineCreate] = Field(default_factory=list)


class ReconciliationRejection(SQLModel):
    reason: str = Field(min_length=3, max_length=1000)


class ReconciliationResponse(BaseDB, BaseReconciliation):
    status: ReconciliationStatus
    reference_no: str = ""
    total_used: float = 0.0
    outstanding_balance: float = 0.0
    amount_issued: float = Field(
        default=0.0,
        description="From the linked disbursement, or the technician's own "
        "declared_amount when this recon is standalone.",
    )
    description: str | None = Field(default=None)
    period_start: datetime | None = None
    period_end: datetime | None = None
    submitted_at: datetime | None = None
    finance_approved_at: datetime | None = None
    rejection_reason: str | None = None
    is_overdue: bool = False

    lines: List[ReconciliationLineResponse] = Field(default_factory=list)

    # Denormalised for the Finance review queue.
    technician_name: str = Field(default="")
    technician_region: str | None = Field(default=None)
    funds_request_id: UUID | None = Field(default=None)
    funds_request_type: str | None = Field(default=None)
    finance_approved_by_name: str | None = Field(default=None)
