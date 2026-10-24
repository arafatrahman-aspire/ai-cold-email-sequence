"""Calendar providers: a contract every provider must meet, Cal.com's exact
API usage (against a mocked HTTP layer), and slot selection.

A new provider (Google) is checked by adding it to PROVIDERS.
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.calendar.base import Attendee, CalendarError, Slot
from app.calendar.calcom import CalComProvider
from app.calendar.fake import FakeCalendar
from app.calendar.msbookings import MSBookingsProvider, discover, parse_duration
from app.calendar.slots import describe_slot, find_slot, pick_near, pick_slots
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
        self.listed: list[dict] = []  # what GET /v2/bookings returns

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
        if request.url.path == "/v2/bookings" and request.method == "GET":
            after = datetime.fromisoformat(request.url.params["afterStart"].replace("Z", "+00:00"))
            data = [b for b in self.listed if datetime.fromisoformat(b["start"].replace("Z", "+00:00")) >= after]
            return httpx.Response(200, json={"status": "success", "data": data,
                                             "pagination": {"nextCursor": None, "hasMore": False}})
        if request.url.path == "/v2/bookings":
            body = json.loads(request.content)
            start = datetime.fromisoformat(body["start"].replace("Z", "+00:00"))
            if start.isoformat() in self.booked:
                return httpx.Response(400, json={"status": "error", "error": {"message": "User either already has booking at this time or is not available"}})
            self.booked.add(start.isoformat())
            self.listed.append({
                "uid": f"bk-{len(self.booked)}", "start": body["start"],
                "end": (start + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
                "status": "accepted", "eventTypeId": body.get("eventTypeId"),
                "attendees": [{"email": body["attendee"]["email"], "name": body["attendee"]["name"]}],
                "location": "https://cal.video/abc"})
            return httpx.Response(201, json={"status": "success", "data": {
                "id": 1, "uid": f"bk-{len(self.booked)}", "start": body["start"],
                "end": (start + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
                "status": "accepted", "meetingUrl": "https://cal.video/abc"}})
        return httpx.Response(404)


def calcom(server=None):
    server = server or FakeCalCom()
    provider = CalComProvider(api_key="cal_live_test", event_type=42, booking_url="https://cal.com/alex/30min",
                              transport=httpx.MockTransport(server))
    return provider, server


# --- a mocked Microsoft Graph (Bookings) ------------------------------------------

BIZ = "Sales@contoso.onmicrosoft.com"
SERVICE = "svc-1"


class FakeGraph:
    """Answers the Bookings endpoints the way Graph does. Every staff member
    works 10:00-11:00 and 14:00-14:30 UTC on weekdays; their appointments
    are busy. Like Graph, appointments are not checked for clashes."""

    def __init__(self, staff=("s1",)):
        self.staff = list(staff)
        self.requests: list[httpx.Request] = []
        self.appointments: list[dict] = []
        self.tokens = 0

    @staticmethod
    def _t(data):
        return datetime.fromisoformat(data["dateTime"]).replace(tzinfo=timezone.utc)

    def _free(self, sid, start, end):
        out = []
        day = start.replace(hour=0, minute=0, second=0)
        while day < end:
            if day.weekday() < 5:
                for a, b in ((10, 11), (14, 14.5)):
                    windows = [(day + timedelta(hours=a), day + timedelta(hours=b))]
                    for appt in self.appointments:
                        if sid in appt["staffMemberIds"]:
                            s, e = self._t(appt["start"]), self._t(appt["end"])
                            windows = [w for ws, we in windows for w in
                                       ((ws, min(we, s)), (max(ws, e), we)) if w[1] > w[0]]
                    out += [(max(ws, start), min(we, end)) for ws, we in windows if we > start and ws < end]
            day += timedelta(days=1)
        return [{"status": "available", "serviceId": "",
                 "startDateTime": {"dateTime": s.replace(tzinfo=None).isoformat(), "timeZone": "UTC"},
                 "endDateTime": {"dateTime": e.replace(tzinfo=None).isoformat(), "timeZone": "UTC"}}
                for s, e in out]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "login.microsoftonline.com":
            self.tokens += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        base = f"/v1.0/solutions/bookingBusinesses/{BIZ}"
        if path == "/v1.0/solutions/bookingBusinesses":
            return httpx.Response(200, json={"value": [{"id": BIZ, "displayName": "Sales",
                                                        "publicUrl": "https://outlook.office365.com/book/Sales@contoso.onmicrosoft.com/"}]})
        if path == f"{base}/services/{SERVICE}":
            return httpx.Response(200, json={
                "id": SERVICE, "displayName": "Intro call", "defaultDuration": "PT30M",
                "preBuffer": "PT0S", "postBuffer": "PT0S", "isLocationOnline": True,
                "staffMemberIds": self.staff, "webUrl": "https://outlook.office365.com/book/Sales/s/svc-1",
                "schedulingPolicy": {"timeSlotInterval": "PT30M", "minimumLeadTime": "PT0S",
                                     "maximumAdvance": "P365D"}})
        if path == f"{base}/services":
            return httpx.Response(200, json={"value": [{"id": SERVICE, "displayName": "Intro call",
                                                        "defaultDuration": "PT30M", "staffMemberIds": self.staff}]})
        if path == f"{base}/staffMembers":
            return httpx.Response(200, json={"value": [
                {"id": s, "displayName": f"Staff {s}", "emailAddress": f"{s}@contoso.com", "role": "member"}
                for s in self.staff]})
        if path == f"{base}/getStaffAvailability":
            body = json.loads(request.content)
            start, end = self._t(body["startDateTime"]), self._t(body["endDateTime"])
            return httpx.Response(200, json={"value": [
                {"staffId": s, "availabilityItems": self._free(s, start, end)} for s in body["staffIds"]]})
        if path == f"{base}/appointments" and request.method == "POST":
            body = json.loads(request.content)
            appt = {**body, "id": f"appt-{len(self.appointments) + 1}",
                    "createdDateTime": f"2026-10-01T00:00:{len(self.appointments):02d}Z",
                    "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/x"}
            self.appointments.append(appt)
            return httpx.Response(201, json=appt)
        if path == f"{base}/calendarView":
            start = datetime.fromisoformat(request.url.params["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(request.url.params["end"].replace("Z", "+00:00"))
            return httpx.Response(200, json={"value": [
                a for a in self.appointments if start <= self._t(a["start"]) < end]})
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": path}})


def msbookings(server=None, **kwargs):
    server = server or FakeGraph()
    provider = MSBookingsProvider("tenant", "client", "secret", BIZ, SERVICE,
                                  transport=httpx.MockTransport(server), **kwargs)
    return provider, server


PROVIDERS = {
    "fake": lambda: FakeCalendar("UTC", "https://cal.example.com/demo"),
    "calcom": lambda: calcom()[0],
    "microsoft": lambda: msbookings()[0],
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

    # The booking can be read back (this is how link bookings are found).
    [seen] = await cal.list_bookings(MON)
    assert (seen.external_id, seen.start, seen.status) == (booking.external_id, first.start, "booked")
    assert (seen.attendee_email, seen.attendee_name, seen.ours) == (DANA.email, DANA.name, True)
    assert await cal.list_bookings(first.start + timedelta(minutes=1)) == []


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


# --- configuration --------------------------------------------------------------

@pytest.mark.anyio
async def test_calcom_by_slug_uses_username_from_booking_url():
    server = FakeCalCom()
    cal = CalComProvider(api_key="k", event_type="ai-cold-email-init",
                         booking_url="https://cal.com/arafat/ai-cold-email-init",
                         transport=httpx.MockTransport(server))
    await cal.free_slots(MON, MON + timedelta(days=1))
    params = dict(server.requests[-1].url.params)
    assert (params["eventTypeSlug"], params["username"]) == ("ai-cold-email-init", "arafat")
    assert "eventTypeId" not in params
    await cal.book(MON + timedelta(hours=10), DANA)
    body = json.loads(server.requests[-1].content)
    assert (body["eventTypeSlug"], body["username"]) == ("ai-cold-email-init", "arafat")


def test_calcom_slug_without_username_is_a_clear_error():
    with pytest.raises(CalendarError, match="CALCOM_USERNAME"):
        CalComProvider(api_key="k", event_type="ai-cold-email-init")
    assert CalComProvider(api_key="k", event_type="30min", username="arafat")._event == {
        "eventTypeSlug": "30min", "username": "arafat"}
    assert CalComProvider(api_key="k", event_type=" 42 ")._event == {"eventTypeId": 42}


def test_bad_calendar_settings_never_stop_the_app(monkeypatch):
    """Empty or non-numeric values used to crash Settings at startup."""
    from app.config import Settings
    for value in ("", "ai-cold-email-init", "1234567"):
        monkeypatch.setenv("CALCOM_EVENT_TYPE_ID", value)
        monkeypatch.setenv("CALCOM_BOOKING_URL", "")
        s = Settings()
        assert s.calcom_event_type_id == value
        assert s.calcom_booking_url == ""


@pytest.mark.anyio
async def test_calcom_booking_list_request_statuses_and_paging():
    pages = [
        {"data": [
            {"uid": "a", "start": "2026-10-06T03:15:00.000Z", "end": "2026-10-06T03:30:00.000Z",
             "status": "accepted", "eventTypeId": 42, "location": "https://app.cal.com/video/a",
             "attendees": [{"email": "lead@x.com", "name": "Lea", "timeZone": "Asia/Dhaka"}]},
            {"uid": "b", "start": "2026-10-07T03:00:00.000Z", "end": "2026-10-07T03:15:00.000Z",
             "status": "awaiting_host", "eventTypeId": 99, "location": "integrations:daily",
             "attendees": [{"email": "other@y.com", "name": "O"}]},
            {"uid": "no-start"},
        ], "pagination": {"nextCursor": "c2", "hasMore": True}},
        {"data": [
            {"uid": "c", "start": "2026-10-08T03:00:00Z", "end": "2026-10-08T03:15:00Z",
             "status": "cancelled", "eventTypeId": 42, "attendees": []},
        ], "pagination": {"nextCursor": None, "hasMore": False}},
    ]
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "success", **pages[len(seen) - 1]})

    cal = CalComProvider("k", 42, transport=httpx.MockTransport(handler))
    got = await cal.list_bookings(MON)
    assert seen[0].headers["cal-api-version"] == "2026-05-01"
    assert dict(seen[0].url.params) == {"afterStart": "2026-10-05T00:00:00Z", "sortStart": "asc", "limit": "100"}
    assert seen[1].url.params["cursor"] == "c2"
    assert [(b.external_id, b.status, b.ours) for b in got] == [
        ("a", "booked", True), ("b", "pending", False), ("c", "cancelled", True)]
    assert (got[0].attendee_email, got[0].meeting_url) == ("lead@x.com", "https://app.cal.com/video/a")
    assert got[1].meeting_url is None and got[2].attendee_email is None


@pytest.mark.anyio
async def test_a_booking_in_the_past_counts_as_taken():
    cal = CalComProvider("k", 1, transport=httpx.MockTransport(lambda r: httpx.Response(
        400, json={"error": {"message": "Attempting to book a meeting in the past."}})))
    with pytest.raises(CalendarError) as err:
        await cal.book(MON, DANA)
    assert err.value.slot_taken


def test_pick_near_prefers_the_same_day_and_closest_time():
    slots = _slots(10, 14, 15) + _slots(16, day_offset=1)
    wanted = MON + timedelta(hours=17)
    near = pick_near(slots, wanted, "UTC", HOURS, MON - timedelta(days=1), count=2, min_notice_hours=0)
    assert [s.start.hour for s in near] == [14, 15]


# --- Microsoft Bookings specifics ---------------------------------------------------

@pytest.mark.anyio
async def test_msbookings_round_robin_fills_a_slot_once_per_staff_member():
    cal, server = msbookings(FakeGraph(staff=("s1", "s2", "s3", "s4")))
    [first, *_] = await cal.free_slots(MON, MON + timedelta(days=1))
    assert first.start == MON + timedelta(hours=10) and first.end - first.start == timedelta(minutes=30)

    # Four leads can take the same time, one per staff member, in order.
    for i in range(4):
        await cal.book(first.start, Attendee(f"Lead {i}", f"lead{i}@x.com", "UTC"))
    assert [a["staffMemberIds"] for a in server.appointments] == [["s1"], ["s2"], ["s3"], ["s4"]]
    assert first.start not in [s.start for s in await cal.free_slots(MON, MON + timedelta(days=1))]
    with pytest.raises(CalendarError) as err:
        await cal.book(first.start, DANA)
    assert err.value.slot_taken

    # The rotation carries on from the last assignment, even in a new process
    # (it is read back from the bookings), and skips whoever is busy.
    fresh, _ = msbookings(server)
    await fresh.book(MON + timedelta(hours=10, minutes=30), DANA)
    assert server.appointments[-1]["staffMemberIds"] == ["s1"]
    await fresh.book(MON + timedelta(hours=10, minutes=30), DANA)
    assert server.appointments[-1]["staffMemberIds"] == ["s2"]


@pytest.mark.anyio
async def test_msbookings_requests_match_the_api():
    cal, server = msbookings(staff_ids=["s1"])
    booking = await cal.book(MON + timedelta(hours=14), DANA, "Booked from a reply")
    assert booking.meeting_url.startswith("https://teams.microsoft.com/")
    assert booking.end - booking.start == timedelta(minutes=30)

    post = next(r for r in server.requests if r.url.path.endswith("/appointments"))
    body = json.loads(post.content)
    assert body["serviceId"] == SERVICE and body["staffMemberIds"] == ["s1"]
    assert body["start"] == {"dateTime": "2026-10-05T14:00:00", "timeZone": "UTC"}
    assert body["isLocationOnline"] is True
    [customer] = body["customers"]
    assert (customer["name"], customer["emailAddress"], customer["timeZone"]) == (DANA.name, DANA.email, DANA.timezone)
    assert server.tokens == 1  # the token is reused
    assert cal.booking_link() == "https://outlook.office365.com/book/Sales/s/svc-1"
    assert msbookings(booking_url="https://book.example.com")[0].booking_link() == "https://book.example.com"


@pytest.mark.anyio
async def test_msbookings_errors_are_calendar_errors():
    def forbidden(request):
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(403, json={"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}})

    cal = MSBookingsProvider("t", "c", "s", BIZ, SERVICE, transport=httpx.MockTransport(forbidden))
    with pytest.raises(CalendarError, match="Bookings.Read.All"):
        await cal.free_slots(MON, MON + timedelta(days=1))
    with pytest.raises(CalendarError, match="MSBOOKINGS_BUSINESS_ID"):
        MSBookingsProvider("t", "c", "s", "", "")
    with pytest.raises(CalendarError, match="GRAPH_TENANT_ID"):
        MSBookingsProvider("", "", "", BIZ, SERVICE)


@pytest.mark.anyio
async def test_msbookings_discover_lists_businesses_services_and_staff():
    server = FakeGraph(staff=("s1", "s2"))
    [biz] = await discover("t", "c", "s", transport=httpx.MockTransport(server))
    assert biz["business_id"] == BIZ
    assert biz["services"][0]["id"] == SERVICE and biz["services"][0]["staff_ids"] == ["s1", "s2"]
    assert [m["email"] for m in biz["staff"]] == ["s1@contoso.com", "s2@contoso.com"]


def test_iso_durations():
    assert parse_duration("PT30M") == timedelta(minutes=30)
    assert parse_duration("P1DT2H") == timedelta(days=1, hours=2)
    assert parse_duration("PT0S") == timedelta(0) == parse_duration(None) == parse_duration("junk")
