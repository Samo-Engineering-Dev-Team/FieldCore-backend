from pydantic import BaseModel, ConfigDict, EmailStr, Field
from uuid import uuid4, UUID
from datetime import datetime
from typing import Any

from app.utils.enums import UserRole


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
        examples=[str(uuid4())]
    )
    role: UserRole = Field(
        description="The role of the authenticated user (e.g., admin, noc, technician)",
        examples=[UserRole.ADMIN]
    )
    name: str | None = Field(
        default=None,
        description="The first name of the authenticated user",
        examples=["John"]
    )
    surname: str | None = Field(
        default=None,
        description="The last name of the authenticated user",
        examples=["Doe"]
    )
    tenant_id: str | None = Field(
        default=None,
        description="Optional tenant scope associated with authenticated user",
        examples=["tenant-123"]
    )
    must_change_password: bool = Field(
        default=False,
        description="Whether user must set a new password before accessing the app",
        examples=[False]
    )
    exp: datetime | None = Field(
        default=None,
        description="Expiration datetime of the token in UTC",
        examples=[datetime(2024, 12, 31, 23, 59, 59)]
    )
    token_type: str | None = Field(
        default=None,
        description="Type of token: 'access' for access tokens, 'refresh' for refresh tokens",
        examples=["access", "refresh"]
    )
    iat: datetime | None = Field(
        default=None,
        description="Issued at datetime of the token in UTC",
        examples=[datetime(2024, 1, 1, 0, 0, 0)]
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "123e4567-e89b-12d3-a456-426614174000",
                "role": "user",
                "name": "John",
                "surname": "Doe",
                "tenant_id": "tenant-123",
                "must_change_password": False,
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
        examples=["OldPassword123"]
    )
    new_password: str = Field(
        min_length=8,
        max_length=128,
        description="The new password (8-128 characters)",
        examples=["NewPassword456"]
    )
    confirm_password: str = Field(
        min_length=8,
        max_length=128,
        description="Confirm the new password",
        examples=["NewPassword456"]
    )


class AdminPasswordReset(BaseModel):
    """Schema for admin-initiated password reset."""

    new_password: str = Field(
        min_length=8,
        max_length=128,
        description="The replacement password (8-128 characters)",
        examples=["ResetPassword456"]
    )
    confirm_password: str = Field(
        min_length=8,
        max_length=128,
        description="Confirm the replacement password",
        examples=["ResetPassword456"]
    )


class PasswordResetCompletion(BaseModel):
    """Schema for user-completed password reset after temporary login."""

    new_password: str = Field(
        min_length=8,
        max_length=128,
        description="The user's final replacement password (8-128 characters)",
        examples=["FinalPassword456"]
    )
    confirm_password: str = Field(
        min_length=8,
        max_length=128,
        description="Confirm the user's final replacement password",
        examples=["FinalPassword456"]
    )


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
