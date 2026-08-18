from uuid import UUID

from loguru import logger as LOG
from sqlmodel import Session, select

from app.exceptions.http import ForbiddenException, NotFoundException
from app.models import FundsCapabilityAssignment, Technician, TechnicianSite
from app.models.auth import TokenData
from app.utils.enums import FundsCapability, UserRole

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
# SHEQ officer read access (SHEQ-CHECKLISTS-PLAN.md §8.2) — deliberately does
# NOT fold into REPORT_READ_ROLES or any incident tuple. A sheq user reads and
# exports SHEQ checklists only; create/update/delete/signature stay on
# require_management or the submitting technician themselves.
SHEQ_READ_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.MANAGER,
    UserRole.NOC,
    UserRole.SHEQ,
)

# Finance–Technician workflow (docs/FieldCore_Finance_Technician_Workflow_Spec.md).
#
# These tuples gate *sight* of funds data. They deliberately do NOT gate the
# power to move a request along the chain — that is a FundsCapability row, checked
# by require_funds_capability below. Keeping the two separate is the whole point
# of decision 1 in FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md: a manager can see
# the Finance Dashboard without being able to approve, load or release anything.
FINANCE_READ_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.MANAGER,
    UserRole.FINANCE,
)
# Who may see any technician's funds records. A technician outside this tuple is
# scoped to their own rows by the service layer.
FUNDS_READ_ALL_ROLES = FINANCE_READ_ROLES


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


def require_sheq_read(current_user: TokenData, message: str) -> None:
    require_roles(current_user, SHEQ_READ_ROLES, message)


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


# ── Funds chain capabilities ──────────────────────────────────────────────
#
# Authority over a stage of the release chain lives in `funds_capabilities`, not
# in UserRole. The spec names individuals against each stage; those names are
# illustrative and nothing here is hardcoded to a person.
#
# Note there is deliberately no management override. Spec §6 is explicit that
# only the loader and the releasers can move money, and an admin bypass would
# quietly undo that control. An admin who genuinely needs to act grants
# themselves the capability first, which leaves a row behind saying so.


def has_funds_capability(
    user_id: UUID, capability: FundsCapability, session: Session
) -> bool:
    row = session.exec(
        select(FundsCapabilityAssignment).where(
            FundsCapabilityAssignment.user_id == user_id,
            FundsCapabilityAssignment.capability == capability,
            FundsCapabilityAssignment.is_active,  # type: ignore[arg-type]
            FundsCapabilityAssignment.deleted_at.is_(None),  # type: ignore
        )
    ).first()
    return row is not None


def get_funds_capability(
    user_id: UUID, capability: FundsCapability, session: Session
) -> FundsCapabilityAssignment | None:
    return session.exec(
        select(FundsCapabilityAssignment).where(
            FundsCapabilityAssignment.user_id == user_id,
            FundsCapabilityAssignment.capability == capability,
            FundsCapabilityAssignment.is_active,  # type: ignore[arg-type]
            FundsCapabilityAssignment.deleted_at.is_(None),  # type: ignore
        )
    ).first()


def require_funds_capability(
    current_user: TokenData, capability: FundsCapability, session: Session
) -> FundsCapabilityAssignment:
    """Assert the caller holds `capability`, returning the assignment row.

    The row is returned rather than discarded because callers need its
    `is_fallback` flag: a fallback approver acts through the identical path, but
    the fact that they did is recorded on Disbursement.is_fallback_approval so
    Finance can see the escalation happened (spec §2).
    """
    assignment = get_funds_capability(current_user.user_id, capability, session)
    if assignment is None:
        raise ForbiddenException(
            f"You do not hold the '{capability.value}' funds capability. "
            "Ask an administrator to assign it before acting on this request."
        )
    return assignment


def users_with_funds_capability(
    capability: FundsCapability, session: Session
) -> list[UUID]:
    """User IDs to notify when a request reaches a stage. Includes fallback
    holders — they need to see the queue in order to be able to stand in."""
    rows = session.exec(
        select(FundsCapabilityAssignment.user_id).where(
            FundsCapabilityAssignment.capability == capability,
            FundsCapabilityAssignment.is_active,  # type: ignore[arg-type]
            FundsCapabilityAssignment.deleted_at.is_(None),  # type: ignore
        )
    ).all()
    return list(dict.fromkeys(rows))


def require_finance_read(current_user: TokenData, message: str) -> None:
    require_roles(current_user, FINANCE_READ_ROLES, message)


def can_read_all_funds(current_user: TokenData) -> bool:
    return current_user.role in FUNDS_READ_ALL_ROLES
