from uuid import UUID
from fastapi import Depends
from typing import List, Annotated
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from app.models import User, UserCreate, UserUpdate, UserResponse, AdminPasswordReset
from app.exceptions.http import (
    BadRequestException,
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
)
from app.utils.enums import UserRole, UserStatus
from app.core import SecurityUtils
from app.utils.funcs import utcnow


def _normalize_optional_tenant_id(tenant_id: str | None) -> str | None:
    if tenant_id is None:
        return None
    normalized = tenant_id.strip()
    return normalized or None


class _UserService:
    def user_to_response(self, user: User) -> UserResponse:
        return UserResponse(**user.model_dump(exclude={"password_hash"}))

    def create_user(
        self,
        data: UserCreate,
        session: Session,
        tenant_id: str | None = None,
    ) -> UserResponse:
        resolved_tenant_id = self._resolve_tenant_scope(
            request_tenant_id=tenant_id,
            payload_tenant_id=data.tenant_id,
        )
        user = User(
            **data.model_dump(exclude={"password", "tenant_id"}),
            tenant_id=resolved_tenant_id,
            password_hash=SecurityUtils.hash_password(data.password),
            must_change_password=True,
        )
        try:
            session.add(user)
            session.commit()
            session.refresh(user)
            return self.user_to_response(user)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating user: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating user: {e}")

    def read_user(
        self,
        user_id: UUID,
        session: Session,
        tenant_id: str | None = None,
    ) -> UserResponse:
        user = self._get_user(user_id, session)
        self._assert_user_in_tenant_scope(user, tenant_id)
        return self.user_to_response(user)

    def read_users(
        self,
        session: Session,
        status: UserStatus | None = None,
        role: UserRole | None = None,
        offset: int = 0,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> List[UserResponse]:
        statement = select(User).where(User.deleted_at.is_(None))  # type: ignore

        if status is not None:
            statement = statement.where(User.status == status)
        if role is not None:
            statement = statement.where(User.role == role)
        normalized_tenant_id = _normalize_optional_tenant_id(tenant_id)
        if normalized_tenant_id is not None:
            statement = statement.where(User.tenant_id == normalized_tenant_id)
        statement = statement.offset(offset).limit(limit)
        users = session.exec(statement).all()
        return [self.user_to_response(user) for user in users]

    def update_user(
        self,
        user_id: UUID,
        data: UserUpdate,
        session: Session,
        tenant_id: str | None = None,
    ) -> UserResponse:
        user = self._get_user(user_id, session)
        self._assert_user_in_tenant_scope(user, tenant_id)
        update_data = data.model_dump(
            exclude_none=True, exclude_defaults=True, exclude_unset=True
        )

        if not update_data:
            return self.user_to_response(user)

        if "tenant_id" in update_data:
            update_data["tenant_id"] = self._resolve_tenant_scope(
                request_tenant_id=tenant_id,
                payload_tenant_id=update_data["tenant_id"],
                current_tenant_id=user.tenant_id,
            )

        for k, v in update_data.items():
            setattr(user, k, v)

        user.touch()

        try:
            session.commit()
            session.refresh(user)
            return self.user_to_response(user)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating user: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating user: {e}")

    def delete_user(
        self,
        user_id: UUID,
        session: Session,
        tenant_id: str | None = None,
    ) -> None:
        user = self._get_user(user_id, session)
        self._assert_user_in_tenant_scope(user, tenant_id)
        user.soft_delete()
        session.commit()

    def activate_user(
        self,
        user_id: UUID,
        session: Session,
        tenant_id: str | None = None,
    ) -> UserResponse:
        """"""
        user = self._get_user(user_id, session)
        self._assert_user_in_tenant_scope(user, tenant_id)
        user.activate()
        session.commit()
        session.refresh(user)
        return self.user_to_response(user)

    def deactivate_user(
        self,
        user_id: UUID,
        session: Session,
        tenant_id: str | None = None,
    ) -> UserResponse:
        """"""
        user = self._get_user(user_id, session)
        self._assert_user_in_tenant_scope(user, tenant_id)
        user.disable()
        session.commit()
        session.refresh(user)
        return self.user_to_response(user)

    def set_user_role(
        self,
        user_id: UUID,
        role: UserRole,
        session: Session,
        tenant_id: str | None = None,
    ) -> UserResponse:
        """"""
        user = self._get_user(user_id, session)
        self._assert_user_in_tenant_scope(user, tenant_id)
        user.role = role
        session.commit()
        session.refresh(user)
        return self.user_to_response(user)

    def reset_password(
        self,
        user_id: UUID,
        payload: AdminPasswordReset,
        session: Session,
        tenant_id: str | None = None,
    ) -> dict:
        user = self._get_user(user_id, session)
        self._assert_user_in_tenant_scope(user, tenant_id)

        if payload.new_password != payload.confirm_password:
            raise BadRequestException("New password and confirmation do not match")

        if SecurityUtils.check_password(payload.new_password, user.password_hash):
            raise BadRequestException("New password must be different from current password")

        user.password_hash = SecurityUtils.hash_password(payload.new_password)
        user.credentials_updated_at = utcnow()
        user.must_change_password = True
        user.touch()

        try:
            session.commit()
            session.refresh(user)
            return {"message": "Password reset successfully"}
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error resetting password: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error resetting password: {e}")

    def _get_user(self, user_id: UUID, session: Session) -> User:
        statement = select(User).where(User.id == user_id, User.deleted_at.is_(None))  # type: ignore
        user: User | None = session.exec(statement).first()
        if not user:
            raise NotFoundException("user not found")
        return user

    def _assert_user_in_tenant_scope(self, user: User, tenant_id: str | None) -> None:
        normalized_tenant_id = _normalize_optional_tenant_id(tenant_id)
        if normalized_tenant_id is None:
            return
        if user.tenant_id != normalized_tenant_id:
            raise NotFoundException("user not found")

    def _resolve_tenant_scope(
        self,
        *,
        request_tenant_id: str | None,
        payload_tenant_id: str | None,
        current_tenant_id: str | None = None,
    ) -> str | None:
        normalized_request_tenant_id = _normalize_optional_tenant_id(request_tenant_id)
        normalized_payload_tenant_id = _normalize_optional_tenant_id(payload_tenant_id)
        normalized_current_tenant_id = _normalize_optional_tenant_id(current_tenant_id)

        if (
            normalized_request_tenant_id is not None
            and normalized_payload_tenant_id is not None
            and normalized_request_tenant_id != normalized_payload_tenant_id
        ):
            raise BadRequestException("tenant_id does not match tenant scope")

        if normalized_request_tenant_id is not None:
            return normalized_request_tenant_id
        if normalized_payload_tenant_id is not None:
            return normalized_payload_tenant_id
        return normalized_current_tenant_id


def get_user_service() -> _UserService:
    return _UserService()


UserService = Annotated[_UserService, Depends(get_user_service)]
