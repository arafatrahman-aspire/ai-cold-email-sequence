"""Send dates for the 6 nurture emails.

Each email is due ``cadence[k]`` calendar days after day 0 (the enrollment,
moved into the sending window), at the same local time, moved into the
sending window in the lead's timezone. In test mode a day is
``minutes_per_day`` minutes and the window is ignored, so a 30-day sequence
runs in about half an hour. Pure functions: everything is passed in.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.scheduling import clamp_into_window, resolve_timezone
from app.settings_store import BusinessHours

UTC = timezone.utc


def lead_timezone(tz_name: Optional[str], default: str) -> str:
    """The lead's timezone if it is a real one, else the default."""
    if tz_name and resolve_timezone(tz_name).key == tz_name:
        return tz_name
    return default


def into_window(moment: datetime, tz_name: str, hours: BusinessHours) -> datetime:
    tz = resolve_timezone(tz_name)
    return clamp_into_window(moment.astimezone(tz), hours).astimezone(UTC)


def day_zero(now: datetime, tz_name: str, hours: BusinessHours, test_minutes: Optional[float]) -> datetime:
    """When email 1 goes out: now, or the next moment inside the window."""
    if test_minutes:
        return now
    return into_window(now, tz_name, hours)


def due_at(anchor: datetime, offset_days: float, tz_name: str, hours: BusinessHours,
           test_minutes: Optional[float]) -> datetime:
    if test_minutes:
        return anchor + timedelta(minutes=offset_days * test_minutes)
    tz = resolve_timezone(tz_name)
    local = anchor.astimezone(tz) + timedelta(days=offset_days)
    return clamp_into_window(local, hours).astimezone(UTC)


def min_gap(test_minutes: Optional[float]) -> timedelta:
    """At least a day (or a test-day) between two emails."""
    return timedelta(minutes=test_minutes) if test_minutes else timedelta(days=1)


def next_send(
    anchor: datetime,
    cadence: list[float],
    sent: int,
    tz_name: str,
    hours: BusinessHours,
    test_minutes: Optional[float],
    *,
    last_sent_at: Optional[datetime] = None,
    not_before: Optional[datetime] = None,
) -> Optional[datetime]:
    """When email ``sent + 1`` is due, or None after the last one.

    Never earlier than a day after the previous email, nor than
    ``not_before`` (used when a track switch recalculates the dates)."""
    if sent >= len(cadence):
        return None
    due = due_at(anchor, float(cadence[sent]), tz_name, hours, test_minutes)
    floor = []
    if last_sent_at is not None:
        floor.append(last_sent_at + min_gap(test_minutes))
    if not_before is not None:
        floor.append(not_before)
    if floor and due < max(floor):
        due = max(floor) if test_minutes else into_window(max(floor), tz_name, hours)
    return due


def generation_horizon(now: datetime, hours_ahead: float, test_minutes: Optional[float]) -> datetime:
    """Emails due before this moment are written now (about a day ahead)."""
    if test_minutes:
        return now + timedelta(minutes=test_minutes * hours_ahead / 24)
    return now + timedelta(hours=hours_ahead)
