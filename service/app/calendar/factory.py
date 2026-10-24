"""Calendar provider selection (CALENDAR_PROVIDER)."""

from __future__ import annotations

from typing import Optional

from app.calendar.base import CalendarError, CalendarProvider
from app.config import get_settings

_calendar: Optional[CalendarProvider] = None
_override: Optional[CalendarProvider] = None


def _build(name: str) -> Optional[CalendarProvider]:
    s = get_settings()
    if name == "none":
        return None
    if name == "fake":
        from app.calendar.fake import FakeCalendar

        return FakeCalendar(s.fake_calendar_timezone, s.fake_calendar_booking_url)
    if name == "calcom":
        from app.calendar.calcom import CalComProvider

        return CalComProvider(
            api_key=s.calcom_api_key,
            event_type=s.calcom_event_type_id,
            booking_url=s.calcom_booking_url,
            username=s.calcom_username,
            base_url=s.calcom_base_url,
            slots_version=s.calcom_slots_api_version,
            bookings_version=s.calcom_bookings_api_version,
            list_version=s.calcom_list_bookings_api_version,
            timeout=s.calendar_timeout_seconds,
        )
    if name == "microsoft":
        from app.calendar.msbookings import MSBookingsProvider

        return MSBookingsProvider(
            tenant_id=s.graph_tenant_id,
            client_id=s.graph_client_id,
            client_secret=s.graph_client_secret,
            business_id=s.msbookings_business_id,
            service_id=s.msbookings_service_id,
            staff_ids=s.msbookings_staff_ids.split(","),
            booking_url=s.msbookings_booking_url,
            business_timezone=s.msbookings_timezone,
            timeout=s.calendar_timeout_seconds,
        )
    if name == "google":
        # Extension point: implement CalendarProvider for it (see base.py).
        raise CalendarError(f"calendar provider {name!r} is not implemented yet")
    raise CalendarError(f"unknown CALENDAR_PROVIDER: {name}")


def get_calendar() -> Optional[CalendarProvider]:
    """The configured calendar, or None when CALENDAR_PROVIDER=none."""
    global _calendar
    if _override is not None:
        return _override
    if _calendar is None:
        _calendar = _build(get_settings().calendar_provider)
    return _calendar


def booking_link() -> Optional[str]:
    """The configured calendar's public booking page, or None. Never raises,
    so callers that only want a link (nurture's demo button) need no checks."""
    try:
        cal = get_calendar()
    except CalendarError:
        return None
    return cal.booking_link() if cal else None


def use_calendar(provider: Optional[CalendarProvider]) -> None:
    """Swap the calendar (tests, or a provider built elsewhere). None resets."""
    global _override
    _override = provider


async def close_calendar() -> None:
    global _calendar
    if _calendar is not None:
        await _calendar.aclose()
        _calendar = None
