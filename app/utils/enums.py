from enum import StrEnum


class UserRole(StrEnum):
    """"""
    ADMIN = "admin"
    MANAGER = "manager"
    TECHNICIAN = "technician"
    NOC = "noc"


class UserStatus(StrEnum):
    """"""
    ACTIVE = "active"
    DISABLED = "disabled"


class TaskType(StrEnum):
    RHS = "remote-hand-support"
    ROUTINE_MAINTENANCE = "routine-maintenance"


class Region(StrEnum):
    GAUTENG = "gauteng"
    MPUMALANGA = "mpumalanga"
    KZN = "kwazulu-natal"
    EASTERN_CAPE = "eastern-cape"
    NORTHERN_CAPE = "northern-cape"
    WESTERN_CAPE = "western-cape"
    FREE_STATE = "free-state"
    NORTH_WEST = "north-west"


class TaskStatus(StrEnum):
    PENDING = "pending"
    STARTED = "started"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    FAILED = "failed"


class IncidentStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in-progress"
    RESOLVED = "resolved"


class AccessRequestStatus(StrEnum):
    REQUESTED = "requested"
    REJECTED = "rejected"
    APPROVED = "approved"
    EXPIRED = "expired"


class ReportType(StrEnum):
    DIESEL = "diesel"
    REPEATER = "repeater"
    ROUTINE_DRIVE = "routine-drive"

class ReportStatus(StrEnum):
    PENDING = "pending"
    STARTED = "started"
    COMPLETED = "completed"


class NotificationPriority(StrEnum):
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class RoutineCheckStatus(StrEnum):
    YES = "yes"
    NO = "no"
    NA = "n/a"


class RoutineIssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentSeverity(StrEnum):
    """
    Contractual fault severity levels — Annexure H, SAMO/SEACOM Maintenance Agreement.
      CRITICAL : total service interruption — on-site 2h, temp restore 4h
      MAJOR    : significant impact/redundancy loss — on-site 4h, temp restore 8h
      MINOR    : non-urgent — on-site next business day, temp restore 2 business days
      QUERY    : information request — resolution within 20 business days
    """
    CRITICAL = "critical"
    MAJOR    = "major"
    MINOR    = "minor"
    QUERY    = "query"
