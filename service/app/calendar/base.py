"""Calendar provider interface for meeting booking.

Reply triage needs four things from a calendar: free slots in a window, a
way to book one of them for a lead, an optional public booking link the lead
can use instead, and the bookings themselves, so a meeting booked through
that link is recorded too. Each provider (Cal.com, Microsoft Bookings; Google
Calendar later) implements this class, and ``CALENDAR_PROVIDER`` picks one,
the same way ``LLM_PROVIDER`` and ``MAIL_SENDER`` do. Nothing outside
app/calendar/ knows which provider is in use.

To add a provider: subclass ``CalendarProvider`` in a new module, then add
one branch to ``app.calendar.factory._build``. Nothing else changes; the
contract tests in tests/test_calendar.py can be run against it.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class CalendarError(RuntimeError):
    """A provider call failed. ``slot_taken`` marks a booking race."""

    def __init__(self, message: str, *, slot_taken: bool = False) -> None:
        super().__init__(message)
        self.slot_taken = slot_taken


@dataclass(frozen=True)
class Slot:
    start: datetime  # timezone-aware
    end: datetime

    def to_json(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    @staticmethod
    def from_json(data: dict) -> "Slot":
        return Slot(
            start=datetime.fromisoformat(data["start"]),
            end=datetime.fromisoformat(data["end"]),
        )


@dataclass(frozen=True)
class Attendee:
    name: str
    email: str
    timezone: str


@dataclass(frozen=True)
class Booking:
    provider: str
    external_id: str
    start: datetime
    end: datetime
    meeting_url: Optional[str] = None


@dataclass(frozen=True)
class RemoteBooking:
    """A booking as the calendar reports it, however it was made."""

    external_id: str
    start: datetime
    end: datetime
    status: str  # booked | pending (awaiting the host's confirmation) | cancelled
    attendee_email: Optional[str] = None
    attendee_name: Optional[str] = None
    meeting_url: Optional[str] = None
    ours: bool = False  # for the event type this app offers


class CalendarProvider(abc.ABC):
    name: str
    # True when the provider deletes a cancelled booking instead of listing it
    # as cancelled: the calendar sync then cancels known meetings it no longer sees.
    missing_means_cancelled: bool = False

    @abc.abstractmethod
    async def free_slots(self, start: datetime, end: datetime) -> list[Slot]:
        """Bookable slots that start between ``start`` and ``end``, soonest first."""

    @abc.abstractmethod
    async def book(self, slot_start: datetime, attendee: Attendee, notes: str = "") -> Booking:
        """Book the slot starting at ``slot_start``.

        Raises ``CalendarError(slot_taken=True)`` if it is no longer free.
        """

    async def reschedule(self, external_id: str, new_start: datetime, attendee: Attendee,
                         notes: str = "") -> Booking:
        """Move the booking ``external_id`` to the slot starting at ``new_start``.

        The returned Booking may carry a new external_id (a provider that
        cancels and rebooks). Raises ``CalendarError(slot_taken=True)`` if the
        new time is not free; the old booking then stays as it was.
        """
        raise CalendarError(f"{self.name} cannot move bookings; a person should reschedule it")

    def booking_link(self) -> Optional[str]:
        """A page where the lead can pick a time themselves, if there is one."""
        return None

    async def list_bookings(self, starting_after: datetime) -> list[RemoteBooking]:
        """Bookings (including cancelled ones) that start after ``starting_after``.

        A provider without a way to read bookings back returns [], and only
        meetings booked from replies are recorded.
        """
        return []

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
