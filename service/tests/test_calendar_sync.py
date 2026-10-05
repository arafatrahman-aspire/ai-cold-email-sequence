"""Calendar sync: bookings made through the link reach the database."""

from datetime import datetime, timedelta, timezone

import pytest

from app import repository as repo
from app.calendar.base import Attendee, CalendarError
from app.calendar.factory import use_calendar
from app.calendar.fake import FakeCalendar
from app.workers import calendar_sync

NOW = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def synced(monkeypatch):
    calls = []

    async def sync_meeting(*args):
        calls.append(args)
        return {"outcome": "new" if args[5] == "lead@x.com" else "ignored"}

    monkeypatch.setattr(repo, "sync_meeting", sync_meeting)
    yield calls
    use_calendar(None)


@pytest.mark.anyio
async def test_link_bookings_are_recorded(synced):
    cal = FakeCalendar("UTC")
    use_calendar(cal)
    slot = (await cal.free_slots(NOW, NOW + timedelta(days=2)))[0]
    await cal.book(slot.start, Attendee("Lea", "lead@x.com", "UTC"))
    await cal.book(slot.start + timedelta(hours=1), Attendee("Sam", "sam@z.com", "UTC"))

    stats = await calendar_sync.run_once(now=NOW)
    assert stats == {"new": 1, "ignored": 1}
    first = synced[0]
    assert first[0] == "fake" and first[2] == slot.start and first[4] == "booked"
    assert first[5:7] == ("lead@x.com", "Lea") and first[8] is True
    assert calendar_sync.last_run["seen"] == 2 and calendar_sync.last_run["provider"] == "fake"


@pytest.mark.anyio
async def test_no_calendar_or_a_broken_one_is_harmless(synced, monkeypatch):
    use_calendar(None)
    monkeypatch.setattr(calendar_sync, "get_calendar", lambda: None)
    assert await calendar_sync.run_once(now=NOW) == {}

    def broken():
        raise CalendarError("Cal.com needs CALCOM_API_KEY")

    monkeypatch.setattr(calendar_sync, "get_calendar", broken)
    assert await calendar_sync.run_once(now=NOW) == {"error": "Cal.com needs CALCOM_API_KEY"}
    assert not synced and calendar_sync.last_run["error"]
