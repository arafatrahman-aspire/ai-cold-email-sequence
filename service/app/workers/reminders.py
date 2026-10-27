"""Meeting reminders: one short email the day before each booked meeting.

Every few minutes, booked meetings starting within the reminder window
(Reply triage -> Settings -> Meetings, default 24 hours) that have had no
reminder get one, from the first active inbox, in the lead's own time, with
the join link. A meeting booked inside the window (e.g. this morning for
tomorrow) needs none: its confirmation is recent. Moving a meeting clears
its reminder, so the new time is reminded too (0019's trigger).

Leads can answer the reminder with a new time; Reply triage then moves the
meeting itself.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app import repository as repo
from app import settings_store
from app.calendar.base import Slot
from app.calendar.slots import describe_slot
from app.config import get_settings
from app.mail import templates
from app.mail.base import OutgoingMessage
from app.mail.factory import get_sender

log = logging.getLogger(__name__)


def _as_dt(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def reminder_text(first_name: Optional[str], when: str, meeting_url: Optional[str]) -> tuple[str, str]:
    """(subject, body). Plain and short, like a person would write it."""
    subject = f"Reminder: our call {when.split(',')[0]}"
    body = f"Hi {first_name},\n\n" if first_name else "Hi,\n\n"
    body += f"A quick reminder of our call on {when}."
    if meeting_url:
        body += f"\n\nYou can join here: {meeting_url}"
    body += "\n\nIf the time no longer works, just reply with one that does and I'll move it."
    return subject, body


async def run_once(now: Optional[datetime] = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    cfg = await settings_store.triage_config()
    hours = int(cfg["meeting"].get("reminder_hours", 24) or 0)
    if hours <= 0:
        return {}
    due = await repo.meetings_due_reminder(now + timedelta(hours=hours))
    if not due:
        return {}
    inboxes = await repo.cached_inboxes()
    if not inboxes:
        log.warning("meeting reminders: no active inbox to send from")
        return {"no_inbox": len(due)}
    inbox = inboxes[0]
    style = await settings_store.email_style()
    s = get_settings()
    stats: dict[str, int] = {}

    for m in due:
        start, created = _as_dt(m["start_at"]), _as_dt(m["created_at"]) if m.get("created_at") else None
        end = _as_dt(m["end_at"]) if m.get("end_at") else start + timedelta(minutes=30)
        if not await repo.mark_meeting_reminded(str(m["id"]), True):
            continue  # another run took it
        if created and start - created < timedelta(hours=hours):
            stats["booked_recently"] = stats.get("booked_recently", 0) + 1
            continue
        lead = await repo.lead_for_step(str(m["lead_id"])) if m.get("lead_id") else None
        tz = (lead or {}).get("timezone") or "UTC"
        first = (lead or {}).get("first_name") or ((m.get("attendee_name") or "").split() or [None])[0]
        subject, body = reminder_text(first, describe_slot(Slot(start, end), tz), m.get("meeting_url"))
        mail = templates.personal(
            body, templates.Signature.from_settings(style, inbox.display_name or ""),
            sign_off=str(style.get("sign_off") or ""), accent=templates.accent_color(style.get("accent")),
            subject=subject, layout=templates.layout_name(style.get("layout")))
        try:
            await get_sender().send(inbox, OutgoingMessage(
                to_email=m["attendee_email"], subject=subject, body_text=mail.text,
                body_html=mail.html if style.get("html", True) else None, reply_to=s.reply_to or None,
            ))
        except Exception:
            # Let the next run try again.
            await repo.mark_meeting_reminded(str(m["id"]), False)
            log.exception("meeting reminder for %s failed", m["attendee_email"])
            stats["failed"] = stats.get("failed", 0) + 1
            continue
        log.info("meeting reminder sent to %s for %s", m["attendee_email"], start.isoformat())
        stats["sent"] = stats.get("sent", 0) + 1
    if stats:
        log.info("meeting reminders: %s", stats)
    return stats
