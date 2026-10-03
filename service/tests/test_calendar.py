"""Calendar providers: a contract every provider must meet, Cal.com's exact
API usage (against a mocked HTTP layer), and slot selection.

A new provider (Google, Microsoft) is checked by adding it to PROVIDERS.
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.calendar.base import Attendee, CalendarError, Slot
from app.calendar.calcom import CalComProvider
from app.calendar.fake import FakeCalendar
from app.calendar.slots import describe_slot, find_slot, pick_slots
from app.settings_store import BusinessHours

MON = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)  # a Monday
HOURS = BusinessHours(start_hour=9, end_hour=17, weekdays=(0, 1, 2, 3, 4))
DANA = Attendee(name="Dana Okafor", email="dana@northwind.com", timezone="Europe/London")


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- a mocked Cal.com --------------------------------------------------------

class FakeCalCom:
    """Answers /v2/slots and /v2/bookings the way Cal.com does."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.booked: set[str] = set()

    def slots(self):
        out = {}
        for d in range(5):
            day = MON + timedelta(days=d)
            out[day.date().isoformat()] = [
                {"start": (day + timedelta(hours=h)).isoformat().replace("+00:00", ".000Z"),
                 "end": (day + timedelta(hours=h, minutes=30)).isoformat().replace("+00:00", ".000Z")}
                for h in (10, 14) if (day + timedelta(hours=h)).isoformat() not in self.booked
            ]
        return out

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v2/slots":
            return httpx.Response(200, json={"status": "success", "data": self.slots()})
        if request.url.path == "/v2/bookings":
            body = json.loads(request.content)
            start = datetime.fromisoformat(body["start"].replace("Z", "+00:00"))
            if start.isoformat() in self.booked:
                return httpx.Response(400, json={"status": "error", "error": {"message": "User either already has booking at this time or is not available"}})
            self.booked.add(start.isoformat())
            return httpx.Response(201, json={"status": "success", "data": {
                "id": 1, "uid": f"bk-{len(self.booked)}", "start": body["start"],
                "end": (start + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
                "status": "accepted", "meetingUrl": "https://cal.video/abc"}})
        return httpx.Response(404)


def calcom(server=None):
    server = server or FakeCalCom()
    provider = CalComProvider(api_key="cal_live_test", event_type_id=42, booking_url="https://cal.com/alex/30min",
                              transport=httpx.MockTransport(server))
    return provider, server


PROVIDERS = {
    "fake": lambda: FakeCalendar("UTC", "https://cal.example.com/demo"),
    "calcom": lambda: calcom()[0],
}


# --- the contract -------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("name", PROVIDERS)
async def test_contract_slots_book_and_conflict(name):
    cal = PROVIDERS[name]()
    slots = await cal.free_slots(MON, MON + timedelta(days=5))
    assert slots, "a provider must return free slots"
    assert all(s.start.tzinfo is not None and s.end > s.start for s in slots)
    assert slots == sorted(slots, key=lambda s: s.start)
    assert all(MON <= s.start <= MON + timedelta(days=5) for s in slots)

    first = slots[0]
    booking = await cal.book(first.start, DANA)
    assert booking.provider == cal.name and booking.external_id
    assert booking.start == first.start and booking.end > booking.start

    # Booking the same slot again is reported as taken, not as a crash.
    with pytest.raises(CalendarError) as err:
        await cal.book(first.start, DANA)
    assert err.value.slot_taken
    assert first.start not in [s.start for s in await cal.free_slots(MON, MON + timedelta(days=5))]
    assert cal.booking_link()


# --- Cal.com specifics --------------------------------------------------------------

@pytest.mark.anyio
async def test_calcom_requests_match_the_api():
    cal, server = calcom()
    await cal.free_slots(MON, MON + timedelta(days=2))
    req = server.requests[-1]
    assert req.headers["authorization"] == "Bearer cal_live_test"
    assert req.headers["cal-api-version"] == "2024-09-04"
    assert dict(req.url.params) == {
        "eventTypeId": "42", "start": "2026-10-05T00:00:00Z", "end": "2026-10-07T00:00:00Z",
        "timeZone": "UTC", "format": "range",
    }

    b = await cal.book(MON + timedelta(hours=10), DANA, notes="from a reply")
    req = server.requests[-1]
    assert req.method == "POST" and req.headers["cal-api-version"] == "2026-02-25"
    body = json.loads(req.content)
    assert body["start"] == "2026-10-05T10:00:00Z" and body["eventTypeId"] == 42
    assert body["attendee"] == {"name": "Dana Okafor", "email": "dana@northwind.com", "timeZone": "Europe/London"}
    assert (b.external_id, b.meeting_url) == ("bk-1", "https://cal.video/abc")


@pytest.mark.anyio
async def test_calcom_errors_are_calendar_errors():
    cal = CalComProvider("k", 1, transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": "Invalid API key"}})))
    with pytest.raises(CalendarError, match="Invalid API key") as err:
        await cal.free_slots(MON, MON + timedelta(days=1))
    assert not err.value.slot_taken
    with pytest.raises(CalendarError):
        CalComProvider("", 0)


# --- choosing slots ------------------------------------------------------------------

def _slots(*hours_utc, day_offset=0):
    return [Slot(MON + timedelta(days=day_offset, hours=h), MON + timedelta(days=day_offset, hours=h, minutes=30))
            for h in hours_utc]


def test_pick_prefers_different_days_and_respects_notice():
    slots = _slots(10, 14) + _slots(10, 14, day_offset=1) + _slots(10, day_offset=2)
    now = MON + timedelta(hours=8)
    picked = pick_slots(slots, "UTC", HOURS, now, count=2, min_notice_hours=12)
    # Monday is inside the 12h notice; Tuesday and Wednesday are chosen.
    assert [s.start for s in picked] == [MON + timedelta(days=1, hours=10), MON + timedelta(days=2, hours=10)]


def test_pick_stays_inside_the_leads_business_hours():
    # 03:00 UTC is 09:00 in Dhaka; 14:00 UTC is 20:00 there.
    slots = _slots(3, 14, day_offset=1)
    picked = pick_slots(slots, "Asia/Dhaka", BusinessHours(9, 17, (6, 0, 1, 2, 3)), MON, count=2, min_notice_hours=0)
    assert [s.start.hour for s in picked] == [3]


def test_describe_and_find_slot():
    slot = _slots(10, day_offset=1)[0]
    assert describe_slot(slot, "Europe/London") == "Tuesday 6 October, 11:00-11:30 (Europe/London)"
    assert find_slot([slot], slot.start + timedelta(seconds=30)) == slot
    assert find_slot([slot], slot.start + timedelta(minutes=30)) is None
