"""Sending worker: claim due steps, respect caps and hours, send, log.

Ordering of the guards matters and follows OUT-01:
  1. global kill switch
  2. the lead still exists and its shared public.leads status is contactable
     (another project may have marked it DNC, Paid, ... since enrollment)
  3. suppression list (re-checked per step, independent of sequence state)
  4. business hours in the lead's own timezone, using that region's window
  5. per-inbox daily cap
Only once all five pass does anything leave the building.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import repository as repo
from app import settings_store
from app.config import get_settings
from app.mail.base import Inbox, MailError, OutgoingMessage
from app.mail.factory import get_sender
from app.scheduling import (
    next_business_day_start,
    resolve_timezone,
    within_business_hours,
)

log = logging.getLogger(__name__)


class InboxPool:
    """Round-robin over active inboxes, with per-day cap reservation.

    A slot is reserved on the email itself *before* the send; failing,
    deferring or cancelling the email gives it back, so a crash costs at most
    one slot of headroom rather than a duplicate email.
    """

    def __init__(self, inboxes: list[Inbox], default_cap: int) -> None:
        self._inboxes = inboxes
        self._default_cap = default_cap
        self._cursor = 0

    def _cap_for(self, inbox: Inbox) -> int:
        return inbox.daily_cap if inbox.daily_cap is not None else self._default_cap

    async def reserve(self, email_id: str) -> Inbox | None:
        """Reserve a slot for ``email_id`` on some inbox; None if all are capped."""
        if not self._inboxes:
            return None
        today = datetime.now(timezone.utc).date()
        for _ in range(len(self._inboxes)):
            inbox = self._inboxes[self._cursor % len(self._inboxes)]
            self._cursor += 1
            if await repo.reserve_inbox_slot(
                email_id, inbox.id, today, self._cap_for(inbox)
            ):
                return inbox
        return None


async def _send_step(
    step: dict, lead: dict, inbox: Inbox, max_attempts: int
) -> bool:
    """Send one step. Returns True on success."""
    s = get_settings()
    anchor_id = await repo.thread_anchor(lead["id"])

    message = OutgoingMessage(
        to_email=lead["email"],
        subject=step["subject"],
        body_text=step["body"],
        # Steps 2-4 thread under the opener so they read as a follow-up rather
        # than four unrelated cold emails.
        in_reply_to=anchor_id if step["step_number"] > 1 else None,
        references=[anchor_id] if (anchor_id and step["step_number"] > 1) else [],
        unsubscribe_mailto=s.unsubscribe_mailto or inbox.email,
        reply_to=s.reply_to or None,
    )

    try:
        result = await get_sender().send(inbox, message)
    except MailError as exc:
        log.warning(
            "send failed for step %s (permanent=%s): %s",
            step["id"], exc.permanent, exc,
        )
        await repo.fail_step(str(step["id"]), str(exc), exc.permanent, max_attempts)
        if exc.permanent and "recipient refused" in str(exc).lower():
            # A synchronous rejection is a hard bounce in everything but name.
            await repo.suppress(lead["email"], "hard_bounce")
            await repo.cancel_sequence(
                str(lead["id"]), "bounced", "recipient refused at submission"
            )
        return False
    except Exception as exc:
        log.exception("unexpected send error for step %s", step["id"])
        await repo.fail_step(str(step["id"]), str(exc), False, max_attempts)
        return False

    await repo.record_send(
        email_id=str(step["id"]),
        to_email=lead["email"],
        message_id=result.message_id,
        thread_id=result.thread_id,
    )
    return True


def _empty_stats() -> dict[str, int]:
    return {"sent": 0, "deferred_hours": 0, "deferred_cap": 0, "skipped": 0, "failed": 0}


async def run_once() -> dict[str, int]:
    """One scheduled tick: send whatever is due."""
    return await _run(lambda batch: repo.claim_due_steps(batch), respect_hours=True)


async def send_now(lead_id: str | None = None) -> dict[str, int]:
    """Send each scheduled lead's next email immediately (or one lead's).

    Skips only the business-hours wait. The kill switch, lead status,
    suppression and daily caps still apply; only one email per lead goes out,
    and a lead emailed in the last 24 hours is skipped, so repeated presses
    can't send follow-ups back to back.
    """
    return await _run(
        lambda batch: repo.claim_next_emails(lead_id, batch), respect_hours=False
    )


async def _run(claim, *, respect_hours: bool) -> dict[str, int]:
    stats = _empty_stats()

    if not bool(await settings_store.get("sequence_enabled")):
        log.info("sending is disabled via cold_email.settings.sequence_enabled")
        return stats

    await repo.reclaim_stuck_steps()

    inboxes = await repo.active_inboxes()
    if not inboxes:
        log.warning("no active inboxes configured; nothing can be sent")
        return stats

    default_cap = int(await settings_store.get("default_daily_cap"))
    max_attempts = int(await settings_store.get("max_send_attempts"))
    batch_size = int(await settings_store.get("send_batch_size"))
    contactable = set(await settings_store.contactable_statuses())

    steps = await claim(batch_size)
    if not steps:
        return stats

    log.info("sender claimed %d step(s)%s", len(steps), "" if respect_hours else " (send now)")
    pool = InboxPool(inboxes, default_cap)
    now = datetime.now(timezone.utc)

    for record in steps:
        step = dict(record)
        step_id = str(step["id"])

        lead_row = await repo.lead_for_step(str(step["lead_id"]))
        if lead_row is None:
            await repo.fail_step(step_id, "lead disappeared", True, max_attempts)
            stats["failed"] += 1
            continue
        lead = dict(lead_row)
        lead["id"] = str(lead["id"])

        if not lead["lead_exists"] or not lead["email"]:
            await repo.cancel_sequence(
                lead["id"], "failed", "lead removed from public.leads or has no email"
            )
            stats["skipped"] += 1
            continue

        if lead["lead_status"] not in contactable:
            log.info(
                "step %s skipped: shared lead status is now %r",
                step_id, lead["lead_status"],
            )
            await repo.cancel_sequence(
                lead["id"], "stopped", f"public.leads status became {lead['lead_status']}"
            )
            stats["skipped"] += 1
            continue

        # Final safeguard: suppression is re-checked here regardless of the
        # state the claim query saw.
        if await repo.is_suppressed(lead["email"]):
            log.info("step %s skipped: %s is suppressed", step_id, lead["email"])
            await repo.cancel_sequence(lead["id"], "unsubscribed", "suppressed")
            stats["skipped"] += 1
            continue

        hours = await settings_store.business_hours(lead.get("timezone"))
        if respect_hours and not within_business_hours(now, lead.get("timezone"), hours):
            local = now.astimezone(resolve_timezone(lead.get("timezone")))
            due = next_business_day_start(now, lead.get("timezone"), hours)
            log.debug(
                "step %s deferred: %s local time is %s",
                step_id, lead.get("timezone"), local.strftime("%a %H:%M"),
            )
            await repo.defer_step(step_id, due)
            stats["deferred_hours"] += 1
            continue

        inbox = await pool.reserve(step_id)
        if inbox is None:
            due = next_business_day_start(now, lead.get("timezone"), hours)
            log.info("step %s deferred to %s: all inboxes at daily cap", step_id, due)
            await repo.defer_step(step_id, due)
            stats["deferred_cap"] += 1
            continue

        if await _send_step(step, lead, inbox, max_attempts):
            stats["sent"] += 1
        else:
            stats["failed"] += 1

    log.info("sender tick: %s", stats)
    return stats
