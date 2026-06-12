from enum import StrEnum


class UserRole(StrEnum):
    """"""

    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"
    MANAGER = "manager"
    TECHNICIAN = "technician"
    NOC = "noc"
    SHEQ = "sheq"


class LinkTarget(StrEnum):
    """
    Domain object a form submission must attach to, declared per template
    category (TemplateCategory.requires_link).

    Each non-NONE value maps to a real nullable FK column on form_submissions
    (TASK -> task_id, INCIDENT -> incident_id). Adding a new target means adding
    a new FK column + a migration.
    """
    NONE = "none"
    TASK = "task"
    INCIDENT = "incident"


class UserStatus(StrEnum):
    """"""

    ACTIVE = "active"
    DISABLED = "disabled"


class PasskeyCeremonyType(StrEnum):
    """"""

    REGISTRATION = "registration"
    AUTHENTICATION = "authentication"


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


class FieldType(StrEnum):
    """
    Field types for dynamic form templates.

    Each value has a matching coerce/validate function registered in
    app/services/form_validation.py (FIELD_TYPE_VALIDATORS). Adding a new
    type = add an enum value here + register a validator there; no table
    or column changes are required.
    """
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    ATTACHMENT = "attachment"
    ENUM = "enum"


class IncidentSeverity(StrEnum):
    """
    Contractual fault severity levels — Annexure H, SAMO/SEACOM Maintenance Agreement.
      CRITICAL : total service interruption — on-site 2h, temp restore 4h
      MAJOR    : significant impact/redundancy loss — on-site 4h, temp restore 8h
      MINOR    : non-urgent — on-site next business day, temp restore 2 business days
      QUERY    : information request — resolution within 20 business days
    """

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    QUERY = "query"
