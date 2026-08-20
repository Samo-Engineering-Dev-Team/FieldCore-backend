import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from uuid import uuid4, UUID
from datetime import datetime

from app.utils.enums import UserRole

# Password policy (M6). No upper cap below 128 — Argon2 handles long inputs, and
# capping low blocks strong passphrases.
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


def validate_password_strength(value: str) -> str:
    """Require at least one letter and one digit."""
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("Password must contain at least one letter and one digit")
    return value


class Token(BaseModel):
    """"""

    access_token: str = Field(
        description="JWT access token",
        examples=[
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiYWRtaW4iOnRydWV9.dyt0CoTl4WoVjAHI9Q_CwSKhl6d_9rhM3NrXuJttkao"
        ],
    )
    token_type: str = Field(default="bearer", description="type of token")
    is_refresh: bool = Field(
        default=False, description="whether the access token is a refresh token or not"
    )


class TokenData(BaseModel):
    """
    Data model representing the decoded JWT token payload.

    This model contains all the essential information extracted from a JWT token,
    including user identification, role, expiration time, and token type.
    """

    user_id: UUID = Field(
        description="The unique identifier of the authenticated user",
        examples=[str(uuid4())],
    )
    role: UserRole = Field(
        description="The role of the authenticated user (e.g., admin, noc, technician)",
        examples=[UserRole.ADMIN],
    )
    name: str | None = Field(
        default=None,
        description="The first name of the authenticated user",
        examples=["John"],
    )
    surname: str | None = Field(
        default=None,
        description="The last name of the authenticated user",
        examples=["Doe"],
    )
    exp: datetime | None = Field(
        default=None,
        description="Expiration datetime of the token in UTC",
        examples=[datetime(2024, 12, 31, 23, 59, 59)],
    )
    token_type: str | None = Field(
        default=None,
        description="Type of token: 'access' for access tokens, 'refresh' for refresh tokens",
        examples=["access", "refresh"],
    )
    iat: datetime | None = Field(
        default=None,
        description="Issued at datetime of the token in UTC",
        examples=[datetime(2024, 1, 1, 0, 0, 0)],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "123e4567-e89b-12d3-a456-426614174000",
                "role": "user",
                "name": "John",
                "surname": "Doe",
                "exp": "2024-12-31T23:59:59",
                "token_type": "access",
                "iat": "2024-01-01T00:00:00",
            }
        }
    )


class LoginForm(BaseModel):
    """"""

    email: EmailStr = Field(examples=["moses@samotelecoms.co.za"])
    password: str = Field(examples=["Password123"])


class PasswordChange(BaseModel):
    """Schema for changing user password."""

    current_password: str = Field(
        min_length=1,
        description="The user's current password",
        examples=["OldPassword123"],
    )
    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="The new password (min 12 chars, must include a letter and a digit)",
        examples=["NewPassword456"],
    )
    confirm_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="Confirm the new password",
        examples=["NewPassword456"],
    )

    _strength = field_validator("new_password")(validate_password_strength)


class AdminPasswordReset(BaseModel):
    """Schema for admin-initiated password reset."""

    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="The replacement password (min 12 chars, must include a letter and a digit)",
        examples=["ResetPassword456"],
    )
    confirm_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="Confirm the replacement password",
        examples=["ResetPassword456"],
    )

    _strength = field_validator("new_password")(validate_password_strength)


class PasskeyCeremonyStart(BaseModel):
    """"""

    ceremony_id: UUID
    options: dict[str, Any] = Field(default_factory=dict)


class PasskeyCredentialResponse(BaseModel):
    """"""

    id: UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None = None
    device_type: str | None = None
    backed_up: bool | None = None
    transports: list[str] = Field(default_factory=list)


class PasskeyRegistrationVerification(BaseModel):
    """"""

    ceremony_id: UUID
    credential: dict[str, Any] = Field(default_factory=dict)
    name: str | None = Field(default=None, max_length=100)


class PasskeyAuthenticationVerification(BaseModel):
    """"""

    ceremony_id: UUID
    credential: dict[str, Any] = Field(default_factory=dict)


class PasskeyMutationResponse(BaseModel):
    """"""

    message: str


class PerformanceHintCookies(BaseModel):
    """Small client-side hints that help the UI restore fast default state."""

    dashboard_view: str | None = Field(default=None, max_length=40)
    dashboard_region: str | None = Field(default=None, max_length=80)
    dashboard_date_range: str | None = Field(default=None, max_length=40)
    table_density: Literal["compact", "comfortable", "spacious"] | None = None

    @field_validator(
        "dashboard_view",
        "dashboard_region",
        "dashboard_date_range",
        mode="before",
    )
    @classmethod
    def clean_hint_value(cls, value: str | None) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip()
        if not cleaned:
            return None

        if any(char in cleaned for char in (";", "\r", "\n")):
            raise ValueError("Cookie hint values cannot contain control characters")

        return cleaned
