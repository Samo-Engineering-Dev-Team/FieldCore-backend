from pydantic import BaseModel, EmailStr, Field
from uuid import uuid4, UUID
from datetime import datetime

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

    class Config:
        """Pydantic model configuration."""
        json_schema_extra = {
            "example": {
                "user_id": "123e4567-e89b-12d3-a456-426614174000",
                "role": "user",
                "name": "John",
                "surname": "Doe",
                "exp": "2024-12-31T23:59:59",
                "token_type": "access",
                "iat": "2024-01-01T00:00:00"
            }
        }


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
        max_length=16,
        description="The new password (8-16 characters)",
        examples=["NewPassword456"]
    )
    confirm_password: str = Field(
        min_length=8,
        max_length=16,
        description="Confirm the new password",
        examples=["NewPassword456"]
    )
