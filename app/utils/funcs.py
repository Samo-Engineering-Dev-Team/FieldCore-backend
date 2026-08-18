import re
from datetime import date, datetime, timedelta, timezone
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


# ── Finance–Technician reporting period ───────────────────────────────────

SAST = timezone(timedelta(hours=2))
"""Africa/Johannesburg. A fixed UTC+2 offset — South Africa observes no DST,
so a plain timezone is exact here and avoids a tzdata dependency."""

_FRIDAY = 4  # datetime.weekday(): Monday=0 .. Sunday=6


def funds_period(at: datetime | None = None) -> tuple[datetime, datetime]:
    """
    Return the Friday→Thursday reporting period containing `at`, as a
    (start, end) pair of UTC datetimes.

    The cycle is anchored to **SAST, not UTC** (decision 6 of
    FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md). Anchoring to UTC would end the
    period at 21:59 SAST on Thursday, silently pushing the last two hours of
    Thursday's reconciliations into the following period and misreporting both
    "Outstanding" and "Recon Rate" — the single highest-likelihood correctness
    bug in this feature. Every finance query derives its bounds from here so
    that anchoring is defined exactly once.

    Boundaries match the cadence in spec §3.1/§3.4: requests are expected by
    Friday, reconciliations by Thursday. `start` is Friday 00:00:00.000000
    SAST; `end` is Thursday 23:59:59.999999 SAST, i.e. inclusive — filter with
    `start <= column <= end`, not a half-open range.

    A naive `at` is read as UTC rather than as system-local time, so behaviour
    does not depend on the host's timezone.
    """
    moment = at or utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(SAST)

    days_since_friday = (local.weekday() - _FRIDAY) % 7
    start_local = (local - timedelta(days=days_since_friday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_local = start_local + timedelta(days=7) - timedelta(microseconds=1)

    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def funds_period_for_date(day: date) -> tuple[datetime, datetime]:
    """`funds_period` for a calendar date, read as that date in SAST."""
    return funds_period(datetime(day.year, day.month, day.day, 12, 0, tzinfo=SAST))


def funds_period_label(start: datetime) -> str:
    """
    Human label for a period, e.g. "15 Aug – 21 Aug 2026". Rendered in SAST so
    it reads as the Friday/Thursday the team actually means.
    """
    start_local = start.astimezone(SAST)
    end_local = start_local + timedelta(days=6)
    if start_local.year == end_local.year:
        return f"{start_local:%-d %b} – {end_local:%-d %b %Y}"
    return f"{start_local:%-d %b %Y} – {end_local:%-d %b %Y}"
