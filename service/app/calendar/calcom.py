"""Cal.com (API v2) calendar provider.

Slots come from ``GET /v2/slots`` for one event type; bookings are made with
``POST /v2/bookings``. Cal.com then sends its own calendar invite to the lead,
adds the video link configured on the event type, and puts the meeting in
whichever calendar (Google, Outlook, ...) is connected to the Cal.com account.

Each endpoint is versioned through the ``cal-api-version`` header; both
versions are settings so a Cal.com API change is an .env edit, not a code edit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.calendar.base import Attendee, Booking, CalendarError, CalendarProvider, Slot

log = logging.getLogger(__name__)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CalComProvider(CalendarProvider):
    name = "calcom"

    def __init__(
        self,
        api_key: str,
        event_type_id: int,
        booking_url: str = "",
        base_url: str = "https://api.cal.com",
        slots_version: str = "2024-09-04",
        bookings_version: str = "2026-02-25",
        timeout: float = 20.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        if not api_key or not event_type_id:
            raise CalendarError("Cal.com needs CALCOM_API_KEY and CALCOM_EVENT_TYPE_ID")
        self._event_type_id = int(event_type_id)
        self._booking_url = booking_url.strip() or None
        self._slots_version = slots_version
        self._bookings_version = bookings_version
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

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
        # Cal.com answers a lost booking race with a 4xx naming the slot.
        taken = resp.status_code in (400, 409) and any(
            w in message.lower() for w in ("already", "unavailable", "not available", "booked")
        )
        return CalendarError(f"Cal.com {what} failed ({resp.status_code}): {message}", slot_taken=taken)

    async def free_slots(self, start: datetime, end: datetime) -> list[Slot]:
        params = {
            "eventTypeId": str(self._event_type_id),
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
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
            "start": slot_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "eventTypeId": self._event_type_id,
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
            meeting_url=data.get("meetingUrl") or data.get("location"),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
