from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime


class Webhook(SQLModel, table=True):
    __tablename__ = "webhooks"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(description="Webhook URL to send notifications to")
    event_type: str = Field(
        description="Type of event to trigger webhook (e.g., 'sla_breach', 'incident_created')"
    )
    secret: Optional[str] = Field(
        default=None, description="Optional secret for webhook verification"
    )
    is_active: bool = Field(default=True, description="Whether the webhook is active")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
