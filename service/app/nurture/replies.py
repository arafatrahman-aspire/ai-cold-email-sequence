"""Replies to nurture emails: the poller and Reply Triage integration.

Nurture emails carry a Reply-To that Reply Triage already reads. Two hooks,
both no-ops for anything that is not about a nurture email, and both unable
to break the cold path (any error here means "not nurture"):

  handle_inbound  (called first by the poller) records the reply against the
                  nurture enrollment, holds the sequence until it is
                  classified, and acts at once on bounces and opt-outs.
  triage_pending  (called by the Reply Triage worker after its own replies)
                  classifies nurture replies with the same classifier and
                  maps the result to a nurture action:
                    interested           -> hand off to sales
                    not_now              -> continue the sequence
                    objection            -> stop nurture (no global suppression), flag
                    unsubscribe          -> stop and suppress everywhere
                    out_of_office        -> next email at least 5 days later
                    wrong_person / other -> flag for a person, sequence held
                  No AI reply is drafted to a nurture reply: sales or a person
                  answers.

poll_tick reads nurture's own mailbox, where bounces (and replies that
ignore Reply-To) arrive.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app import repository as repo
from app.db import DatabaseError
from app.mail.base import Inbox, IncomingMessage
from app.nurture import exits, options, sending
from app.nurture import repo as nrepo
from app.scheduling import resolve_timezone
from app.triage.classify import classify_reply
from app.triage.conversation import format_history, strip_quoted

log = logging.getLogger(__name__)


def _thread_ids(message: IncomingMessage) -> list[str]:
    return [m for m in ([message.in_reply_to] + list(message.references)) if m]


async def handle_inbound(inbox: Inbox, message: IncomingMessage, event_type: str) -> Optional[str]:
    """The poller's outcome if this message belongs to nurture, else None."""
    try:
        ids = _thread_ids(message)
        # A reply in a cold-sequence thread stays with the cold sequence.
        if ids and await repo.match_lead_by_message_ids(ids):
            return None
        address = message.bounced_recipient if (message.is_bounce_report and message.bounced_recipient) \
            else message.from_email
        eid = await nrepo.match_reply(ids, address or "")
        if not eid:
            return None
        is_new = await nrepo.save_reply(
            inbox.id, eid, event_type, message.from_email, message.to_email, message.subject,
            message.message_id, message.in_reply_to, (message.body_snippet or "")[:2000], message.received_at,
        )
        if not is_new:
            return "nurture_duplicate"
        if event_type == "bounce":
            if message.bounce_is_permanent:
                await exits.unsubscribe_enrollment(eid, "bounced")
                return "nurture_hard_bounce"
            return "nurture_soft_bounce"
        if event_type == "unsubscribe":
            await exits.unsubscribe_enrollment(eid, "unsubscribed")
            return "nurture_unsubscribe"
        if event_type == "reply":
            await nrepo.set_status(eid, "held", "replied; waiting for Reply Triage")
            return "nurture_reply"
        return f"nurture_{event_type}"
    except DatabaseError as exc:
        # e.g. the nurture migrations (0010-0014) not run yet: the cold path carries on as before.
        log.debug("nurture inbound check skipped: %s", exc)
        return None
    except Exception:
        log.exception("nurture inbound handling failed; treating the message as non-nurture")
        return None


async def _act(eid: str, category: str, confident: bool, result: Any, cfg: dict[str, Any],
               now: datetime) -> tuple[list[str], bool]:
    """Carry out the mapping. Returns (actions, needs_human)."""
    done: list[str] = []
    if category == "unsubscribe":
        await exits.unsubscribe_enrollment(eid, "unsubscribed")
        return ["suppressed everywhere; nurture stopped"], False
    if category == "out_of_office":
        until = now + timedelta(days=int(cfg["ooo_delay_days"]))
        back = (result.extracted or {}).get("return_date")
        if back:
            try:
                until = max(until, datetime.fromisoformat(back).replace(tzinfo=timezone.utc) + timedelta(days=1))
            except ValueError:
                pass
        await nrepo.set_status(eid, "active", "out of office: next email delayed", until)
        return [f"next email delayed to {until:%Y-%m-%d}"], False
    if not confident:
        await nrepo.update(eid, needs_review=True, note=f"reply looks like {category}; please check")
        return ["held for a person (low confidence)"], True
    if category == "interested":
        handed = await exits.handoff(eid, "reply_interested")
        return ["handed off to sales" if handed else "already handed off"], False
    if category == "not_now":
        await nrepo.set_status(eid, "active", "replied 'not now': sequence continues")
        return ["sequence continues"], False
    if category == "objection":
        await nrepo.update(eid, needs_review=True, note=f"objection: {result.summary}"[:300])
        await nrepo.set_status(eid, "exited", "not_interested")
        return ["nurture stopped (not interested); flagged"], True
    # wrong_person, other
    await nrepo.update(eid, needs_review=True, note=f"{category}: {result.summary}"[:300])
    done.append("held for a person")
    return done, True


