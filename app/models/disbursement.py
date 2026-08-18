"""
The money facts for one funds request: what was issued, and who moved it.

Spec §6: "Only [the loader] and [the releasers] can move money — approval alone
never releases funds." That rule is the reason this record exists separately
from FundsRequest. The request tracks intent and position in the chain; this
tracks the three distinct human acts, each with its own identity and timestamp,
so a disbursement can be audited end to end without replaying a status log.

Created when a request is approved, with `amount_issued` seeded from
`requested_amount`. The loader may adjust it at load time to the amount actually
loaded — spec §3.4 has the loader reporting weekly on amount issued, so the
issued figure has to be theirs to state, not an echo of the request.
"""

from abc import ABC
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Numeric, text
from sqlmodel import Column, DateTime, Field, Index, Relationship, SQLModel

from .base import BaseDB

if TYPE_CHECKING:
    from .funds_request import FundsRequest
    from .reconciliation import Reconciliation


class BaseDisbursement(SQLModel, ABC):
    funds_request_id: UUID = Field(
        foreign_key="funds_requests.id", description="The request this disburses"
    )


class Disbursement(BaseDB, BaseDisbursement, table=True):
    __tablename__ = "disbursements"  # type: ignore

    __table_args__ = (
        # One disbursement per request (spec §4). Partial so a soft-deleted
        # record cannot permanently block re-disbursing a corrected request.
        Index(
            "uq_disbursements_funds_request",
            "funds_request_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_disbursements_released_at",
            "released_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    amount_issued: Decimal = Field(
        sa_column=Column(Numeric(12, 2), nullable=False),
        description="Rand actually issued. Seeded from the request's requested_amount "
        "at approval; the loader may correct it to what was really loaded.",
    )

    # ── Stage 1: approve ──────────────────────────────────────────────────
    approved_by_user_id: UUID = Field(foreign_key="users.id")
    approved_at: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    is_fallback_approval: bool = Field(
        default=False,
        description="True when approved by a stand-in rather than the primary "
        "holder (spec §2 escalation path). Recorded because the spec treats "
        "fallback approval as an exception worth seeing, not a silent equivalent.",
    )

    # ── Stage 2: load ─────────────────────────────────────────────────────
    loaded_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")
    loaded_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    # ── Stage 3: release ──────────────────────────────────────────────────
    released_by_user_id: UUID | None = Field(default=None, foreign_key="users.id")
    released_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    funds_request: "FundsRequest" = Relationship(back_populates="disbursement")
    reconciliation: "Reconciliation" = Relationship(
        back_populates="disbursement", sa_relationship_kwargs={"uselist": False}
    )

    @property
    def is_released(self) -> bool:
        return self.released_at is not None


class DisbursementLoad(SQLModel):
    """Payload for the load stage — the loader states what was actually loaded."""

    amount_issued: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="Omit to accept the approved amount unchanged.",
    )


class DisbursementResponse(BaseDB, BaseDisbursement):
    amount_issued: float = 0.0
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    is_fallback_approval: bool = False
    loaded_by_user_id: UUID | None = None
    loaded_at: datetime | None = None
    released_by_user_id: UUID | None = None
    released_at: datetime | None = None

    approved_by_name: str = Field(default="")
    loaded_by_name: str | None = Field(default=None)
    released_by_name: str | None = Field(default=None)
