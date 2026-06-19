from uuid import UUID

from sqlmodel import Session, select

from app.exceptions.http import ForbiddenException, NotFoundException
from app.models import Technician
from app.models.auth import TokenData
from app.utils.enums import UserRole

MANAGEMENT_ROLES = (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.MANAGER, UserRole.NOC)
ADMIN_MANAGER_ROLES = (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.MANAGER)
REPORT_READ_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.MANAGER,
    UserRole.NOC,
    UserRole.TECHNICIAN,
    UserRole.PARTNER,
)
REPORT_EXPORT_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.MANAGER,
    UserRole.NOC,
    UserRole.PARTNER,
)
REPORT_WRITE_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.MANAGER,
    UserRole.NOC,
    UserRole.TECHNICIAN,
)


def is_management(current_user: TokenData) -> bool:
    return current_user.role in MANAGEMENT_ROLES


def is_admin_or_manager(current_user: TokenData) -> bool:
    return current_user.role in ADMIN_MANAGER_ROLES


def require_roles(
    current_user: TokenData,
    allowed_roles: tuple[UserRole, ...],
    message: str,
) -> None:
    if current_user.role not in allowed_roles:
        raise ForbiddenException(message)


def require_management(current_user: TokenData, message: str) -> None:
    require_roles(current_user, MANAGEMENT_ROLES, message)


def require_admin_or_manager(current_user: TokenData, message: str) -> None:
    require_roles(current_user, ADMIN_MANAGER_ROLES, message)


def require_report_read(current_user: TokenData, message: str) -> None:
    require_roles(current_user, REPORT_READ_ROLES, message)


def require_report_export(current_user: TokenData, message: str) -> None:
    require_roles(current_user, REPORT_EXPORT_ROLES, message)


def require_report_write(current_user: TokenData, message: str) -> None:
    require_roles(current_user, REPORT_WRITE_ROLES, message)


def get_technician_by_user(user_id: UUID, session: Session) -> Technician:
    technician = session.exec(
        select(Technician).where(
            Technician.user_id == user_id,
            Technician.deleted_at.is_(None),  # type: ignore
        )
    ).first()
    if not technician:
        raise NotFoundException("technician profile not found for current user")
    return technician


def get_technician_id_for_user(user_id: UUID, session: Session) -> UUID:
    return get_technician_by_user(user_id, session).id


def assert_self_or_roles(
    target_user_id: UUID,
    current_user: TokenData,
    allowed_roles: tuple[UserRole, ...],
    message: str,
) -> None:
    if current_user.user_id == target_user_id:
        return
    require_roles(current_user, allowed_roles, message)


def assert_technician_self_or_roles(
    target_technician_id: UUID,
    current_user: TokenData,
    session: Session,
    allowed_roles: tuple[UserRole, ...],
    message: str,
) -> None:
    if current_user.role in allowed_roles:
        return

    technician_id = get_technician_id_for_user(current_user.user_id, session)
    if technician_id != target_technician_id:
        raise ForbiddenException(message)
