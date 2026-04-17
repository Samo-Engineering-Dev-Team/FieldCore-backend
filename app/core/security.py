from passlib.context import CryptContext
from jose import jwt, JWTError
from uuid import UUID
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models import Token, TokenData
from app.utils.enums import UserRole
from app.utils.funcs import utcnow
from app.exceptions.http import UnauthorizedException
from .settings import app_settings


class SecurityUtils:
    """
    Utility class for security-related operations including password hashing,
    token generation, and token verification.
    
    This class provides methods for:
    - Hashing and verifying passwords using Argon2
    - Creating and decoding JWT access tokens
    - Creating and decoding JWT refresh tokens
    """

    context = CryptContext(
        schemes=["argon2"],
        deprecated="auto",
    )

    @classmethod
    def hash_password(cls, password: str) -> str:
        """
        Hash a plain text password using `Argon2`.
        
        Args:
            password: The plain text password to hash
            
        Returns:
            The hashed password string
        """
        return cls.context.hash(password)

    @classmethod
    def check_password(cls, password: str, password_hash: str) -> bool:
        """
        Verify a plain text password against a hashed password.
        
        Args:
            password: The plain text password to verify
            password_hash: The hashed password to compare against
            
        Returns:
            True if the password matches the hash, False otherwise
        """
        return cls.context.verify(password, password_hash)

    @classmethod
    def create_token(
        cls,
        user_id: UUID,
        role: UserRole,
        name: str,
        surname: str,
        must_change_password: bool = False,
        exp: datetime | None = None,
    ) -> Token:
        """
        Create a JWT access token for a user.
        
        Args:
            user_id: The unique identifier of the user
            role: The role of the user (from UserRole enum)
            name: The first name of the user
            surname: The last name of the user
            exp: Optional custom expiration datetime. If not provided, uses default from settings
            
        Returns:
            A Token object containing the encoded JWT access token
        """
        expiration = (
            exp
            if exp
            else utcnow() + timedelta(minutes=app_settings.JWT_TOKEN_EXPIRE_MINUTES)
        )
        
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "role": role,
            "name": name,
            "surname": surname,
            "must_change_password": must_change_password,
            "exp": expiration,
            "iat": utcnow(),
            "type": "access",
        }
        
        encoded = jwt.encode(
            payload,
            key=app_settings.JWT_SECRET_KEY,
            algorithm=app_settings.JWT_ALGORITHM,
        )
        
        return Token(access_token=encoded, token_type="bearer")

    @classmethod
    def create_refresh_token(
        cls,
        user_id: UUID,
        role: UserRole,
        name: str,
        surname: str,
        must_change_password: bool = False,
        exp: datetime | None = None,
    ) -> Token:
        """
        Create a JWT refresh token for a user.
        
        Args:
            user_id: The unique identifier of the user
            role: The role of the user (from UserRole enum)
            name: The first name of the user
            surname: The last name of the user
            exp: Optional custom expiration datetime. If not provided, uses default from settings
            
        Returns:
            A Token object containing the encoded JWT refresh token
        """
        expiration = (
            exp
            if exp
            else utcnow() + timedelta(days=app_settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        )
        
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "role": role,
            "name": name,
            "surname": surname,
            "must_change_password": must_change_password,
            "exp": expiration,
            "iat": utcnow(),
            "type": "refresh",
        }
        
        encoded = jwt.encode(
            payload,
            key=app_settings.JWT_SECRET_KEY,
            algorithm=app_settings.JWT_ALGORITHM,
        )
        
        return Token(access_token=encoded, is_refresh=True)

    @staticmethod
    def decode_token(token: Token | str, verify_type: str | None = None) -> TokenData:
        """
        Decode and verify a JWT token.
        
        Args:
            token: Either a Token object or a string containing the JWT
            verify_type: Optional token type to verify ("access" or "refresh")
            
        Returns:
            TokenData object containing the decoded token information
            
        Raises:
            UnauthorizedException: If the token is invalid, expired, or malformed
        """
        try:
            token_str = token.access_token if isinstance(token, Token) else token
            
            decoded = jwt.decode(
                token_str,
                key=app_settings.JWT_SECRET_KEY,
                algorithms=[app_settings.JWT_ALGORITHM],
            )
            
            user_id: str | None = decoded.get("user_id")
            role: str | None = decoded.get("role")
            name: str | None = decoded.get("name")
            surname: str | None = decoded.get("surname")
            must_change_password: bool = bool(decoded.get("must_change_password", False))
            exp: int | None = decoded.get("exp")
            iat: int | None = decoded.get("iat")
            token_type: str | None = decoded.get("type")
            
            if not user_id or not role:
                raise UnauthorizedException("invalid token payload")
            
            # Verify token type if specified
            if verify_type and token_type != verify_type:
                raise UnauthorizedException(
                    f"expected {verify_type} token, got {token_type}"
                )
            
            # Convert expiration timestamp to datetime
            expiration = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None
            issued_at = datetime.fromtimestamp(iat, tz=timezone.utc) if iat else None
            
            return TokenData(
                user_id=UUID(user_id),
                role=UserRole(role),
                name=name,
                surname=surname,
                must_change_password=must_change_password,
                exp=expiration,
                token_type=token_type,
                iat=issued_at,
            )
            
        except JWTError as e:
            raise UnauthorizedException(f"invalid token: {str(e)}")
        except ValueError as e:
            raise UnauthorizedException(f"invalid token data: {str(e)}")

    @staticmethod
    def verify_access_token(token: Token | str) -> TokenData:
        """
        Verify that a token is a valid access token.
        
        Args:
            token: Either a Token object or a string containing the JWT
            
        Returns:
            TokenData object containing the decoded token information
            
        Raises:
            UnauthorizedException: If the token is not a valid access token
        """
        return SecurityUtils.decode_token(token, verify_type="access")

    @staticmethod
    def verify_refresh_token(token: Token | str) -> TokenData:
        """
        Verify that a token is a valid refresh token.
        
        Args:
            token: Either a Token object or a string containing the JWT
            
        Returns:
            TokenData object containing the decoded token information
            
        Raises:
            UnauthorizedException: If the token is not a valid refresh token
        """
        return SecurityUtils.decode_token(token, verify_type="refresh")
