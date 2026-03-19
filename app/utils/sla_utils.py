"""
SLA calculation utilities aligned with SAMO/SEACOM Maintenance Agreement Annexure H.

Business hours: 08:00–16:30, Monday–Friday (South Africa, no public holiday logic).
Three independent SLA milestones per fault:
  1. Respond       — technician acknowledges / contacts
  2. On-site       — technician physically present at fault location
  3. Temp restore  — service temporarily restored (permanent repair follows separately)
"""

from datetime import datetime, timedelta, time, date, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# SA is UTC+2 year-round (no DST).  Use ZoneInfo when tzdata is installed
# (required on Windows), fall back to a fixed UTC+2 offset otherwise.
try:
    SA_TZ: timezone | ZoneInfo = ZoneInfo("Africa/Johannesburg")
except ZoneInfoNotFoundError:
    SA_TZ = timezone(timedelta(hours=2))

BIZ_START = time(8, 0)    # 08:00
BIZ_END   = time(16, 30)  # 16:30

# Contractual SLA milestones in minutes (None = business-hours-based logic applies)
# Source: Annexure H of the SAMO/SEACOM Maintenance and Support Agreement
SLA_MILESTONES: dict[str, dict] = {
    "critical": {
        "respond_mins":       1,    # "Immediate" — treat as 1 minute
        "onsite_mins":        120,  # 2 hours
        "temp_restore_mins":  240,  # 4 hours total from fault raised
    },
    "major": {
        "respond_mins":       30,   # 30 minutes
        "onsite_mins":        240,  # 4 hours
        "temp_restore_mins":  480,  # 8 hours total from fault raised
    },
    # Minor and Query use business-hours logic
    "minor": {
        "respond_mins":       None,  # business hours — same day response
        "onsite_bd_days":     1,     # next business day
        "temp_restore_bd_days": 2,   # 2 business days
    },
    "query": {
        "respond_mins":       None,
        "resolution_bd_days": 20,   # 20 business days
    },
}


def _to_sa(dt: datetime) -> datetime:
    """Ensure datetime is in SA timezone."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SA_TZ)
    return dt.astimezone(SA_TZ)


def is_business_hours(dt: datetime) -> bool:
    """Return True if dt falls within SA business hours (Mon–Fri 08:00–16:30)."""
    dt_sa = _to_sa(dt)
    if dt_sa.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return BIZ_START <= dt_sa.time() <= BIZ_END


def next_business_day_start(dt: datetime) -> datetime:
    """Return the start of the next business day (08:00) after dt."""
    dt_sa = _to_sa(dt)
    candidate = dt_sa + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.replace(hour=8, minute=0, second=0, microsecond=0)


def add_business_days(dt: datetime, days: int) -> datetime:
    """Add n full business days to dt, returning end of working day."""
    dt_sa = _to_sa(dt)
    # If outside business hours, start counting from next business day start
    if not is_business_hours(dt_sa) or dt_sa.time() >= BIZ_END:
        dt_sa = next_business_day_start(dt_sa)

    added = 0
    current = dt_sa
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current.replace(hour=16, minute=30, second=0, microsecond=0)


def calculate_sla_deadlines(severity: str, raised_at: datetime) -> dict[str, datetime | None]:
    """
    Calculate the three SLA milestone deadlines for a given severity and raise time.

    Returns a dict with keys: respond_deadline, onsite_deadline, temp_restore_deadline.
    Values are None if not applicable for the given severity.
    """
    milestones = SLA_MILESTONES.get(severity, SLA_MILESTONES["minor"])
    ra = _to_sa(raised_at) if raised_at else _to_sa(datetime.now(SA_TZ))

    respond_deadline      = None
    onsite_deadline       = None
    temp_restore_deadline = None

    respond_mins = milestones.get("respond_mins")
    if respond_mins is not None:
        respond_deadline = ra + timedelta(minutes=respond_mins)

    onsite_mins = milestones.get("onsite_mins")
    if onsite_mins is not None:
        onsite_deadline = ra + timedelta(minutes=onsite_mins)

    onsite_bd_days = milestones.get("onsite_bd_days")
    if onsite_bd_days is not None:
        onsite_deadline = next_business_day_start(ra)

    temp_restore_mins = milestones.get("temp_restore_mins")
    if temp_restore_mins is not None:
        temp_restore_deadline = ra + timedelta(minutes=temp_restore_mins)

    temp_restore_bd = milestones.get("temp_restore_bd_days")
    if temp_restore_bd is not None:
        temp_restore_deadline = add_business_days(ra, temp_restore_bd)

    resolution_bd = milestones.get("resolution_bd_days")
    if resolution_bd is not None:
        temp_restore_deadline = add_business_days(ra, resolution_bd)

    return {
        "respond_deadline":       respond_deadline,
        "onsite_deadline":        onsite_deadline,
        "temp_restore_deadline":  temp_restore_deadline,
    }


def get_milestone_status(deadline: datetime | None, actual: datetime | None, now: datetime) -> dict:
    """
    Return the status of a single SLA milestone.
    Status values: 'not_applicable', 'pending', 'met', 'at_risk', 'breached'
    """
    if deadline is None:
        return {"status": "not_applicable", "deadline": None, "actual": None}

    if actual:
        return {
            "status": "met" if actual <= deadline else "breached",
            "deadline": deadline.isoformat(),
            "actual": actual.isoformat(),
            "delay_minutes": max(0, int((actual - deadline).total_seconds() / 60)),
        }

    time_remaining = (deadline - now).total_seconds()
    total = (deadline - (deadline - timedelta(hours=1))).total_seconds()  # context window
    pct_used = max(0.0, 1.0 - (time_remaining / max((deadline - deadline).total_seconds() + 3600, 3600)))

    if time_remaining <= 0:
        status = "breached"
    elif time_remaining <= 1800:  # 30 minutes remaining
        status = "at_risk"
    else:
        status = "pending"

    return {
        "status": status,
        "deadline": deadline.isoformat(),
        "actual": None,
        "seconds_remaining": max(0, int(time_remaining)),
    }
