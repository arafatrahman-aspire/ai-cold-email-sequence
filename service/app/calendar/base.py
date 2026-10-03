"""Calendar provider interface for meeting booking.

Reply triage needs three things from a calendar: free slots in a window, a
way to book one of them for a lead, and an optional public booking link the
lead can use instead. Each provider (Cal.com today; Google Calendar or
Microsoft Graph later) implements this class, and ``CALENDAR_PROVIDER``
picks one, the same way ``LLM_PROVIDER`` and ``MAIL_SENDER`` do.

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


class CalendarProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    async def free_slots(self, start: datetime, end: datetime) -> list[Slot]:
        """Bookable slots that start between ``start`` and ``end``, soonest first."""

    @abc.abstractmethod
    async def book(self, slot_start: datetime, attendee: Attendee, notes: str = "") -> Booking:
        """Book the slot starting at ``slot_start``.

        Raises ``CalendarError(slot_taken=True)`` if it is no longer free.
        """

    def booking_link(self) -> Optional[str]:
        """A page where the lead can pick a time themselves, if there is one."""
        return None

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
