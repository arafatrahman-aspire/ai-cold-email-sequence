"""Cal.com (API v2) calendar provider.

Slots come from ``GET /v2/slots`` for one event type; bookings are made with
``POST /v2/bookings`` and read back with ``GET /v2/bookings`` (so meetings
booked through the public link are recorded too). Cal.com then sends its own calendar invite to the lead,
adds the video link configured on the event type, and puts the meeting in
whichever calendar (Google, Outlook, ...) is connected to the Cal.com account.

Each endpoint is versioned through the ``cal-api-version`` header; both
versions are settings so a Cal.com API change is an .env edit, not a code edit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from app.calendar.base import Attendee, Booking, CalendarError, CalendarProvider, RemoteBooking, Slot

log = logging.getLogger(__name__)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _url(data: dict[str, Any]) -> Optional[str]:
    """The join link. ``location`` can also be a place or "integrations:...". """
    for key in ("location", "meetingUrl"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


# Cal.com booking statuses -> ours.
_STATUS = {"accepted": "booked", "pending": "pending", "awaiting_host": "pending",
           "cancelled": "cancelled", "rejected": "cancelled"}


class CalComProvider(CalendarProvider):
    name = "calcom"

    def __init__(
        self,
        api_key: str,
        event_type: int | str,
        booking_url: str = "",
        username: str = "",
        base_url: str = "https://api.cal.com",
        slots_version: str = "2024-09-04",
        bookings_version: str = "2026-02-25",
        list_version: str = "2026-05-01",
        timeout: float = 20.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        event = str(event_type or "").strip()
        if not api_key or not event:
            raise CalendarError("Cal.com needs CALCOM_API_KEY and CALCOM_EVENT_TYPE_ID")
        self._booking_url = booking_url.strip() or None
        # An event type is addressed by its number, or by username + slug.
        if event.isdigit():
            self._event = {"eventTypeId": int(event)}
        else:
            user = username.strip() or self._username_from(self._booking_url)
            if not user:
                raise CalendarError(
                    f"CALCOM_EVENT_TYPE_ID={event!r} is a slug, so Cal.com also needs the username: "
                    "set CALCOM_USERNAME, or CALCOM_BOOKING_URL=https://cal.com/<username>/<slug>"
                )
            self._event = {"eventTypeSlug": event, "username": user}
        self._slots_version = slots_version
        self._bookings_version = bookings_version
        self._list_version = list_version
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    @staticmethod
    def _username_from(url: Optional[str]) -> str:
        """'https://cal.com/alex/30min' -> 'alex'."""
        parts = [p for p in urlsplit(url or "").path.split("/") if p]
        return parts[0] if len(parts) >= 2 else ""

    def booking_link(self) -> Optional[str]:
        return self._booking_url

    @staticmethod
    def _error(resp: httpx.Response, what: str) -> CalendarError:
        try:
            detail = resp.json().get("error", {})
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = resp.text
        message = str(message or resp.text)[:300]
        # Cal.com answers a lost booking race (or a time that has since
        # passed, or is now inside the minimum notice) with a 4xx: offer others.
        taken = resp.status_code in (400, 409) and any(
            w in message.lower()
            for w in ("already", "unavailable", "not available", "booked", "past", "notice")
        )
        return CalendarError(f"Cal.com {what} failed ({resp.status_code}): {message}", slot_taken=taken)

    async def free_slots(self, start: datetime, end: datetime) -> list[Slot]:
        params = {
            **{k: str(v) for k, v in self._event.items()},
            "start": _utc(start),
            "end": _utc(end),
            "timeZone": "UTC",
            "format": "range",
        }
        try:
            resp = await self._client.get(
                "/v2/slots", params=params, headers={"cal-api-version": self._slots_version}
            )
        except httpx.HTTPError as exc:
            raise CalendarError(f"Cal.com unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise self._error(resp, "slot lookup")

        slots: list[Slot] = []
        for day in (resp.json().get("data") or {}).values():
            for item in day or []:
                s = _parse(item["start"])
                e = _parse(item["end"]) if item.get("end") else None
                if e is None:
                    raise CalendarError("Cal.com returned a slot without an end; is format=range supported?")
                if start <= s <= end:
                    slots.append(Slot(start=s, end=e))
        return sorted(slots, key=lambda x: x.start)

    async def book(self, slot_start: datetime, attendee: Attendee, notes: str = "") -> Booking:
        body = {
            "start": _utc(slot_start),
            **self._event,
            "attendee": {"name": attendee.name, "email": attendee.email, "timeZone": attendee.timezone},
            "metadata": {"source": "reply-triage"},
        }
        if notes:
            body["bookingFieldsResponses"] = {"notes": notes[:500]}
        try:
            resp = await self._client.post(
                "/v2/bookings", json=body, headers={"cal-api-version": self._bookings_version}
            )
        except httpx.HTTPError as exc:
            raise CalendarError(f"Cal.com unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise self._error(resp, "booking")

        data = resp.json().get("data") or {}
        if isinstance(data, list):  # recurring event types answer with a list
            data = data[0] if data else {}
        uid = data.get("uid") or data.get("id")
        if not uid:
            raise CalendarError("Cal.com booking response had no uid")
        start = _parse(data["start"]) if data.get("start") else slot_start
        end = _parse(data["end"]) if data.get("end") else start
        return Booking(
            provider=self.name,
            external_id=str(uid),
            start=start,
            end=end,
            meeting_url=_url(data),
        )

    async def list_bookings(self, starting_after: datetime) -> list[RemoteBooking]:
        out: list[RemoteBooking] = []
        params: dict[str, Any] = {"afterStart": _utc(starting_after), "sortStart": "asc", "limit": 100}
        for _ in range(10):  # 1,000 bookings is far more than one sync needs
            try:
                resp = await self._client.get(
                    "/v2/bookings", params=params, headers={"cal-api-version": self._list_version}
                )
            except httpx.HTTPError as exc:
                raise CalendarError(f"Cal.com unreachable: {exc}") from exc
            if resp.status_code >= 400:
                raise self._error(resp, "booking list")
            body = resp.json()
            for item in body.get("data") or []:
                booking = self._remote(item)
                if booking is not None:
                    out.append(booking)
            cursor = (body.get("pagination") or {}).get("nextCursor")
            if not cursor:
                break
            params["cursor"] = cursor
        return out

    def _remote(self, item: dict[str, Any]) -> Optional[RemoteBooking]:
        uid, start = item.get("uid"), item.get("start")
        if not uid or not start:
            return None
        guest = next((a for a in item.get("attendees") or [] if a.get("email")), {})
        begins = _parse(start)
        ours = (
            str(item.get("eventTypeId")) == str(self._event["eventTypeId"])
            if "eventTypeId" in self._event
            else (item.get("eventType") or {}).get("slug") == self._event["eventTypeSlug"]
        )
        return RemoteBooking(
            external_id=str(uid),
            start=begins,
            end=_parse(item["end"]) if item.get("end") else begins,
            status=_STATUS.get(str(item.get("status") or "").lower(), "booked"),
            attendee_email=guest.get("email"),
            attendee_name=guest.get("name"),
            meeting_url=_url(item),
            ours=ours,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
