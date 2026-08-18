"""
Who holds authority over each stage of the funds release chain.

The spec names individuals against each stage (approve / load / release /
finance lead). Those names are illustrative only — this table is why nothing is
hardcoded. A stage's holder is a row, reassignable when staff change without a
deploy or a migration.

Deliberately NOT modelled as UserRole values (decision 1 of
FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md): the JWT carries a single role, and
the spec's own approval chain has one person holding two capabilities at once
(fallback approver *and* releaser), which a single-valued enum cannot express.
UserRole.FINANCE grants sight of the Finance Dashboard; a FundsCapability row
grants the power to move a request forward. The two are separate on purpose.
"""

from abc import ABC
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Field, Index, Relationship, SQLModel

from app.utils.enums import FundsCapability, UserRole

from .base import BaseDB

if TYPE_CHECKING:
    from .user import User


class BaseFundsCapability(SQLModel, ABC):
    user_id: UUID = Field(foreign_key="users.id", description="Holder of the capability")
    capability: FundsCapability = Field(description="Stage this user may act on")
    is_fallback: bool = Field(
        default=False,
        description="True for a stand-in who acts only when the primary holder is "
        "unreachable (spec §2). Enforcement is not technical — a fallback may act "
        "at any time — but every use is recorded on "
        "Disbursement.is_fallback_approval so Finance can see it happened.",
    )


class FundsCapabilityAssignment(BaseDB, BaseFundsCapability, table=True):
    """Named 'Assignment' so it does not collide with the FundsCapability enum."""

    __tablename__ = "funds_capabilities"  # type: ignore

    __table_args__ = (
        Index(
            "uq_funds_capabilities_user_capability",
            "user_id",
            "capability",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_funds_capabilities_capability",
            "capability",
            postgresql_where=text("deleted_at IS NULL AND is_active"),
        ),
    )

    is_active: bool = Field(
        default=True,
        description="Revoke by clearing this rather than deleting, so historical "
        "approvals stay explainable.",
    )

    user: "User" = Relationship()


class FundsCapabilityAssignmentCreate(BaseFundsCapability):
    is_active: bool = Field(default=True)


class FundsCapabilityAssignmentUpdate(SQLModel):
    is_fallback: bool | None = Field(default=None)
    is_active: bool | None = Field(default=None)


class FundsCapabilityAssignmentResponse(BaseDB, BaseFundsCapability):
    is_active: bool = Field(default=True)
    user_name: str = Field(default="", description="Holder's full name")
    user_email: str = Field(default="")
    user_role: UserRole | None = Field(default=None)
