"""Reply alerts: tell a human as soon as a prospect answers.

The sequence never answers prospects itself; once someone replies, a person
takes over. This sends that person a short email with who replied and what
they said, from the same inbox that received the reply. Alerts are best
effort: a failure is logged and never blocks reply processing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote

from app import repository as repo
from app import settings_store
from app.mail.base import Inbox, IncomingMessage, OutgoingMessage
from app.mail.factory import get_sender

log = logging.getLogger(__name__)

_SNIPPET_CHARS = 1500


def gmail_link(inbox_email: str, message_id: str | None) -> str | None:
    """A link that opens the message in Gmail, for Gmail inboxes."""
    if not message_id or not inbox_email.lower().endswith(("@gmail.com", "@googlemail.com")):
        return None
    msgid = message_id.strip().strip("<>")
    return (
        f"https://mail.google.com/mail/u/?authuser={quote(inbox_email)}"
        f"#search/rfc822msgid%3A{quote(msgid, safe='')}"
    )


def build_alert(inbox: Inbox, lead: dict | None, message: IncomingMessage) -> tuple[str, str]:
    """Subject and plain-text body for a reply alert."""
    lead = lead or {}
    name = " ".join(p for p in (lead.get("first_name"), lead.get("last_name")) if p)
    who = f"{name} <{message.from_email}>" if name else message.from_email
    details = " · ".join(
        p for p in (name or None, lead.get("job_title"), lead.get("company")) if p
    )
    received = (message.received_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    snippet = (message.body_snippet or "").strip()
    if len(snippet) > _SNIPPET_CHARS:
        snippet = snippet[:_SNIPPET_CHARS].rstrip() + " …"

    lines = [
        f"New reply from {who}",
        "",
        f"Lead:     {details or lead.get('email') or message.from_email}",
        f"Received: {received:%Y-%m-%d %H:%M} UTC",
        f"Subject:  {message.subject or '(no subject)'}",
        "",
        "--- Their message ---",
        snippet or "(no text)",
        "---------------------",
        "",
        "Their remaining cold emails have been cancelled.",
        f"Reply to them from {inbox.email}.",
    ]
    link = gmail_link(inbox.email, message.message_id)
    if link:
        lines += ["", f"Open in Gmail: {link}"]
    subject = f"New reply from {name or message.from_email}"
    if message.subject:
        subject += f": {message.subject}"
    return subject[:200], "\n".join(lines)


async def send_reply_alert(inbox: Inbox, lead_id: str, message: IncomingMessage) -> bool:
    """Email the configured address about a new reply. Returns True if sent."""
    to = str(await settings_store.get("reply_alert_email") or "").strip()
    if not to:
        return False
    try:
        lead = await repo.lead_for_step(lead_id)
        subject, body = build_alert(inbox, lead, message)
        await get_sender().send(inbox, OutgoingMessage(to_email=to, subject=subject, body_text=body))
        log.info("reply alert for lead %s sent to %s", lead_id, to)
        return True
    except Exception:
        log.exception("could not send reply alert for lead %s to %s", lead_id, to)
        return False
