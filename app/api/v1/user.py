from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models import UserCreate, UserUpdate, UserResponse, UserRoleUpdate
from app.services import UserService, CurrentUser
from app.database import Session
from app.utils.enums import UserRole, UserStatus
from app.exceptions.http import UnauthorizedException

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("", response_model=UserResponse, status_code=201, include_in_schema=False)
@router.post("/", response_model=UserResponse, status_code=201)
def create_user(
    payload: UserCreate,
    service: UserService,
    session: Session,
    current_user: CurrentUser
) -> UserResponse:
    """Create a new user. Only accessible to admin and manager roles."""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to create users.")
    return service.create_user(payload, session)


@router.get("", response_model=List[UserResponse], status_code=200, include_in_schema=False)
@router.get("/", response_model=List[UserResponse], status_code=200)
def read_users(
    service: UserService,
    session: Session,
    current_user: CurrentUser,
    status: UserStatus | None = Query(default=None),
    role: UserRole | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000)
) -> List[UserResponse]:
    """Get all users. Only accessible to admin and manager roles."""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to view all users.")
    return service.read_users(session, status, role, offset, limit)


@router.get("/{user_id}", response_model=UserResponse, status_code=200)
def read_user(
    user_id: UUID,
    service: UserService,
    session: Session
) -> UserResponse:
    """"""
    return service.read_user(user_id, session)


@router.patch("/{user_id}", response_model=UserResponse, status_code=200)
def update_user(
    user_id: UUID,
    payload: UserUpdate,
    service: UserService,
    session: Session,
) -> UserResponse:
    """"""
    return service.update_user(user_id, payload, session)


@router.patch("/{user_id}/role", response_model=UserResponse, status_code=200)
def set_user_role(
    user_id: UUID,
    payload: UserRoleUpdate,
    service: UserService,
    session: Session,
    current_user: CurrentUser
) -> UserResponse:
    """"""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to perform this action.")
    return service.set_user_role(user_id, payload.new_role, session)


@router.patch("/{user_id}/status/activate", response_model=UserResponse, status_code=200)
def activate_user(
    user_id: UUID,
    service: UserService,
    session: Session,
    current_user: CurrentUser
) -> UserResponse:
    """"""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to perform this action.")
    return service.activate_user(user_id, session)


@router.patch("/{user_id}/status/deactivate", response_model=UserResponse, status_code=200)
def deactivate_user(
    user_id: UUID,
    service: UserService,
    session: Session,
    current_user: CurrentUser
) -> UserResponse:
    """"""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to perform this action.")
    return service.deactivate_user(user_id, session)


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: UUID,
    service: UserService,
    session: Session,
    current_user: CurrentUser
) -> None:
    """Soft delete a user. Only accessible to admin and manager roles."""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to delete users.")
    service.delete_user(user_id, session)
