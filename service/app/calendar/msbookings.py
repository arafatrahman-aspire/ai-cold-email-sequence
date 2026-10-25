"""Microsoft Bookings (Microsoft Graph) calendar provider, round robin.

One Bookings *service* (e.g. "30 min intro call") is offered, served by a
team of staff members in turn. A time is offered while at least one of them
is free, so with four staff up to four leads can hold the same slot; each
booking goes to the next free staff member in round-robin order.

Graph calls (application permissions, admin-consented on the Entra app):

* ``POST /solutions/bookingBusinesses/{id}/getStaffAvailability`` -- each
  staff member's free / busy windows (``Bookings.Read.All``). The windows are
  cut into slots of the service's length, on the service's time-slot interval.
* ``POST .../appointments`` -- the booking (``BookingsAppointment.ReadWrite.All``).
  Bookings sends the invite and, for an online service, the Teams link. Graph
  does not check availability itself, so the slot is checked just before.
* ``GET .../calendarView`` -- bookings read back, including ones the lead made
  on the public booking page. Bookings deletes a cancelled appointment rather
  than marking it, so the calendar sync treats a known one that is no longer
  listed as cancelled (``missing_means_cancelled``).
* ``GET .../services/{id}`` and ``GET /solutions/bookingBusinesses/{id}`` --
  the service's length, buffers, staff, scheduling policy and booking page.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.calendar.base import Attendee, Booking, CalendarError, CalendarProvider, RemoteBooking, Slot

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_BASE = "https://login.microsoftonline.com"

# How far ahead bookings are read, and how far back the round robin looks for
# the most recent assignment.
LIST_HORIZON = timedelta(days=365)
ROTATION_LOOKBACK = timedelta(days=60)
SERVICE_TTL_SECONDS = 600

_DURATION = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$"
)


def parse_duration(value: Optional[str]) -> timedelta:
    """ISO 8601 duration as Graph writes it ('PT30M', 'P1DT2H') -> timedelta."""
    match = _DURATION.match((value or "").strip())
    if not match:
        return timedelta(0)
    parts = {k: float(v) for k, v in match.groupdict().items() if v}
    return timedelta(days=parts.get("d", 0), hours=parts.get("h", 0),
                     minutes=parts.get("m", 0), seconds=parts.get("s", 0))


def _utc_naive(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _graph_time(data: Optional[dict[str, Any]], fallback_tz) -> Optional[datetime]:
    """A Graph dateTimeTimeZone -> aware datetime.

    Graph may name the zone in IANA form, as UTC, or as a Windows display name
    ("(UTC-08:00) Pacific Time (US & Canada)"); the last one cannot be mapped
    reliably, so it falls back to the business timezone (MSBOOKINGS_TIMEZONE).
    """
    if not data or not data.get("dateTime"):
        return None
    raw = str(data["dateTime"]).replace("Z", "+00:00")
    raw = re.sub(r"(\.\d{6})\d+", r"\1", raw)  # Graph writes 7 fractional digits
    value = datetime.fromisoformat(raw)
    if value.tzinfo is not None:
        return value
    name = str(data.get("timeZone") or "").strip()
    if name.upper() in ("UTC", "Z", "GMT", "COORDINATED UNIVERSAL TIME") or name.startswith("(UTC)"):
        return value.replace(tzinfo=timezone.utc)
    try:
        return value.replace(tzinfo=ZoneInfo(name))
    except (ZoneInfoNotFoundError, ValueError):
        return value.replace(tzinfo=fallback_tz)


def _merge(windows: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for start, end in sorted(windows):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


class _Service:
    """What the provider needs to know about the Bookings service."""

    def __init__(self, data: dict[str, Any], business: dict[str, Any]) -> None:
        policy = data.get("schedulingPolicy") or {}
        self.name = data.get("displayName") or ""
        self.duration = parse_duration(data.get("defaultDuration")) or timedelta(minutes=30)
        self.pre = parse_duration(data.get("preBuffer"))
        self.post = parse_duration(data.get("postBuffer"))
        self.interval = parse_duration(policy.get("timeSlotInterval")) or self.duration
        self.lead_time = parse_duration(policy.get("minimumLeadTime"))
        self.max_advance = parse_duration(policy.get("maximumAdvance"))
        self.online = bool(data.get("isLocationOnline"))
        self.staff_ids = [str(s) for s in data.get("staffMemberIds") or []]
        self.url = data.get("webUrl") or business.get("publicUrl") or None


class MSBookingsProvider(CalendarProvider):
    name = "microsoft"
    missing_means_cancelled = True

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        business_id: str,
        service_id: str,
        staff_ids: Iterable[str] = (),
        booking_url: str = "",
        business_timezone: str = "UTC",
        timeout: float = 20.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        if not (tenant_id and client_id and client_secret):
            raise CalendarError(
                "Microsoft Bookings needs GRAPH_TENANT_ID, GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET"
            )
        if not (business_id.strip() and service_id.strip()):
            raise CalendarError(
                "Microsoft Bookings needs MSBOOKINGS_BUSINESS_ID and MSBOOKINGS_SERVICE_ID "
                "(GET /calendar/microsoft/discover lists them)"
            )
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._business = quote(business_id.strip(), safe="@.")
        self._service_id = service_id.strip()
        self._staff_override = [s.strip() for s in staff_ids if s and s.strip()]
        self._booking_url = booking_url.strip() or None
        try:
            self._tz = ZoneInfo(business_timezone or "UTC")
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CalendarError(f"MSBOOKINGS_TIMEZONE={business_timezone!r} is not a timezone") from exc
        self._client = httpx.AsyncClient(base_url=GRAPH_BASE, timeout=timeout, transport=transport)
        self._token: Optional[str] = None
        self._token_expires = 0.0
        self._service: Optional[_Service] = None
        self._service_loaded = 0.0
        # Round robin: the staff member who got the last booking from here.
        self._last_staff: Optional[str] = None

    # --- plumbing -----------------------------------------------------------

    async def _auth(self) -> dict[str, str]:
        if not self._token or time.monotonic() >= self._token_expires:
            try:
                resp = await self._client.post(
                    f"{LOGIN_BASE}/{self._tenant_id}/oauth2/v2.0/token",
                    data={"client_id": self._client_id, "client_secret": self._client_secret,
                          "scope": "https://graph.microsoft.com/.default",
                          "grant_type": "client_credentials"},
                )
            except httpx.HTTPError as exc:
                raise CalendarError(f"Microsoft sign-in unreachable: {exc}") from exc
            if resp.status_code >= 400:
                raise CalendarError(f"Microsoft sign-in failed ({resp.status_code}): {resp.text[:300]}")
            data = resp.json()
            self._token = data["access_token"]
            self._token_expires = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return {"Authorization": f"Bearer {self._token}"}

    async def _call(self, method: str, path: str, what: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = await self._client.request(method, path, headers=await self._auth(), **kwargs)
        except httpx.HTTPError as exc:
            raise CalendarError(f"Microsoft Graph unreachable: {exc}") from exc
        if resp.status_code >= 400:
            try:
                message = (resp.json().get("error") or {}).get("message") or resp.text
            except ValueError:
                message = resp.text
            hint = ""
            if resp.status_code in (401, 403):
                hint = (" (does the Entra app have the Bookings.Read.All and "
                        "BookingsAppointment.ReadWrite.All application permissions, admin-consented?)")
            raise CalendarError(
                f"Microsoft Bookings {what} failed ({resp.status_code}): {str(message)[:300]}{hint}",
                slot_taken=resp.status_code == 409,
            )
        return resp.json() if resp.content else {}

    def _biz(self, suffix: str = "") -> str:
        return f"/solutions/bookingBusinesses/{self._business}{suffix}"

    async def _svc(self) -> _Service:
        if self._service is None or time.monotonic() - self._service_loaded > SERVICE_TTL_SECONDS:
            data = await self._call("GET", self._biz(f"/services/{quote(self._service_id)}"), "service lookup")
            business: dict[str, Any] = {}
            if not data.get("webUrl"):
                business = await self._call("GET", self._biz(), "business lookup")
            self._service = _Service(data, business)
            self._service_loaded = time.monotonic()
        return self._service

    async def _staff(self) -> list[str]:
        """The round-robin team, in rotation order: MSBOOKINGS_STAFF_IDS (the
        order), limited to the staff assigned to the service. Bookings answers
        a booking for someone not on the service with a bare 500, so offering
        their times would only produce bookings that fail."""
        assigned = (await self._svc()).staff_ids
        if not assigned:
            raise CalendarError(
                "the Bookings service has no staff assigned: in Bookings, open Services -> "
                "the service -> Staff, and select the people who take these meetings"
            )
        if not self._staff_override:
            return assigned
        staff = [s for s in self._staff_override if s in assigned]
        if not staff:
            raise CalendarError(
                "none of MSBOOKINGS_STAFF_IDS is assigned to the Bookings service: assign them "
                "under Services -> the service -> Staff, or empty MSBOOKINGS_STAFF_IDS"
            )
        return staff

    # --- availability -------------------------------------------------------

    async def _free_windows(self, staff: list[str], start: datetime, end: datetime
                            ) -> dict[str, list[tuple[datetime, datetime]]]:
        body = {
            "staffIds": staff,
            "startDateTime": {"dateTime": _utc_naive(start), "timeZone": "UTC"},
            "endDateTime": {"dateTime": _utc_naive(end), "timeZone": "UTC"},
        }
        data = await self._call("POST", self._biz("/getStaffAvailability"), "availability lookup", json=body)
        items = data.get("value") or data.get("staffAvailabilityItem") or []
        out: dict[str, list[tuple[datetime, datetime]]] = {s: [] for s in staff}
        for person in items:
            sid = str(person.get("staffId") or "")
            if sid not in out:
                continue
            for item in person.get("availabilityItems") or []:
                if str(item.get("status") or "").lower() not in ("available", "slotsavailable"):
                    continue
                s = _graph_time(item.get("startDateTime"), self._tz)
                e = _graph_time(item.get("endDateTime"), self._tz)
                if s and e and e > s:
                    out[sid].append((s, e))
        return {sid: _merge(w) for sid, w in out.items()}

    def _starts(self, window: tuple[datetime, datetime], svc: _Service) -> Iterable[datetime]:
        """Slot starts inside a free window, on the interval from local midnight."""
        lo, hi = window[0] + svc.pre, window[1] - svc.post - svc.duration
        if hi < lo:
            return
        local = lo.astimezone(self._tz)
        midnight = datetime.combine(local.date(), datetime.min.time(), tzinfo=self._tz)
        step = svc.interval.total_seconds()
        n = -(-((local - midnight).total_seconds()) // step)  # ceil
        t = midnight + timedelta(seconds=n * step)
        while t <= hi:
            yield t.astimezone(timezone.utc)
            t += svc.interval

    async def _who_is_free(self, start: datetime, end: datetime) -> dict[datetime, list[str]]:
        """Slot start -> the staff free for it, in rotation order."""
        svc = await self._svc()
        staff = await self._staff()
        windows = await self._free_windows(staff, start - svc.pre, end + svc.duration + svc.post)
        # The service's own booking rules; the lead's minimum notice is
        # applied later, by app.calendar.slots.
        now = datetime.now(timezone.utc)
        earliest = now + svc.lead_time if svc.lead_time else None
        latest = now + svc.max_advance if svc.max_advance else None
        free: dict[datetime, list[str]] = {}
        for sid in staff:
            for window in windows.get(sid, []):
                for t in self._starts(window, svc):
                    if (start <= t <= end and (earliest is None or t >= earliest)
                            and (latest is None or t <= latest)):
                        free.setdefault(t, []).append(sid)
        return free

    async def free_slots(self, start: datetime, end: datetime) -> list[Slot]:
        svc = await self._svc()
        free = await self._who_is_free(start, end)
        return [Slot(start=t, end=t + svc.duration) for t in sorted(free)]

    # --- round robin ----------------------------------------------------------

    async def _last_assigned(self) -> Optional[str]:
        """Who got the most recently created booking, so the rotation
        survives a restart and includes bookings made on the booking page."""
        now = datetime.now(timezone.utc)
        try:
            items = await self._appointments(now - ROTATION_LOOKBACK, now + LIST_HORIZON)
        except CalendarError as exc:
            log.warning("round robin: could not read recent bookings (%s); using memory", exc)
            return self._last_staff
        latest: Optional[tuple[str, str]] = None
        for item in items:
            created = item.get("createdDateTime")
            staff = item.get("staffMemberIds") or []
            if created and staff and str(item.get("serviceId")) == self._service_id:
                if latest is None or created > latest[0]:
                    latest = (created, str(staff[0]))
        return latest[1] if latest else self._last_staff

    async def _next_staff(self, candidates: list[str]) -> str:
        """The first free staff member after the last one assigned."""
        order = await self._staff()
        last = await self._last_assigned()
        if last in order:
            i = order.index(last) + 1
            order = order[i:] + order[:i]
        for sid in order:
            if sid in candidates:
                return sid
        return candidates[0]

    # --- booking --------------------------------------------------------------

    async def book(self, slot_start: datetime, attendee: Attendee, notes: str = "") -> Booking:
        svc = await self._svc()
        slot_start = slot_start.astimezone(timezone.utc)
        free = await self._who_is_free(slot_start - timedelta(minutes=1), slot_start + timedelta(minutes=1))
        candidates = next((v for t, v in free.items() if abs((t - slot_start).total_seconds()) < 60), [])
        if not candidates:
            raise CalendarError(f"{slot_start.isoformat()} is no longer free for anyone", slot_taken=True)
        staff = await self._next_staff(candidates)
        end = slot_start + svc.duration
        body: dict[str, Any] = {
            "serviceId": self._service_id,
            "serviceName": svc.name,
            "staffMemberIds": [staff],
            "start": {"dateTime": _utc_naive(slot_start), "timeZone": "UTC"},
            "end": {"dateTime": _utc_naive(end), "timeZone": "UTC"},
            "preBuffer": f"PT{int(svc.pre.total_seconds())}S",
            "postBuffer": f"PT{int(svc.post.total_seconds())}S",
            "isLocationOnline": svc.online,
            "optOutOfCustomerEmail": False,
            "customerTimeZone": attendee.timezone,
            "customers": [{
                "@odata.type": "#microsoft.graph.bookingCustomerInformation",
                "name": attendee.name,
                "emailAddress": attendee.email,
                "timeZone": attendee.timezone,
                "notes": notes[:500] or None,
            }],
        }
        try:
            data = await self._call("POST", self._biz("/appointments"), "booking", json=body)
        except CalendarError as exc:
            # Graph sometimes answers 500 although the appointment was made
            # (seen with online-meeting services): look before reporting a
            # failure, or the next attempt would book the lead twice.
            if exc.slot_taken:
                raise
            data = await self._made_anyway(slot_start, end, attendee.email)
            if data is None:
                raise
            log.warning("Microsoft Bookings reported an error but the booking exists: %s", exc)
        if not data.get("id"):
            raise CalendarError("Microsoft Bookings answered the booking without an id")
        self._last_staff = staff
        log.info("Microsoft Bookings: %s booked with staff %s at %s", attendee.email, staff, slot_start)
        return Booking(
            provider=self.name,
            external_id=str(data["id"]),
            start=_graph_time(data.get("start"), self._tz) or slot_start,
            end=_graph_time(data.get("end"), self._tz) or end,
            meeting_url=data.get("joinWebUrl") or data.get("anonymousJoinWebUrl") or None,
        )

    async def _made_anyway(self, start: datetime, end: datetime, email: str) -> Optional[dict[str, Any]]:
        """This service's appointment for ``email`` at ``start``, if it exists."""
        try:
            items = await self._appointments(start - timedelta(minutes=1), end + timedelta(minutes=1))
        except CalendarError:
            return None
        for item in items:
            begins = _graph_time(item.get("start"), self._tz)
            emails = {(c.get("emailAddress") or "").lower() for c in item.get("customers") or []}
            emails.add((item.get("customerEmailAddress") or "").lower())
            if (begins and abs((begins - start).total_seconds()) < 60 and email.lower() in emails
                    and str(item.get("serviceId")) == self._service_id):
                return item
        return None

    def booking_link(self) -> Optional[str]:
        """MSBOOKINGS_BOOKING_URL, else the service's page once it has been read."""
        return self._booking_url or (self._service.url if self._service else None)

    # --- reading bookings back ------------------------------------------------

    async def _appointments(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        path: Optional[str] = self._biz("/calendarView")
        params: Optional[dict[str, str]] = {"start": _utc(start), "end": _utc(end)}
        for _ in range(20):
            data = await self._call("GET", path, "booking list", params=params)
            out.extend(data.get("value") or [])
            path, params = data.get("@odata.nextLink"), None
            if not path:
                break
            path = path.removeprefix(GRAPH_BASE)
        return out

    async def list_bookings(self, starting_after: datetime) -> list[RemoteBooking]:
        await self._svc()  # also keeps booking_link() filled for the sync callers
        out: list[RemoteBooking] = []
        for item in await self._appointments(starting_after, starting_after + LIST_HORIZON):
            start = _graph_time(item.get("start"), self._tz)
            if not item.get("id") or start is None or start < starting_after:
                continue
            guest = next((c for c in item.get("customers") or [] if c.get("emailAddress")), {})
            out.append(RemoteBooking(
                external_id=str(item["id"]),
                start=start,
                end=_graph_time(item.get("end"), self._tz) or start,
                status="booked",  # cancelled appointments are deleted, not listed
                attendee_email=guest.get("emailAddress") or item.get("customerEmailAddress"),
                attendee_name=guest.get("name") or item.get("customerName"),
                meeting_url=item.get("joinWebUrl") or item.get("anonymousJoinWebUrl") or None,
                ours=str(item.get("serviceId")) == self._service_id,
            ))
        return sorted(out, key=lambda b: b.start)

    # --- setup help -------------------------------------------------------------

    async def describe(self) -> dict[str, Any]:
        """The configured service, its staff and the booking page (setup check)."""
        svc = await self._svc()
        staff = await self._call("GET", self._biz("/staffMembers"), "staff lookup")
        team = set(await self._staff())
        return {
            "service": {"id": self._service_id, "name": svc.name,
                        "minutes": int(svc.duration.total_seconds() // 60),
                        "interval_minutes": int(svc.interval.total_seconds() // 60),
                        "online": svc.online},
            "booking_link": self.booking_link(),
            "round_robin": [
                {"id": m.get("id"), "name": m.get("displayName"), "email": m.get("emailAddress")}
                for m in staff.get("value") or [] if m.get("id") in team
            ],
        }

    async def aclose(self) -> None:
        await self._client.aclose()


async def discover(tenant_id: str, client_id: str, client_secret: str,
                   transport: Optional[httpx.AsyncBaseTransport] = None) -> list[dict[str, Any]]:
    """Every Bookings business the app can see, with its services and staff:
    where MSBOOKINGS_BUSINESS_ID / _SERVICE_ID / _STAFF_IDS come from."""
    probe = MSBookingsProvider(tenant_id, client_id, client_secret, "-", "-", transport=transport)
    try:
        out = []
        data = await probe._call("GET", "/solutions/bookingBusinesses", "business list")
        for biz in data.get("value") or []:
            bid = quote(str(biz.get("id")), safe="@.")
            services = await probe._call("GET", f"/solutions/bookingBusinesses/{bid}/services", "service list")
            staff = await probe._call("GET", f"/solutions/bookingBusinesses/{bid}/staffMembers", "staff list")
            out.append({
                "business_id": biz.get("id"),
                "name": biz.get("displayName"),
                "booking_page": biz.get("publicUrl"),
                "services": [{"id": s.get("id"), "name": s.get("displayName"),
                              "length": s.get("defaultDuration"),
                              "staff_ids": s.get("staffMemberIds") or [],
                              "booking_page": s.get("webUrl")}
                             for s in services.get("value") or []],
                "staff": [{"id": m.get("id"), "name": m.get("displayName"),
                           "email": m.get("emailAddress"), "role": m.get("role")}
                          for m in staff.get("value") or []],
            })
        return out
    finally:
        await probe.aclose()
