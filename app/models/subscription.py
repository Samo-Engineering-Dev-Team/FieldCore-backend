from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, Column, DateTime, Index, JSON, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.utils.funcs import utcnow

from .base import BaseDB


JSON_VARIANT = JSON().with_variant(JSONB, "postgresql")


class TenantSubscriptionState(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    OVERDUE = "overdue"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


SUBSCRIPTION_STATE_TRANSITIONS: dict[
    TenantSubscriptionState,
    frozenset[TenantSubscriptionState],
] = {
    TenantSubscriptionState.TRIAL: frozenset(
        {
            TenantSubscriptionState.ACTIVE,
            TenantSubscriptionState.OVERDUE,
            TenantSubscriptionState.SUSPENDED,
            TenantSubscriptionState.CANCELLED,
        }
    ),
    TenantSubscriptionState.ACTIVE: frozenset(
        {
            TenantSubscriptionState.OVERDUE,
            TenantSubscriptionState.SUSPENDED,
            TenantSubscriptionState.CANCELLED,
        }
    ),
    TenantSubscriptionState.OVERDUE: frozenset(
        {
            TenantSubscriptionState.ACTIVE,
            TenantSubscriptionState.SUSPENDED,
            TenantSubscriptionState.CANCELLED,
        }
    ),
    TenantSubscriptionState.SUSPENDED: frozenset(
        {
            TenantSubscriptionState.ACTIVE,
            TenantSubscriptionState.CANCELLED,
        }
    ),
    TenantSubscriptionState.CANCELLED: frozenset(),
}


class TenantSubscriptionBase(SQLModel):
    tenant_id: str = Field(
        foreign_key="tenants.id",
        max_length=128,
        nullable=False,
        index=True,
    )
    state: TenantSubscriptionState = Field(
        default=TenantSubscriptionState.TRIAL,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    billing_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON_VARIANT, nullable=False),
    )


class TenantSubscription(BaseDB, TenantSubscriptionBase, table=True):
    __tablename__ = "tenant_subscriptions"  # type: ignore
    __table_args__ = (
        CheckConstraint(
            "state IN ('trial', 'active', 'overdue', 'suspended', 'cancelled')",
            name="ck_tenant_subscriptions_state",
        ),
        UniqueConstraint("tenant_id", name="uq_tenant_subscriptions_tenant_id"),
        Index("ix_tenant_subscriptions_tenant_state", "tenant_id", "state"),
    )

    def can_transition_to(self, target_state: TenantSubscriptionState | str) -> bool:
        target = TenantSubscriptionState(target_state)
        current = TenantSubscriptionState(self.state)
        return target == current or target in SUBSCRIPTION_STATE_TRANSITIONS[current]

    def transition_to(self, target_state: TenantSubscriptionState | str) -> bool:
        target = TenantSubscriptionState(target_state)
        current = TenantSubscriptionState(self.state)
        if target == current:
            self.touch()
            return False
        if target not in SUBSCRIPTION_STATE_TRANSITIONS[current]:
            raise ValueError(f"Cannot transition tenant subscription from {current} to {target}")
        self.state = target
        self.touch()
        return True


class TenantSubscriptionCreate(SQLModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    state: TenantSubscriptionState = TenantSubscriptionState.TRIAL
    billing_metadata: dict[str, Any] = Field(default_factory=dict)


class TenantSubscriptionResponse(TenantSubscriptionBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class BillingWebhookIngestResponse(SQLModel):
    provider: str
    event_type: str
    tenant_id: str
    previous_state: TenantSubscriptionState | None = None
    new_state: TenantSubscriptionState
    processed: bool = True
    subscription: TenantSubscriptionResponse
    message: str
