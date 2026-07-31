import re
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    """Return the current date and time with a UTC timezone"""
    return datetime.now(tz=timezone.utc)


def format_iso_week(dt: datetime | None) -> str:
    """
    Format a date as the ISO-8601 week label used on diesel reports, e.g. "WEEK 30".

    Mirrored client-side by `formatIsoWeek` in the frontend's `src/lib/helpers.ts`
    and mobile's `lib/diesel.ts` — keep all three in step.
    """
    if dt is None:
        return "N/A"
    try:
        return f"WEEK {dt.isocalendar().week}"
    except (AttributeError, ValueError):
        return "N/A"


def parse_diesel_runtime_minutes(value: Any) -> int | None:
    """
    Parse generator runtime stored as hours, a numeric string, or H/M notation.

    Returns total minutes, or None when the value is missing or unparseable
    (the log carries "N/A" and blank runtimes).
    """
    if isinstance(value, bool) or value is None:
        return None

    if isinstance(value, (int, float)):
        if not value or value < 0:
            return None
        total_minutes = round(float(value) * 60)
        return total_minutes if total_minutes > 0 else None

    if not isinstance(value, str):
        return None

    normalized = value.strip().upper().replace(" ", "")
    if not normalized:
        return None

    runtime_match = re.fullmatch(r"(?:(\d+)H(?:(\d{1,2})M)?|(\d+)M)", normalized)
    if runtime_match:
        hours = int(runtime_match.group(1) or 0)
        minutes = int(runtime_match.group(2) or runtime_match.group(3) or 0)
        if minutes >= 60:
            return None
        total_minutes = (hours * 60) + minutes
        return total_minutes if total_minutes > 0 else None

    try:
        numeric_hours = float(normalized)
    except ValueError:
        return None

    if numeric_hours <= 0:
        return None
    total_minutes = round(numeric_hours * 60)
    return total_minutes if total_minutes > 0 else None
