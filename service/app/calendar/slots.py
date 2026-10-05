"""Choosing which slots to offer a lead, and writing them out for them.

Provider-independent: the calendar says what is free; this decides what is
sensible to offer. Slots must start after a minimum notice, fall inside the
lead's own business hours (so nobody is offered 3am), and are spread over
different days when possible.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from app.calendar.base import Slot
from app.scheduling import resolve_timezone
from app.settings_store import BusinessHours


def _fits(slot: Slot, tz, hours: BusinessHours) -> bool:
    local_start = slot.start.astimezone(tz)
    local_end = slot.end.astimezone(tz)
    if local_start.weekday() not in hours.weekdays:
        return False
    if local_start.hour < hours.start_hour:
        return False
    end_hour = local_end.hour + (local_end.minute > 0) if local_end.date() == local_start.date() else 24
    return end_hour <= hours.end_hour


def pick_slots(
    slots: list[Slot],
    lead_timezone: Optional[str],
    hours: BusinessHours,
    now: datetime,
    count: int = 2,
    min_notice_hours: int = 12,
) -> list[Slot]:
    """Up to ``count`` slots to offer, on different days where possible."""
    tz = resolve_timezone(lead_timezone)
    earliest = now + timedelta(hours=min_notice_hours)
    usable = sorted(
        (s for s in slots if s.start >= earliest and _fits(s, tz, hours)),
        key=lambda s: s.start,
    )
    chosen: list[Slot] = []
    seen_days: set = set()
    for s in usable:
        day = s.start.astimezone(tz).date()
        if day not in seen_days:
            chosen.append(s)
            seen_days.add(day)
        if len(chosen) == count:
            return chosen
    # Not enough distinct days: fill with the earliest remaining slots.
    for s in usable:
        if s not in chosen:
            chosen.append(s)
        if len(chosen) == count:
            break
    return sorted(chosen, key=lambda s: s.start)


def pick_near(
    slots: list[Slot],
    around: datetime,
    lead_timezone: Optional[str],
    hours: BusinessHours,
    now: datetime,
    count: int = 2,
    min_notice_hours: int = 12,
) -> list[Slot]:
    """Up to ``count`` slots closest to the time the lead asked for, the same
    day first ("Tuesday 5pm" is not free -> Tuesday 4pm, not next Friday)."""
    tz = resolve_timezone(lead_timezone)
    earliest = now + timedelta(hours=min_notice_hours)
    wanted_day = around.astimezone(tz).date()
    usable = [s for s in slots if s.start >= earliest and _fits(s, tz, hours)]
    usable.sort(key=lambda s: (s.start.astimezone(tz).date() != wanted_day, abs(s.start - around)))
    return sorted(usable[:count], key=lambda s: s.start)


def describe_slot(slot: Slot, lead_timezone: Optional[str]) -> str:
    """'Tuesday 6 October, 10:00-10:30 (Asia/Dhaka)' in the lead's time."""
    tz = resolve_timezone(lead_timezone)
    s = slot.start.astimezone(tz)
    e = slot.end.astimezone(tz)
    label = getattr(tz, "key", "UTC").replace("_", " ")
    return f"{s:%A} {s.day} {s:%B}, {s:%H:%M}-{e:%H:%M} ({label})"


def find_slot(slots: list[Slot], start: datetime, tolerance_minutes: int = 1) -> Optional[Slot]:
    """The slot starting at ``start`` (within a minute), if it is in the list."""
    for s in slots:
        if abs((s.start - start).total_seconds()) <= tolerance_minutes * 60:
            return s
    return None
