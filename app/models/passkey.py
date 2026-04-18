from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime
from sqlmodel import Field, Index

from app.utils.enums import PasskeyCeremonyType

from .base import BaseDB


class PasskeyCredential(BaseDB, table=True):
    """"""

    __tablename__ = "passkey_credentials"  # type: ignore
    __table_args__ = (
        Index(
            "uq_active_passkey_credentials_credential_id",
            "credential_id",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
    )

    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    name: str = Field(default="My passkey", max_length=100, nullable=False)
    credential_id: str = Field(nullable=False)
    public_key: str = Field(nullable=False)
    sign_count: int = Field(default=0, nullable=False)
    transports_json: str | None = Field(default=None)
    aaguid: str | None = Field(default=None, max_length=64)
    device_type: str | None = Field(default=None, max_length=32)
    backed_up: bool | None = Field(default=None)
    last_used_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


class PasskeyChallenge(BaseDB, table=True):
    """"""

    __tablename__ = "passkey_challenges"  # type: ignore

    flow: PasskeyCeremonyType = Field(nullable=False, index=True)
    user_id: UUID | None = Field(default=None, foreign_key="users.id", index=True)
    challenge: str = Field(nullable=False)
    rp_id: str = Field(nullable=False, max_length=255)
    origin: str = Field(nullable=False, max_length=255)
    expires_at: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        nullable=False,
    )
    consumed_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
