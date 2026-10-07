"""Inbound worker: poll each inbox, classify, cancel sequences.

Classification precedence, highest first:
  bounce      -> hard bounces suppress the address and stop the sequence
  auto_reply  -> out-of-office; logged, but the sequence continues
  unsubscribe -> opt-out language in a human reply; suppress and stop
  reply       -> a real human reply; stop the sequence
"""

from __future__ import annotations

import logging
from typing import Any

from app import repository as repo
from app.workers import alerts
from app.mail import parsing
from app.mail.base import Inbox, IncomingMessage, MailError
from app.mail.factory import get_reader
from app.nurture import replies as nurture_replies

log = logging.getLogger(__name__)


async def _match_lead(message: IncomingMessage) -> str | None:
    """Tie an inbound message to a lead.

    Header threading is tried first because it survives a reply sent from a
    different address than the one we mailed; the address match is the
    fallback.
    """
    candidates: list[str] = []
    if message.in_reply_to:
        candidates.append(message.in_reply_to)
    candidates.extend(message.references)

    lead_id = await repo.match_lead_by_message_ids(candidates)
    if lead_id:
        return lead_id

    if message.is_bounce_report and message.bounced_recipient:
        lead_id = await repo.match_lead_by_email(message.bounced_recipient)
        if lead_id:
            return lead_id

    return await repo.match_lead_by_email(message.from_email)


def _classify(message: IncomingMessage) -> str:
    if message.is_bounce_report:
        return "bounce"
    if message.is_auto_reply:
        return "auto_reply"
    if parsing.looks_like_unsubscribe(message.subject, message.body_snippet):
        return "unsubscribe"
    if message.from_email:
        return "reply"
    return "unknown"


async def _handle(inbox: Inbox, message: IncomingMessage) -> str:
    event_type = _classify(message)

    # Email Nurture: a reply or bounce about a nurture email is handled there.
    # Anything else (or any nurture error) continues exactly as before.
    handled = await nurture_replies.handle_inbound(inbox, message, event_type)
    if handled:
        return handled

    lead_id = await _match_lead(message)

    # Mail that is not about an enrolled lead is none of our business: it is
    # neither acted on nor stored (the inbox may also carry unrelated mail).
    if lead_id is None:
        log.debug("inbound %s from %s matched no lead", event_type, message.from_email)
        return "unmatched"

    is_new = await repo.record_inbox_event(
        inbox_id=inbox.id,
        lead_id=lead_id,
        event_type=event_type,
        from_email=message.from_email,
        to_email=message.to_email,
        subject=message.subject,
        message_id=message.message_id,
        in_reply_to=message.in_reply_to,
        snippet=message.body_snippet[:2000],
        received_at=message.received_at,
    )
    if not is_new:
        return "duplicate"

    if event_type == "bounce":
        target = message.bounced_recipient or message.from_email
        if message.bounce_is_permanent:
            await repo.suppress(target, "hard_bounce")
            await repo.cancel_sequence(lead_id, "bounced", "hard bounce")
            log.info("lead %s hard bounced (%s); suppressed", lead_id, target)
            return "hard_bounce"
        # Soft bounce: the address is probably fine, so leave the sequence running.
        log.info("lead %s soft bounced (%s); sequence continues", lead_id, target)
        return "soft_bounce"

    if event_type == "auto_reply":
        log.info("lead %s sent an auto-reply; sequence continues", lead_id)
        return "auto_reply"

    if event_type == "unsubscribe":
        await repo.suppress(message.from_email, "unsubscribed")
        await repo.cancel_sequence(lead_id, "unsubscribed", "opt-out in reply")
        log.info("lead %s unsubscribed; suppressed and cancelled", lead_id)
        return "unsubscribe"

    if event_type == "reply":
        await repo.cancel_sequence(lead_id, "replied", "human reply received")
        log.info("lead %s replied; sequence cancelled", lead_id)
        # The sequence never answers; a person takes over from here.
        await alerts.send_reply_alert(inbox, lead_id, message)
        return "reply"

    return "unknown"


async def _start_tracking(inbox: Inbox) -> bool:
    """First poll of an inbox: skip the mail already in it.

    None of it can be a reply to cold email sent through this inbox, and
    reading a whole mailbox history would be slow and pointless. Returns
    False for an empty IMAP folder, where there is nothing to skip.
    """
    cursor = await get_reader().current_cursor(inbox)
    if cursor <= 0:
        return False
    await repo.update_inbox_cursor(inbox.id, cursor)
    log.info("started reply tracking for %s (skipping existing mail)", inbox.email)
    return True


async def poll_inbox(inbox: Inbox) -> tuple[dict[str, int], str | None]:
    """Poll one inbox. Returns outcome counts and, if it failed, why."""
    stats: dict[str, int] = {}
    try:
        if inbox.poll_cursor == 0 and await _start_tracking(inbox):
            return {"tracking_started": 1}, None
        messages = await get_reader().fetch_new(inbox)
    except MailError as exc:
        log.warning("poll failed for %s: %s", inbox.email, exc)
        return {"error": 1}, f"{inbox.email}: {exc}"
    except Exception as exc:
        log.exception("unexpected poll error for %s", inbox.email)
        return {"error": 1}, f"{inbox.email}: unexpected error: {exc}"

    if not messages:
        return stats, None

    log.info("polled %d message(s) from %s", len(messages), inbox.email)
    highest = inbox.poll_cursor
    for message in messages:
        try:
            outcome = await _handle(inbox, message)
            stats[outcome] = stats.get(outcome, 0) + 1
        except Exception:
            # Advancing the cursor past a message we failed to process would
            # lose it permanently, so stop here and retry on the next tick.
            log.exception(
                "failed to handle message uid=%s in %s; stopping this poll",
                message.uid, inbox.email,
            )
            break
        highest = max(highest, message.uid)

    if highest > inbox.poll_cursor:
        await repo.update_inbox_cursor(inbox.id, highest)

    return stats, None


async def run_once() -> dict[str, Any]:
    """Poll every inbox. Returns outcome counts, plus ``errors`` (the reason
    for each inbox that could not be polled) when there were any."""
    inboxes = await repo.active_inboxes()
    totals: dict[str, Any] = {}
    errors: list[str] = []
    for inbox in inboxes:
        stats, error = await poll_inbox(inbox)
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + value
        if error:
            errors.append(error)
    if errors:
        totals["errors"] = errors
    if totals:
        log.info("poller tick: %s", totals)
    return totals
