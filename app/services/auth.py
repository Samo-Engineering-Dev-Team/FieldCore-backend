from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from typing import Annotated, Optional
from sqlmodel import select, Session, text
from uuid import UUID

from app.core import SecurityUtils
from app.models import User, Token, TokenData, LoginForm, PasswordChange, PasswordResetCompletion
from app.exceptions.http import UnauthorizedException, NotFoundException, ForbiddenException, BadRequestException
from app.utils.enums import UserRole
from app.utils.funcs import utcnow
from app.database import get_session
from loguru import logger as LOG

oauth = OAuth2PasswordBearer("/api/v1/auth/login")


def _record_login(
    session: Session,
    *,
    email: str,
    success: bool,
    user_id: Optional[UUID] = None,
    role: Optional[str] = None,
    failure_reason: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Insert a row into login_audit. Swallows errors so a DB issue never blocks login."""
    try:
        session.execute(
            text(
                """
                INSERT INTO login_audit
                    (user_id, email, ip_address, user_agent, success, failure_reason, role)
                VALUES
                    (:user_id, :email, :ip_address, :user_agent, :success, :failure_reason, :role)
                """
            ),
            {
                "user_id": str(user_id) if user_id else None,
                "email": email,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "success": success,
                "failure_reason": failure_reason,
                "role": str(role) if role else None,
            },
        )
        session.commit()
    except Exception as exc:
        LOG.warning("Could not write login_audit row: {}", exc)


class _AuthService:

    def authenticate(
        self,
        form: LoginForm,
        session: Session,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Token:
        """"""
        statement = select(User).where(User.email == form.email, User.deleted_at.is_(None)) # type: ignore
        user: User | None = session.exec(statement).first()

        if not user:
            _record_login(session, email=form.email, success=False,
                          failure_reason="User not found",
                          ip_address=ip_address, user_agent=user_agent)
            raise UnauthorizedException("Invalid email or password")

        if not user.is_active():
            _record_login(session, email=form.email, success=False,
                          user_id=user.id, role=str(user.role),
                          failure_reason="Account deactivated",
                          ip_address=ip_address, user_agent=user_agent)
            raise UnauthorizedException("This account has been deactivated. Please contact your admin.")

        if not SecurityUtils.check_password(form.password, user.password_hash):
            _record_login(session, email=form.email, success=False,
                          user_id=user.id, role=str(user.role),
                          failure_reason="Wrong password",
                          ip_address=ip_address, user_agent=user_agent)
            raise UnauthorizedException("Invalid email or password")

        _record_login(session, email=form.email, success=True,
                      user_id=user.id, role=str(user.role),
                      ip_address=ip_address, user_agent=user_agent)
        return SecurityUtils.create_token(
            user.id,
            user.role,
            user.name,
            user.surname,
            user.must_change_password,
        )

    def change_password(self, user_id: UUID, payload: PasswordChange, session: Session) -> dict:
        """Change the password for a user."""
        # Validate passwords match
        if payload.new_password != payload.confirm_password:
            raise BadRequestException("New password and confirmation do not match")
        
        # Get user from database
        user = self._get_user(user_id, session)
        
        # Verify current password
        if not SecurityUtils.check_password(payload.current_password, user.password_hash):
            raise BadRequestException("Current password is incorrect")
        
        # Check new password is different
        if payload.current_password == payload.new_password:
            raise BadRequestException("New password must be different from current password")
        
        # Update password
        user.password_hash = SecurityUtils.hash_password(payload.new_password)
        user.credentials_updated_at = utcnow()
        user.must_change_password = False
        session.add(user)
        session.commit()
        
        return {"message": "Password changed successfully"}

    def complete_password_reset(
        self,
        user_id: UUID,
        payload: PasswordResetCompletion,
        session: Session,
    ) -> Token:
        """Replace temporary password with user's final password and clear reset flag."""
        user = self._get_user(user_id, session)

        if not user.must_change_password:
            raise BadRequestException("Password reset is not required for this account")

        if payload.new_password != payload.confirm_password:
            raise BadRequestException("New password and confirmation do not match")

        if SecurityUtils.check_password(payload.new_password, user.password_hash):
            raise BadRequestException("New password must be different from temporary password")

        user.password_hash = SecurityUtils.hash_password(payload.new_password)
        user.credentials_updated_at = utcnow()
        user.must_change_password = False
        session.add(user)
        session.commit()
        session.refresh(user)

        return SecurityUtils.create_token(
            user.id,
            user.role,
            user.name,
            user.surname,
            user.must_change_password,
        )

    def read_current_user(self, current_user: TokenData, session: Session) -> TokenData:
        """Return latest user profile fields while keeping token metadata."""
        user = self._get_user(current_user.user_id, session)

        return TokenData(
            user_id=user.id,
            role=user.role,
            name=user.name,
            surname=user.surname,
            must_change_password=user.must_change_password,
            exp=current_user.exp,
            token_type=current_user.token_type,
            iat=current_user.iat,
        )

    def _get_user(self, user_id: UUID, session: Session) -> User:
        user = session.exec(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        ).first()

        if not user:
            raise NotFoundException("User not found")

        return user

def get_auth_service() -> _AuthService:
    """"""
    return _AuthService()


def get_current_user(token: str = Depends(oauth), session: Session = Depends(get_session)) -> TokenData:
    """Decode access token, then validate it against live user credentials."""
    current_user = SecurityUtils.decode_token(token, "access")

    user = session.exec(
        select(User).where(User.id == current_user.user_id, User.deleted_at.is_(None))
    ).first()

    if not user:
        raise UnauthorizedException("User not found")

    if not user.is_active():
        raise UnauthorizedException("This account has been deactivated. Please contact your admin.")

    if (
        current_user.iat is not None
        and user.credentials_updated_at is not None
        and current_user.iat < user.credentials_updated_at
    ):
        raise UnauthorizedException("Session expired. Please log in again.")

    return TokenData(
        user_id=user.id,
        role=user.role,
        name=user.name,
        surname=user.surname,
        must_change_password=user.must_change_password,
        exp=current_user.exp,
        token_type=current_user.token_type,
        iat=current_user.iat,
    )


def require_admin(current_user: TokenData = Depends(get_current_user)) -> TokenData:
    """Dependency that ensures the current user is an admin."""
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenException("Admin access required")
    return current_user


def require_noc_or_admin(current_user: TokenData = Depends(get_current_user)) -> TokenData:
    """Dependency that ensures the current user is NOC or admin."""
    if current_user.role not in (UserRole.ADMIN, UserRole.NOC):
        raise ForbiddenException("NOC or Admin access required")
    return current_user

def require_manager_or_admin(current_user: TokenData = Depends(get_current_user)) -> TokenData:
    """Dependency that ensures the current user is manager or admin."""
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        raise ForbiddenException("Manager or Admin access required")
    return current_user

def require_noc_or_manager_or_admin(current_user: TokenData = Depends(get_current_user)) -> TokenData:
    """Dependency that ensures the current user is NOC, manager, or admin."""
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER, UserRole.NOC):
        raise ForbiddenException("NOC, Manager, or Admin access required")
    return current_user


AuthService = Annotated[_AuthService, Depends(get_auth_service)]
CurrentUser = Annotated[TokenData, Depends(get_current_user)]
NocOrAdminUser = Annotated[TokenData, Depends(require_noc_or_admin)]
ManagerOrAdminUser = Annotated[TokenData, Depends(require_manager_or_admin)]
NocOrManagerOrAdminUser = Annotated[TokenData, Depends(require_noc_or_manager_or_admin)]
