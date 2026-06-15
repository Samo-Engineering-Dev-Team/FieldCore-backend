from typing import Optional

from sqlmodel import Field

from .base import BaseDB


class Webhook(BaseDB, table=True):
    """Outbound webhook registration.

    Aligned with the rest of the schema via BaseDB (UUID PK, tz-aware
    created_at/updated_at, soft-delete deleted_at).
    """

    __tablename__ = "webhooks"  # type: ignore

    url: str = Field(description="Webhook URL to send notifications to")
    event_type: str = Field(
        description="Type of event to trigger webhook (e.g., 'sla_breach', 'incident_created')"
    )
    secret: Optional[str] = Field(
        default=None, description="Optional secret for webhook verification"
    )
    is_active: bool = Field(default=True, description="Whether the webhook is active")
