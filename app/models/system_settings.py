from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from typing import Any
from pydantic import field_validator
import json

from app.utils.funcs import utcnow


class SystemSettingBase(SQLModel):
    """Base model for system settings."""
    key: str = Field(max_length=100, description="Unique setting key")
    value: Any = Field(sa_column=Column(JSONB), description="Setting value stored as JSON")
    description: str | None = Field(default=None, sa_column=Column(Text), description="Setting description")
    category: str = Field(default="general", max_length=50, description="Setting category")


class SystemSetting(SystemSettingBase, table=True):
    """Database model for system settings."""
    __tablename__ = "system_settings"  # type: ignore
    
    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique database identifier",
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        nullable=False,
        description="Date and time the setting was created",
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        nullable=False,
        description="Date and time the setting was last updated",
    )


class SystemSettingCreate(SQLModel):
    """Model for creating a new system setting."""
    key: str = Field(max_length=100)
    value: Any
    description: str | None = None
    category: str = "general"


class SystemSettingUpdate(SQLModel):
    """Model for updating a system setting."""
    value: Any


class SystemSettingResponse(SQLModel):
    """Response model for system settings."""
    id: UUID
    key: str
    value: Any
    description: str | None
    category: str
    created_at: datetime
    updated_at: datetime


class SystemSettingsBulkUpdate(SQLModel):
    """Model for bulk updating multiple settings."""
    settings: dict[str, Any] = Field(description="Dictionary of key-value pairs to update")


class SystemSettingsResponse(SQLModel):
    """Response model for all system settings grouped by category."""
    system: dict[str, Any] = Field(default_factory=dict)
    sla: dict[str, Any] = Field(default_factory=dict)
    location: dict[str, Any] = Field(default_factory=dict)
    notifications: dict[str, Any] = Field(default_factory=dict)
    debug: dict[str, Any] = Field(default_factory=dict)


class DebugConfig(SQLModel):
    """Configuration model specifically for debug settings."""
    debug_mode: bool = False
    log_level: str = "INFO"
    enable_request_logging: bool = False
    enable_sql_logging: bool = False
    enable_performance_headers: bool = False
