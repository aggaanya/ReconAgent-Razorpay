"""Reporting-period boundaries for the Finance Intelligence Engine.

Timezone policy (specification §6.2): all calendar bucketing is done in
IST (``Asia/Kolkata``, UTC+05:30) — never the server's local timezone.
IST is a fixed offset with no daylight saving, so a fixed
``timezone(timedelta(hours=5, minutes=30))`` is exactly equivalent to the
IANA zone while keeping calculations dependency-free and deterministic.

Every period is a **half-open** interval ``[start, end)``: adjacent named
periods never share an instant, so no record can be double-counted at a
boundary. Repositories filter with inclusive ``<=`` bounds, so
:func:`query_bounds` converts a half-open window into the equivalent
closed UTC bounds (end minus one microsecond) before touching the
database.

Week start is **Monday** (ISO-8601), matching standard Indian business
reporting; the specification leaves week boundaries as a project-level
decision (Part 6.1) and this module is the single place that defines them.

All functions are pure with respect to their inputs: ``now`` is always an
explicit parameter so callers (and tests) fully control the clock.
"""

from datetime import datetime, timedelta, timezone

# Spec §6.2 recommendation: IST for finance-team-facing reporting.
REPORTING_TIMEZONE_NAME = "Asia/Kolkata"
REPORTING_TIMEZONE = timezone(timedelta(hours=5, minutes=30))

# Named period presets accepted by the finance metric endpoints (spec Part 6.1).
PERIOD_PRESETS = (
    "today",
    "yesterday",
    "this_week",
    "previous_week",
    "this_month",
    "previous_month",
)

# Trend comparison granularities (spec Part 6.1).
TREND_GRANULARITIES = ("day", "week", "month")


def _start_of_day(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_week(moment: datetime) -> datetime:
    """Monday 00:00 of the ISO week containing ``moment``."""
    day_start = _start_of_day(moment)
    return day_start - timedelta(days=day_start.weekday())


def _start_of_month(moment: datetime) -> datetime:
    return moment.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _start_of_previous_month(month_start: datetime) -> datetime:
    """First instant of the month before ``month_start`` (a day-1 datetime)."""
    if month_start.month == 1:
        return month_start.replace(year=month_start.year - 1, month=12)
    return month_start.replace(month=month_start.month - 1)


def trend_pair(
    granularity: str, now: datetime
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Current/previous half-open windows for one trend granularity.

    Spec Part 6.1 table: current windows run from the calendar-period start
    to ``now``; previous windows cover the full preceding calendar period,
    ending exactly where the current window starts (no overlap, no gap).
    """
    if granularity == "day":
        today = _start_of_day(now)
        yesterday = today - timedelta(days=1)
        return (today, now), (yesterday, today)
    if granularity == "week":
        week_start = _start_of_week(now)
        previous_week_start = week_start - timedelta(days=7)
        return (week_start, now), (previous_week_start, week_start)
    if granularity == "month":
        month_start = _start_of_month(now)
        previous_month_start = _start_of_previous_month(month_start)
        return (month_start, now), (previous_month_start, month_start)
    raise ValueError(f"Unsupported granularity: {granularity!r}")


def named_period(name: str, now: datetime) -> tuple[datetime, datetime]:
    """Half-open ``(start, end)`` for one named preset from :data:`PERIOD_PRESETS`."""
    day_current, day_previous = trend_pair("day", now)
    week_current, week_previous = trend_pair("week", now)
    month_current, month_previous = trend_pair("month", now)
    windows = {
        "today": day_current,
        "yesterday": day_previous,
        "this_week": week_current,
        "previous_week": week_previous,
        "this_month": month_current,
        "previous_month": month_previous,
    }
    try:
        return windows[name]
    except KeyError:
        raise ValueError(f"Unsupported period: {name!r}") from None


def query_bounds(
    start: datetime, end: datetime
) -> tuple[datetime, datetime]:
    """Convert a half-open ``[start, end)`` window to closed UTC bounds.

    Repositories compare ``provider_created_at`` against aware-UTC
    datetimes using inclusive comparisons, mirroring the existing
    finance-scan convention. Subtracting one microsecond makes the
    half-open upper edge representable without changing any boundary
    record's membership.
    """
    return (
        start.astimezone(timezone.utc),
        (end - timedelta(microseconds=1)).astimezone(timezone.utc),
    )
