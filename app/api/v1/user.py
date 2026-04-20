from fastapi import APIRouter, Query, Request
from typing import List
from uuid import UUID

from app.models import UserCreate, UserUpdate, UserResponse, UserRoleUpdate, AdminPasswordReset
from app.services import UserService, CurrentUser
from app.database import Session
from app.utils.enums import UserRole, UserStatus
from app.exceptions.http import UnauthorizedException
from app.services.authorization import ADMIN_MANAGER_ROLES, assert_self_or_roles

router = APIRouter(prefix="/users", tags=["Users"])


def _resolve_tenant_scope(request: Request, tenant_id: str | None) -> str | None:
    query_tenant_id = tenant_id.strip() if tenant_id and tenant_id.strip() else None
    header_tenant_id = request.headers.get("X-Tenant-ID")
    header_tenant_id = header_tenant_id.strip() if header_tenant_id and header_tenant_id.strip() else None

    if (
        query_tenant_id is not None
        and header_tenant_id is not None
        and query_tenant_id != header_tenant_id
    ):
        raise UnauthorizedException("Tenant scope mismatch between header and query parameter")

    return query_tenant_id or header_tenant_id


@router.post("", response_model=UserResponse, status_code=201, include_in_schema=False)
@router.post("/", response_model=UserResponse, status_code=201)
def create_user(
    request: Request,
    payload: UserCreate,
    service: UserService,
    session: Session,
    current_user: CurrentUser,
    tenant_id: str | None = Query(default=None),
) -> UserResponse:
    """Create a new user. Only accessible to admin and manager roles."""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to create users.")
    return service.create_user(payload, session, tenant_id=_resolve_tenant_scope(request, tenant_id))


@router.get("", response_model=List[UserResponse], status_code=200, include_in_schema=False)
@router.get("/", response_model=List[UserResponse], status_code=200)
def read_users(
    request: Request,
    service: UserService,
    session: Session,
    current_user: CurrentUser,
    status: UserStatus | None = Query(default=None),
    role: UserRole | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
    tenant_id: str | None = Query(default=None),
) -> List[UserResponse]:
    """Get all users. Only accessible to admin and manager roles."""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to view all users.")
    return service.read_users(
        session,
        status,
        role,
        offset,
        limit,
        tenant_id=_resolve_tenant_scope(request, tenant_id),
    )


@router.get("/{user_id}", response_model=UserResponse, status_code=200)
def read_user(
    request: Request,
    user_id: UUID,
    service: UserService,
    session: Session,
    current_user: CurrentUser,
    tenant_id: str | None = Query(default=None),
) -> UserResponse:
    """"""
    assert_self_or_roles(
        user_id,
        current_user,
        ADMIN_MANAGER_ROLES,
        "You do not have permission to view this user.",
    )
    return service.read_user(user_id, session, tenant_id=_resolve_tenant_scope(request, tenant_id))


@router.patch("/{user_id}", response_model=UserResponse, status_code=200)
def update_user(
    request: Request,
    user_id: UUID,
    payload: UserUpdate,
    service: UserService,
    session: Session,
    current_user: CurrentUser,
    tenant_id: str | None = Query(default=None),
) -> UserResponse:
    """"""
    assert_self_or_roles(
        user_id,
        current_user,
        ADMIN_MANAGER_ROLES,
        "You do not have permission to update this user.",
    )
    return service.update_user(
        user_id,
        payload,
        session,
        tenant_id=_resolve_tenant_scope(request, tenant_id),
    )


@router.patch("/{user_id}/role", response_model=UserResponse, status_code=200)
def set_user_role(
    request: Request,
    user_id: UUID,
    payload: UserRoleUpdate,
    service: UserService,
    session: Session,
    current_user: CurrentUser,
    tenant_id: str | None = Query(default=None),
) -> UserResponse:
    """"""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to perform this action.")
    return service.set_user_role(
        user_id,
        payload.new_role,
        session,
        tenant_id=_resolve_tenant_scope(request, tenant_id),
    )


@router.post("/{user_id}/reset-password", status_code=200)
def reset_user_password(
    request: Request,
    user_id: UUID,
    payload: AdminPasswordReset,
    service: UserService,
    session: Session,
    current_user: CurrentUser,
    tenant_id: str | None = Query(default=None),
) -> dict:
    """Reset a user's password. Only accessible to admins."""
    if current_user.role != UserRole.ADMIN:
        raise UnauthorizedException("You do not have permission to reset passwords.")
    return service.reset_password(
        user_id,
        payload,
        session,
        tenant_id=_resolve_tenant_scope(request, tenant_id),
    )


@router.patch("/{user_id}/status/activate", response_model=UserResponse, status_code=200)
def activate_user(
    request: Request,
    user_id: UUID,
    service: UserService,
    session: Session,
    current_user: CurrentUser,
    tenant_id: str | None = Query(default=None),
) -> UserResponse:
    """"""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to perform this action.")
    return service.activate_user(
        user_id,
        session,
        tenant_id=_resolve_tenant_scope(request, tenant_id),
    )


@router.patch("/{user_id}/status/deactivate", response_model=UserResponse, status_code=200)
def deactivate_user(
    request: Request,
    user_id: UUID,
    service: UserService,
    session: Session,
    current_user: CurrentUser,
    tenant_id: str | None = Query(default=None),
) -> UserResponse:
    """"""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to perform this action.")
    return service.deactivate_user(
        user_id,
        session,
        tenant_id=_resolve_tenant_scope(request, tenant_id),
    )


@router.delete("/{user_id}", status_code=204)
def delete_user(
    request: Request,
    user_id: UUID,
    service: UserService,
    session: Session,
    current_user: CurrentUser,
    tenant_id: str | None = Query(default=None),
) -> None:
    """Soft delete a user. Only accessible to admin and manager roles."""
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        raise UnauthorizedException("You do not have permission to delete users.")
    service.delete_user(user_id, session, tenant_id=_resolve_tenant_scope(request, tenant_id))
