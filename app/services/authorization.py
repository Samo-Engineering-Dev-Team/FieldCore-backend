from uuid import UUID

from loguru import logger as LOG
from sqlmodel import Session, select

from app.exceptions.http import ForbiddenException, NotFoundException
from app.models import Technician, TechnicianSite
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
    UserRole.TECHNICIAN,
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


def assigned_site_ids(technician_id: UUID, session: Session) -> list[UUID]:
    """Site IDs assigned to a technician via the technician_sites join table."""
    rows = session.exec(
        select(TechnicianSite.site_id).where(
            TechnicianSite.technician_id == technician_id
        )
    ).all()
    return list(rows)


def covered_site_ids(technician_id: UUID, session: Session) -> list[UUID]:
    """Site IDs a technician is covering for someone else *this week*.

    Coverage is granted up front by NOC/management (`MaintenanceScheduleCoverage`
    carries `assigned_by_user_id` and a `reason`), so it is already an audited
    grant — a covering technician needs to see the site to file the report.

    Matches the week window by exact equality on the ISO bounds, the same way
    `app/services/maintenance_schedule.py` queries coverage; rows are written with
    exactly those bounds.
    """
    # Local imports: authorization.py sits below the services layer and importing
    # maintenance_schedule at module scope would create a cycle.
    from app.models import MaintenanceSchedule, MaintenanceScheduleCoverage
    from app.services.maintenance_schedule import _week_bounds

    week_start, week_end = _week_bounds()
    rows = session.exec(
        select(MaintenanceSchedule.site_id)
        .join(
            MaintenanceScheduleCoverage,
            MaintenanceScheduleCoverage.schedule_id == MaintenanceSchedule.id,  # type: ignore
        )
        .where(
            MaintenanceScheduleCoverage.assigned_technician_id == technician_id,
            MaintenanceScheduleCoverage.week_start_at == week_start,
            MaintenanceScheduleCoverage.week_end_at == week_end,
            MaintenanceScheduleCoverage.cancelled_at.is_(None),  # type: ignore
            MaintenanceScheduleCoverage.deleted_at.is_(None),  # type: ignore
        )
    ).all()
    return list(rows)


def site_scope_for_user(current_user: TokenData, session: Session) -> list[UUID] | None:
    """Site IDs the current user may see, or None when unrestricted.

    Only the technician role is restricted — management and partner roles are
    unscoped. A technician sees their assigned sites plus any site they are
    covering for this week.

    An empty list means "restricted to nothing" and callers MUST treat it as such
    rather than as "no restriction"; a technician with no assignments and no
    coverage sees no sites.
    """
    if current_user.role != UserRole.TECHNICIAN:
        return None

    try:
        technician_id = get_technician_id_for_user(current_user.user_id, session)
    except NotFoundException:
        # A technician-role login with no technician row is broken data. Scope it
        # to nothing rather than 404ing a list endpoint or leaking every site.
        LOG.warning(
            "No technician profile for technician-role user {}; scoping sites to none",
            current_user.user_id,
        )
        return []

    assigned = assigned_site_ids(technician_id, session)
    covered = covered_site_ids(technician_id, session)
    # dict.fromkeys de-duplicates while keeping assigned sites first.
    return list(dict.fromkeys([*assigned, *covered]))


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
