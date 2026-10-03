"""An in-memory calendar for tests and for trying triage without Cal.com.

It offers 30-minute slots at 10:00, 11:00, 14:00 and 15:00 on weekdays in
its own timezone, and remembers bookings for as long as the process lives.
Set CALENDAR_PROVIDER=fake to use it end to end; nothing leaves the machine.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app.calendar.base import Attendee, Booking, CalendarError, CalendarProvider, Slot

SLOT_HOURS = (10, 11, 14, 15)
SLOT_MINUTES = 30


class FakeCalendar(CalendarProvider):
    name = "fake"

    def __init__(self, timezone: str = "UTC", booking_url: str = "") -> None:
        self._tz = ZoneInfo(timezone)
        self._booking_url = booking_url or None
        self.bookings: dict[datetime, Booking] = {}
        self.attendees: dict[str, Attendee] = {}

    def booking_link(self) -> Optional[str]:
        return self._booking_url

    def _all_slots(self, start: datetime, end: datetime) -> list[Slot]:
        out = []
        day = start.astimezone(self._tz).date()
        last = end.astimezone(self._tz).date()
        while day <= last:
            if day.weekday() < 5:
                for h in SLOT_HOURS:
                    s = datetime.combine(day, time(h), tzinfo=self._tz)
                    if start <= s <= end:
                        out.append(Slot(start=s, end=s + timedelta(minutes=SLOT_MINUTES)))
            day += timedelta(days=1)
        return out

    async def free_slots(self, start: datetime, end: datetime) -> list[Slot]:
        return [s for s in self._all_slots(start, end) if s.start not in self.bookings]

    async def book(self, slot_start: datetime, attendee: Attendee, notes: str = "") -> Booking:
        valid = {s.start for s in self._all_slots(slot_start - timedelta(minutes=1), slot_start + timedelta(minutes=1))}
        if slot_start not in valid:
            raise CalendarError(f"{slot_start.isoformat()} is not a bookable slot", slot_taken=True)
        if slot_start in self.bookings:
            raise CalendarError(f"{slot_start.isoformat()} is already booked", slot_taken=True)
        booking = Booking(
            provider=self.name,
            external_id=f"fake-{uuid.uuid4().hex[:12]}",
            start=slot_start,
            end=slot_start + timedelta(minutes=SLOT_MINUTES),
            meeting_url="https://meet.example.com/fake",
        )
        self.bookings[slot_start] = booking
        self.attendees[booking.external_id] = attendee
        return booking