async def _triage_one(event: dict[str, Any], cfg: dict[str, Any], now: datetime) -> str:
    event_id, eid = str(event["id"]), str(event["nurture_enrollment_id"])
    detail = await nrepo.detail(eid) or {}
    e = detail.get("lead") or {}
    tz = e.get("timezone") or "UTC"
    history_text = None
    wanted = int(cfg.get("context_messages", 4) or 0)
    if wanted:
        history = await nrepo.thread(eid, event.get("received_at") or now.isoformat(), event_id, wanted)
        if history:
            name = " ".join(p for p in (e.get("first_name"), e.get("last_name")) if p)
            history_text = format_history(history, lead_name=name, lead_timezone=tz)
    raw = event.get("snippet") or ""
    try:
        result = await classify_reply(
            event.get("subject") or "", strip_quoted(raw) or raw,
            lead_timezone=tz, today=now.astimezone(resolve_timezone(tz)).date(),
            history=history_text, forced_category=event.get("human_category"),
        )
    except Exception as exc:
        log.warning("nurture reply %s not classified (%s); retrying in 15 minutes", event_id, exc)
        return "deferred"

    category = result.category
    if event.get("event_type") == "auto_reply" and category != "unsubscribe":
        category = "out_of_office"
    confident = bool(event.get("human_category")) or result.confidence >= float(cfg["min_confidence"]) \
        or category == "out_of_office"
    try:
        actions, needs_human = await _act(eid, category, confident, result, cfg, now)
        extracted = {**(result.extracted or {}), "actions": actions, "source": "nurture", "model": result.model}
        await repo.save_triage(event_id, category, result.confidence, result.summary, extracted,
                               "needs_human" if needs_human else "done")
        return category
    except Exception as exc:
        log.exception("acting on nurture reply %s failed", event_id)
        await repo.save_triage(event_id, category, result.confidence, result.summary,
                               {"source": "nurture"}, "failed", str(exc)[:300])
        return "failed"


async def triage_pending(triage_cfg: dict[str, Any], now: Optional[datetime] = None) -> dict[str, int]:
    """Classify and act on waiting nurture replies. Never raises."""
    now = now or datetime.now(timezone.utc)
    stats: dict[str, int] = {}
    try:
        events = await nrepo.claim_replies(10)
    except Exception as exc:
        log.debug("nurture replies skipped: %s", exc)
        return stats
    if not events:
        return stats
    cfg = {**await options.load(), **triage_cfg}
    for event in events:
        try:
            outcome = await _triage_one(event, cfg, now)
        except Exception:
            log.exception("nurture reply %s crashed", event.get("id"))
            outcome = "deferred"
        stats[outcome] = stats.get(outcome, 0) + 1
    return stats


async def poll_tick() -> dict[str, Any]:
    """Read the nurture mailbox (bounces arrive here) with the cold poller's
    own code. Skipped when the nurture account is not set up."""
    from app.workers import poller

    box = sending.mailbox()
    if box is None:
        return {}
    row = await nrepo.register_mailbox(box.email, box.display_name or "")
    if not row:
        return {}
    if row.get("active"):
        return {"skipped": "this mailbox is a cold inbox and is read by the cold poller"}
    inbox = replace(box, id=str(row["id"]), poll_cursor=int(row.get("poll_cursor") or 0))
    stats, error = await poller.poll_inbox(inbox)
    if error:
        stats["error"] = error
    return stats
