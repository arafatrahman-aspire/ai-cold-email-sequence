"""Calendar sync: record meetings the lead booked themselves.

Triage records the meetings it books. A lead can also use the booking link
from a reply (or forward it to a colleague), and then nothing in the inbox
says so. This worker reads the calendar's bookings every few minutes and
records them in cold_email.meetings: a booking by a known lead stops their
sequence and cancels any unsent reply, a cancellation is noted, and
bookings for our own event type are kept even from unknown attendees.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app import repository as repo
from app.calendar.base import CalendarError
from app.calendar.factory import get_calendar

log = logging.getLogger(__name__)

# Bookings that started up to a day ago are still read, so a meeting that is
# under way (or a late cancellation) is not missed.
LOOKBACK = timedelta(days=1)

last_run: dict[str, Any] = {}


async def run_once(now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    stats: dict[str, Any] = {}
    try:
        calendar = get_calendar()
        bookings = await calendar.list_bookings(now - LOOKBACK) if calendar else None
    except CalendarError as exc:
        log.warning("calendar sync skipped: %s", exc)
        stats["error"] = str(exc)
        bookings = None

    for b in bookings or []:
        try:
            result = await repo.sync_meeting(
                calendar.name, b.external_id, b.start, b.end, b.status,
                b.attendee_email, b.attendee_name, b.meeting_url, b.ours,
            )
            outcome = result.get("outcome") or "unknown"
            if outcome in ("new", "cancelled"):
                log.info("calendar sync: %s booking %s (%s, %s)", outcome, b.external_id,
                         b.attendee_email, b.start.isoformat())
        except Exception as exc:
            log.warning("calendar sync of booking %s failed: %s", b.external_id, exc)
            outcome = "failed"
        stats[outcome] = stats.get(outcome, 0) + 1

    if bookings is not None and calendar.missing_means_cancelled:
        await _cancel_missing(calendar.name, {b.external_id for b in bookings}, now, stats)

    last_run.clear()
    last_run.update({
        "at": now.isoformat(),
        "provider": calendar.name if bookings is not None else None,
        "seen": len(bookings or []),
        **stats,
    })
    if stats.get("new") or stats.get("cancelled") or stats.get("updated") or stats.get("failed"):
        log.info("calendar sync: %s", stats)
    return stats


async def _cancel_missing(provider: str, seen: set[str], now: datetime, stats: dict[str, Any]) -> None:
    """A provider that deletes cancelled bookings (Microsoft Bookings): a
    meeting we hold as booked, inside the window just read, that the
    calendar no longer lists was cancelled."""
    try:
        known = await repo.meetings_overview(500)
    except Exception as exc:
        log.warning("calendar sync: could not read meetings to find cancellations: %s", exc)
        return
    for m in known:
        start = m.get("start_at")
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if (m.get("provider") != provider or m.get("status") not in ("booked", "pending")
                or m.get("external_id") in seen or start is None or start < now - LOOKBACK):
            continue
        try:
            result = await repo.sync_meeting(
                provider, m["external_id"], start, m.get("end_at"), "cancelled",
                m.get("attendee_email"), m.get("attendee_name"), None, True,
            )
            outcome = result.get("outcome") or "unknown"
            log.info("calendar sync: booking %s is gone from %s, marked cancelled", m["external_id"], provider)
        except Exception as exc:
            log.warning("calendar sync: cancelling booking %s failed: %s", m.get("external_id"), exc)
            outcome = "failed"
        stats[outcome] = stats.get(outcome, 0) + 1
