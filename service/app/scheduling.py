"""Business-hours scheduling in each lead's own timezone.

Every ``due_at`` stored in the database is UTC; the local-time reasoning
happens here and nowhere else.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.settings_store import BusinessHours

log = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")


def resolve_timezone(name: str | None) -> ZoneInfo:
    """Return the lead's timezone, falling back to UTC for bad data."""
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown timezone %r on lead; falling back to UTC", name)
        return UTC


def is_business_day(d: date, hours: BusinessHours) -> bool:
    return d.weekday() in hours.weekdays


def advance_business_days(start: date, n: int, hours: BusinessHours) -> date:
    """Move ``n`` business days forward from ``start``.

    ``n == 0`` returns ``start`` itself if it is a business day, otherwise the
    next one — a sequence never starts on a weekend.
    """
    d = start
    if n == 0:
        while not is_business_day(d, hours):
            d += timedelta(days=1)
        return d

    remaining = n
    while remaining > 0:
        d += timedelta(days=1)
        if is_business_day(d, hours):
            remaining -= 1
    return d


def clamp_into_window(
    local_dt: datetime, hours: BusinessHours
) -> datetime:
    """Push a local datetime into the next valid business-hours slot.

    Before the window   -> same day at ``start_hour``.
    Inside the window   -> unchanged.
    After the window    -> next business day at ``start_hour``.
    Non-business day    -> next business day at ``start_hour``.
    """
    d = local_dt

    if not is_business_day(d.date(), hours):
        nxt = advance_business_days(d.date(), 1, hours)
        return d.replace(
            year=nxt.year, month=nxt.month, day=nxt.day,
            hour=hours.start_hour, minute=0, second=0, microsecond=0,
        )

    if d.hour < hours.start_hour:
        return d.replace(hour=hours.start_hour, minute=0, second=0, microsecond=0)

    if d.hour >= hours.end_hour:
        nxt = advance_business_days(d.date(), 1, hours)
        return d.replace(
            year=nxt.year, month=nxt.month, day=nxt.day,
            hour=hours.start_hour, minute=0, second=0, microsecond=0,
        )

    return d


def step_due_at(
    anchor_utc: datetime,
    timezone_name: str | None,
    gap_business_days: int,
    hours: BusinessHours,
) -> datetime:
    """Compute a step's UTC ``due_at``.

    The anchor's local time-of-day is preserved where it already falls inside
    the window, so a sequence keeps a natural, consistent sending hour rather
    than every step landing exactly at 09:00.
    """
    tz = resolve_timezone(timezone_name)
    local_anchor = anchor_utc.astimezone(tz)

    target_date = advance_business_days(
        local_anchor.date(), gap_business_days, hours
    )

    # Preserve the anchor's time-of-day; clamp_into_window fixes it if it sits
    # outside the window.
    candidate = datetime.combine(
        target_date, local_anchor.timetz().replace(tzinfo=None), tzinfo=tz
    )
    candidate = clamp_into_window(candidate, hours)
    return candidate.astimezone(UTC)


def build_schedule(
    anchor_utc: datetime,
    timezone_name: str | None,
    gaps: list[int],
    hours: BusinessHours,
) -> list[datetime]:
    """Compute ``due_at`` for every step, guaranteeing strict ordering."""
    out: list[datetime] = []
    for gap in gaps:
        due = step_due_at(anchor_utc, timezone_name, gap, hours)
        if out and due <= out[-1]:
            # Defensive: a pathological gap list must never produce two steps
            # due at the same instant or out of order.
            due = out[-1] + timedelta(minutes=1)
        out.append(due)
    return out


def within_business_hours(
    now_utc: datetime, timezone_name: str | None, hours: BusinessHours
) -> bool:
    tz = resolve_timezone(timezone_name)
    local = now_utc.astimezone(tz)
    return (
        is_business_day(local.date(), hours)
        and hours.start_hour <= local.hour < hours.end_hour
    )


def next_business_day_start(
    now_utc: datetime, timezone_name: str | None, hours: BusinessHours
) -> datetime:
    """First moment of the next business day, in UTC. Used when a cap is hit."""
    tz = resolve_timezone(timezone_name)
    local = now_utc.astimezone(tz)
    nxt = advance_business_days(local.date(), 1, hours)
    return datetime.combine(
        nxt, time(hour=hours.start_hour), tzinfo=tz
    ).astimezone(UTC)
