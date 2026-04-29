from typing import Optional
from datetime import datetime
from sqlmodel import Field, DateTime
from uuid import UUID
from app.models.base import BaseDB


class UserSession(BaseDB, table=True):
    __tablename__ = "user_sessions"

    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    role: str = Field(nullable=False, max_length=32)
    session_id: str = Field(nullable=False, index=True)
    is_active: bool = Field(default=True, nullable=False)
    last_seen: Optional[datetime] = Field(default=None, sa_column=DateTime(timezone=True))
    expires_at: Optional[datetime] = Field(default=None, sa_column=DateTime(timezone=True))

    def to_public(self) -> dict:
        # Ensure last_seen is a datetime before formatting (handle legacy string values)
        last_seen_val = self.last_seen
        if isinstance(last_seen_val, str):
            try:
                from datetime import datetime as _dt

                last_seen_val = _dt.fromisoformat(last_seen_val)
            except Exception:
                last_seen_val = None

        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "role": self.role,
            "session_id": self.session_id,
            "is_active": self.is_active,
            "last_seen": last_seen_val.isoformat() if last_seen_val else None,
        }