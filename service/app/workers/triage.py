"""Reply triage worker (OUT-05).

Each tick:
  1. wakes "not now" leads whose snooze is over (they get a fresh sequence),
  2. triages new replies: classify -> plan (app.triage.graph), then carries
     the plan out and writes the reply draft,
  3. sends drafts that were approved, or that reached their auto-send time
     without anyone approving or rejecting them.

The poller has already done the safe, immediate part when a reply arrived
(stopped the sequence, alerted a person). Triage adds the rest.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional

from app import repository as repo
from app import settings_store
from app.calendar.base import Attendee, CalendarError, Slot
from app.calendar.factory import get_calendar
from app.calendar.slots import describe_slot, find_slot, pick_near, pick_slots
from app.mail import templates
from app.mail.base import Inbox, MailError, OutgoingMessage
from app.mail.factory import get_sender
from app.nurture import replies as nurture_replies
from app.scheduling import advance_business_days, clamp_into_window, resolve_timezone
from app.triage.conversation import format_history, strip_quoted
from app.triage.drafting import DraftInput, write_draft
from app.triage.graph import triage

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timing helpers (pure)
# ---------------------------------------------------------------------------

def auto_send_time(now: datetime, delay_minutes: int, tz_name: Optional[str], hours) -> datetime:
    """``delay_minutes`` from now, moved into the lead's business hours."""
    tz = resolve_timezone(tz_name)
    local = (now + timedelta(minutes=max(0, delay_minutes))).astimezone(tz)
    return clamp_into_window(local, hours).astimezone(timezone.utc)


def return_time(return_date: Optional[str], now: datetime, tz_name: Optional[str], hours,
                default_days: int) -> datetime:
    """When to resume after an out-of-office: the business day after they are
    back, at the start of business hours; ``default_days`` if no date given."""
    tz = resolve_timezone(tz_name)
    if return_date:
        back = datetime.fromisoformat(return_date).date()
        resume = advance_business_days(back, 1, hours)
    else:
        resume = advance_business_days((now + timedelta(days=default_days)).astimezone(tz).date(), 0, hours)
    return datetime.combine(resume, time(hour=hours.start_hour), tzinfo=tz).astimezone(timezone.utc)


def _attendee(lead: dict, email: str) -> Attendee:
    name = " ".join(p for p in (lead.get("first_name"), lead.get("last_name")) if p) or email
    return Attendee(name=name, email=email, timezone=lead.get("timezone") or "UTC")


def _split_name(name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    parts = (name or "").split()
    if not parts:
        return None, None
    return parts[0], " ".join(parts[1:]) or None


# ---------------------------------------------------------------------------
# Triage of one reply
# ---------------------------------------------------------------------------

# Redraft instructions waiting for their reply's next triage, by event id. The
# redraft endpoint runs triage straight away in this process, so they are
# used within the same request; nothing needs to survive a restart.
_guidance: dict[str, str] = {}


def set_guidance(event_id: str, text: str) -> None:
    _guidance[event_id] = text


def _as_dt(value) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def process_event(event: dict, cfg: dict, inboxes: dict[str, Inbox],
                        now: Optional[datetime] = None) -> str:
    """Triage one reply and act on it. Returns the outcome for the tick stats."""
    now = now or datetime.now(timezone.utc)
    event_id, lead_id = str(event["id"]), str(event["lead_id"])
    lead = await repo.lead_for_step(lead_id)
    if lead is None:
        await repo.save_triage(event_id, "other", 0.0, "", {}, "failed", "lead is not enrolled")
        return "failed"

    tz = lead.get("timezone") or "UTC"
    hours = await settings_store.business_hours(tz)
    ctx = await repo.triage_context(lead_id)
    offered = [Slot.from_json(x) for x in ctx.get("offered_slots") or []]

    # The thread so far, so the AI reads "yes, Tuesday works" in context.
    history_text: Optional[str] = None
    wanted = int(cfg.get("context_messages", 4) or 0)
    if wanted > 0:
        try:
            history = await repo.conversation_history(
                lead_id, event.get("received_at") or now.isoformat(), event_id, wanted
            )
            if history:
                lead_name = " ".join(p for p in (lead.get("first_name"), lead.get("last_name")) if p)
                history_text = format_history(history, lead_name=lead_name, lead_timezone=tz)
        except Exception as exc:  # e.g. 0007_conversation.sql not run yet
            log.warning("conversation history unavailable (%s); using our last email only", exc)
    raw_body = event.get("snippet") or ""
    reply_body = strip_quoted(raw_body) or raw_body

    try:
        state = await triage({
            "subject": event.get("subject") or "",
            "body": reply_body,
            "event_type": event.get("event_type") or "reply",
            "lead_timezone": tz,
            "today": now.astimezone(resolve_timezone(tz)).date(),
            "last_sent": ctx.get("last_sent"),
            "history": history_text,
            "offered": [describe_slot(s, tz) for s in offered],
            "forced_category": event.get("human_category"),
            "has_meeting": bool(ctx.get("has_meeting")),
            "min_confidence": float(cfg["min_confidence"]),
        })
    except Exception as exc:
        # The AI is unreachable (quota, outage). Nothing has been done yet, so
        # leave the reply claimed: claim_untriaged picks it up again after 15
        # minutes. Drafts and other replies carry on in the meantime.
        log.warning("could not classify reply %s (%s); retrying in 15 minutes", event_id, exc)
        return "deferred"
    result, plan = state["result"], state["plan"]
    extracted = dict(result.extracted)
    done: list[str] = []
    meeting_cfg = cfg["meeting"]
    draft_kind = plan.draft_kind
    needs_human = plan.needs_human
    try:
        calendar = get_calendar()
    except CalendarError as exc:
        # Misconfigured calendar (.env): carry on without it, and flag replies
        # that needed it so a person books the meeting.
        calendar = None
        if any(a in plan.actions for a in ("offer_slots", "book_offered", "book_proposed")):
            needs_human = True
            done.append(f"calendar not usable ({exc})")
    slot_lines: list[str] = []
    new_offer: list[Slot] = []
    booked: Optional[str] = None
    meeting_url: Optional[str] = None
    referral_name: Optional[str] = None
    reply_to = event.get("from_email") or lead.get("email")

    async def offer_slots(around: Optional[datetime] = None) -> None:
        nonlocal new_offer, slot_lines, needs_human
        if calendar is None:
            return
        start = max(now, (around or now) - timedelta(days=1))
        try:
            found = await calendar.free_slots(start, start + timedelta(days=int(meeting_cfg["days_ahead"])))
        except CalendarError as exc:
            # Calendar down: the draft asks for times and waits for a person.
            needs_human = True
            done.append(f"calendar unavailable ({exc})")
            return
        count, notice = int(meeting_cfg["slots_to_offer"]), int(meeting_cfg["min_notice_hours"])
        if around is not None:
            new_offer = pick_near(found, around, tz, hours, now, count, notice)
        if around is None or not new_offer:
            new_offer = pick_slots(found, tz, hours, now, count, notice)
        slot_lines = [describe_slot(s, tz) for s in new_offer]
        if not new_offer:
            # Nothing to offer: the draft asks for their times, and a person
            # checks it first instead of it going out on its own.
            needs_human = True
            done.append(
                f"no free calendar time falls in their business hours "
                f"({hours.start_hour}:00-{hours.end_hour}:00 {tz})" if found
                else f"the calendar has no free time in the next {meeting_cfg['days_ahead']} days"
            )

    async def book(slot: Slot) -> bool:
        nonlocal booked, meeting_url
        b = await calendar.book(slot.start, _attendee(lead, reply_to), f"Booked from a reply: {result.summary}")
        await repo.record_meeting(lead_id, event_id, b.provider, b.external_id, b.start, b.end,
                                  reply_to, b.meeting_url)
        await repo.cancel_lead_drafts(lead_id, "meeting booked")
        booked, meeting_url = describe_slot(Slot(b.start, b.end), tz), b.meeting_url
        done.append(f"booked {booked}")
        return True

    async def reschedule() -> None:
        """Move their booked meeting to the time they now chose, when it is free.
        Otherwise the meeting stays and the nearest free times are offered."""
        nonlocal draft_kind, needs_human, booked, meeting_url
        current = await repo.upcoming_meeting(lead_id, now)
        if calendar is None or current is None or current.get("provider") != calendar.name:
            draft_kind, needs_human = None, True
            done.append("wants to move their meeting; a person should reschedule it"
                        + ("" if current else " (no upcoming meeting found)"))
            return
        chosen = extracted.get("chosen_slot")
        if isinstance(chosen, int) and 1 <= chosen <= len(offered):
            slot = offered[chosen - 1]
        else:
            when = datetime.fromisoformat(extracted["proposed_time"])
            try:
                free = await calendar.free_slots(when - timedelta(minutes=1), when + timedelta(minutes=1))
            except CalendarError as exc:
                draft_kind, needs_human = None, True
                done.append(f"calendar unavailable ({exc}); a person should reschedule")
                return
            slot = find_slot(free, when)
            if slot is None:
                draft_kind = "slot_unavailable"
                done.append("their new time was not free; the meeting stays as booked")
                await offer_slots(around=when)
                return
        old_start = _as_dt(current["start_at"])
        old_end = _as_dt(current["end_at"]) if current.get("end_at") else old_start + (slot.end - slot.start)
        if abs((slot.start - old_start).total_seconds()) < 60:
            draft_kind = None
            done.append("they confirmed the time already booked")
            return
        try:
            b = await calendar.reschedule(current["external_id"], slot.start, _attendee(lead, reply_to),
                                          f"Moved at their request: {result.summary}")
        except CalendarError as exc:
            if exc.slot_taken:
                draft_kind = "slot_unavailable"
                done.append("their new time was just taken; the meeting stays as booked")
                await offer_slots(around=slot.start)
            else:
                draft_kind, needs_human = None, True
                done.append(f"reschedule failed ({exc}); a person should move it")
            return
        if b.external_id != current["external_id"]:
            # Rebooked under a new id (Cal.com): record it, then retire the old one.
            await repo.record_meeting(lead_id, event_id, b.provider, b.external_id, b.start, b.end,
                                      reply_to, b.meeting_url)
            await repo.sync_meeting(b.provider, current["external_id"], old_start, old_end, "cancelled",
                                    current.get("attendee_email"), current.get("attendee_name"), None, True)
        else:
            await repo.sync_meeting(b.provider, b.external_id, b.start, b.end, "booked",
                                    current.get("attendee_email") or reply_to, current.get("attendee_name"),
                                    b.meeting_url, True)
        await repo.cancel_lead_drafts(lead_id, "meeting rescheduled")
        was = describe_slot(Slot(old_start, old_end), tz)
        booked = f"{describe_slot(Slot(b.start, b.end), tz)} (moved from {was})"
        meeting_url = b.meeting_url or current.get("meeting_url")
        done.append(f"rescheduled to {describe_slot(Slot(b.start, b.end), tz)} (was {was})")

    try:
        for action in plan.actions:
            if action == "suppress":
                await repo.suppress(lead["email"], "unsubscribed")
                if reply_to and reply_to.lower() != (lead.get("email") or "").lower():
                    await repo.suppress(reply_to, "unsubscribed")
                await repo.cancel_sequence(lead_id, "unsubscribed", "asked to stop (triage)")
                done.append("suppressed")
            elif action == "cancel_drafts":
                if await repo.cancel_lead_drafts(lead_id, "lead unsubscribed"):
                    done.append("drafts cancelled")
            elif action == "mark_dnc":
                if await repo.mark_dnc(lead_id):
                    done.append("leads.status set to DNC")
            elif action == "pause_ooo":
                until = return_time(extracted.get("return_date"), now, tz, hours, int(cfg["ooo_default_days"]))
                moved = await repo.pause_sequence_until(lead_id, until)
                extracted["resume_at"] = until.isoformat()
                done.append(f"sequence paused until {until:%Y-%m-%d}" if moved else "nothing scheduled to pause")
            elif action == "snooze":
                until = now + timedelta(days=int(cfg["not_now_days"]))
                await repo.snooze_lead(lead_id, until, f"not now: {result.summary}"[:500])
                extracted["snoozed_until"] = until.isoformat()
                done.append(f"snoozed until {until:%Y-%m-%d}")
            elif action == "add_referral":
                ref = extracted.get("referral") or {}
                first, last = _split_name(ref.get("name"))
                referred_by = " ".join(p for p in (lead.get("first_name"), lead.get("last_name")) if p) or lead["email"]
                outcome, _ = await repo.add_referral(
                    lead_id, ref["email"], first, last, ref.get("job_title"),
                    lead.get("company"), None, referred_by,
                )
                referral_name = ref.get("name") or ref["email"]
                extracted["referral_outcome"] = outcome
                done.append(f"referral {ref['email']}: {outcome.replace('_', ' ')}")
            elif action in ("book_offered", "book_proposed"):
                if calendar is None:
                    draft_kind, needs_human = "meeting_offer", True
                    done.append("no calendar configured: asked for times instead")
                    continue
                if action == "book_offered":
                    slot = offered[extracted["chosen_slot"] - 1]
                    if slot.start <= now + timedelta(minutes=30):
                        # They answered too late: that time has passed.
                        draft_kind = "slot_unavailable"
                        done.append("the time they picked has passed")
                        await offer_slots()
                        continue
                else:
                    when = datetime.fromisoformat(extracted["proposed_time"])
                    try:
                        free = await calendar.free_slots(when - timedelta(minutes=1), when + timedelta(minutes=1))
                    except CalendarError as exc:
                        draft_kind, needs_human = "meeting_offer", True
                        done.append(f"calendar unavailable ({exc})")
                        continue
                    slot = find_slot(free, when)
                    if slot is None:
                        draft_kind = "slot_unavailable"
                        done.append("their time was not free")
                        await offer_slots(around=when)
                        continue
                try:
                    await book(slot)
                except CalendarError as exc:
                    if not exc.slot_taken:
                        draft_kind, needs_human = "meeting_offer", True
                        done.append(f"booking failed ({exc}); a person should book it")
                        continue
                    draft_kind = "slot_unavailable"
                    done.append("slot was taken")
                    await offer_slots()
            elif action == "reschedule":
                await reschedule()
            elif action == "offer_slots":
                await offer_slots()
                if new_offer:
                    done.append(f"offered {len(new_offer)} slot(s)")

        draft_id = None
        if draft_kind:
            inbox = inboxes.get(str(event["inbox_id"]))
            if inbox is None:
                raise RuntimeError("the inbox that received this reply is not active")
            sign_off = (inbox.display_name or "").split()[0] if inbox.display_name else ""
            subject, body, source = await write_draft(DraftInput(
                kind=draft_kind,
                first_name=lead.get("first_name"),
                sign_off=sign_off,
                reply_subject=event.get("subject") or "",
                reply_body=reply_body,
                slot_lines=slot_lines,
                booking_link=calendar.booking_link() if calendar else None,
                booked=booked,
                meeting_url=meeting_url,
                objection=extracted.get("objection"),
                referral=referral_name,
                snooze_days=int(cfg["not_now_days"]),
                history=history_text,
                guidance=_guidance.pop(event_id, None),
                examples=await repo.draft_examples(draft_kind),
                snippets=await settings_store.triage_snippets(),
            ))
            auto = (not needs_human and draft_kind in cfg["auto_send_kinds"])
            refs = [r for r in (event.get("in_reply_to"), event.get("message_id")) if r]
            draft_id = await repo.create_draft(
                event_id, lead_id, str(event["inbox_id"]), draft_kind, reply_to, subject, body,
                [s.to_json() for s in new_offer], event.get("message_id"), refs,
                auto_send_time(now, int(cfg["auto_send_delay_minutes"]), tz, hours) if auto else None,
            )
            done.append(f"{draft_kind.replace('_', ' ')} draft ({source})"
                        + ("" if auto else ", waits for approval"))

        extracted.update({"actions": done, "note": plan.note, "model": result.model})
        if draft_id:
            extracted["draft_id"] = draft_id
        status = "needs_human" if needs_human else "done"
        await repo.save_triage(event_id, result.category, result.confidence, result.summary, extracted, status)
        log.info("triaged reply %s as %s (%.2f): %s", event_id, result.category, result.confidence, "; ".join(done))
        return result.category
    except Exception as exc:
        log.exception("triage of reply %s failed", event_id)
        extracted.update({"actions": done})
        await repo.save_triage(event_id, result.category, result.confidence, result.summary,
                               extracted, "failed", str(exc))
        return "failed"


# ---------------------------------------------------------------------------
# Sending drafts
# ---------------------------------------------------------------------------

async def compose_answer(subject: str, body: str, inbox: Inbox) -> templates.Email:
    """An answer to a reply as sent: the body (it ends with the sign-off name)
    and the signature. No opt-out line: they wrote to us."""
    style = await settings_store.email_style()
    mail = templates.personal(body, templates.Signature.from_settings(style, inbox.display_name or ""),
                              accent=templates.accent_color(style.get("accent")), subject=subject,
                              layout=templates.layout_name(style.get("layout")))
    return mail if style.get("html", True) else templates.Email(mail.text, "")


async def send_due_drafts(inboxes: dict[str, Inbox]) -> dict[str, int]:
    stats: dict[str, int] = {}

    def count(k: str) -> None:
        stats[k] = stats.get(k, 0) + 1

    for d in await repo.claim_drafts_to_send(10):
        draft_id, lead_id = str(d["id"]), str(d["lead_id"])
        try:
            # Auto-sends only: if the lead has written again, a newer draft
            # replaces this one. (A person's approval stands.)
            if d["status_before"] != "approved" and await repo.newer_reply_exists(lead_id, d["created_at"]):
                await repo.finish_draft(draft_id, "cancelled", error="the lead wrote again; see the newer draft")
                count("superseded")
                continue
            lead = await repo.lead_for_step(lead_id)
            if await repo.is_suppressed(d["to_email"]) or (lead and lead.get("lead_status") == "DNC"):
                await repo.finish_draft(draft_id, "cancelled", error="address suppressed or lead is DNC")
                count("blocked")
                continue
            inbox = inboxes.get(str(d["inbox_id"]))
            if inbox is None:
                await repo.finish_draft(draft_id, "failed", error="sending inbox is not active")
                count("failed")
                continue
            mail = await compose_answer(d["subject"], d["body"], inbox)
            result = await get_sender().send(inbox, OutgoingMessage(
                to_email=d["to_email"],
                subject=d["subject"],
                body_text=mail.text,
                body_html=mail.html,
                in_reply_to=d.get("in_reply_to"),
                references=list(d.get("references_ids") or []),
            ))
            await repo.finish_draft(draft_id, "sent", message_id=result.message_id)
            count("sent")
        except MailError as exc:
            await repo.finish_draft(draft_id, "failed", error=str(exc))
            count("failed")
        except Exception as exc:
            log.exception("sending draft %s failed", draft_id)
            await repo.finish_draft(draft_id, "failed", error=f"unexpected: {exc}")
            count("failed")
    return stats


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------

async def run_once() -> dict[str, Any]:
    cfg = await settings_store.triage_config()
    stats: dict[str, Any] = {}
    woken = await repo.wake_snoozed()
    if woken:
        stats["woken"] = woken
    if not cfg.get("enabled", True):
        stats["disabled"] = 1
        return stats

    inboxes = {i.id: i for i in await repo.cached_inboxes()}
    for event in await repo.claim_untriaged(10):
        try:
            outcome = await process_event(event, cfg, inboxes)
        except Exception:
            # e.g. the database blinked while loading the lead: retried later.
            log.exception("triage of reply %s crashed", event.get("id"))
            outcome = "deferred"
        stats[outcome] = stats.get(outcome, 0) + 1

    # Replies to Email Nurture emails: same classifier, nurture's own actions
    # (no reply drafts). Never raises.
    for key, value in (await nurture_replies.triage_pending(cfg)).items():
        stats[f"nurture_{key}"] = value

    if bool(await settings_store.get("sequence_enabled")):
        for k, v in (await send_due_drafts(inboxes)).items():
            stats[f"drafts_{k}"] = v
    if stats:
        log.info("triage tick: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# From a reply's card in the console: see the times, book one
# ---------------------------------------------------------------------------

class CardError(Exception):
    """Something the person should read (not a crash)."""


async def _event_and_lead(event_id: str) -> tuple[dict, dict]:
    event = await repo.inbox_event(event_id)
    if event is None:
        raise CardError("this reply no longer exists")
    if not event.get("lead_id"):
        raise CardError("this reply is not linked to a cold-sequence lead")
    lead = await repo.lead_for_step(str(event["lead_id"]))
    if lead is None:
        raise CardError("the lead is no longer enrolled")
    return event, lead


async def times_for_event(event_id: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """The times worth one click on a reply card: what we offered last, what
    they proposed, and the next free times inside their business hours.
    ``problem`` says in plain words why no time can be offered."""
    now = now or datetime.now(timezone.utc)
    event, lead = await _event_and_lead(event_id)
    tz = lead.get("timezone") or "UTC"
    hours = await settings_store.business_hours(tz)
    ctx = await repo.triage_context(str(event["lead_id"]))
    out: dict[str, Any] = {
        "timezone": tz, "timezone_source": lead.get("timezone_source"),
        "business_hours": f"{hours.start_hour}:00-{hours.end_hour}:00",
        "has_meeting": bool(ctx.get("has_meeting")),
        "offered": [], "proposed": None, "suggestions": [], "problem": None,
    }
    try:
        calendar = get_calendar()
    except CalendarError as exc:
        out["problem"] = f"The calendar is not set up ({exc})"
        return out
    if calendar is None:
        out["problem"] = "No calendar is connected (CALENDAR_PROVIDER=none)"
        return out
    meeting_cfg = (await settings_store.triage_config())["meeting"]
    try:
        free = await calendar.free_slots(now, now + timedelta(days=int(meeting_cfg["days_ahead"])))
    except CalendarError as exc:
        out["problem"] = f"The calendar could not be read ({exc})"
        return out

    def entry(slot: Slot) -> dict[str, Any]:
        return {**slot.to_json(), "label": describe_slot(slot, tz),
                "free": find_slot(free, slot.start) is not None}

    earliest = now + timedelta(minutes=30)
    out["offered"] = [entry(s) for s in (Slot.from_json(x) for x in ctx.get("offered_slots") or [])
                      if s.start > earliest]
    proposed = (event.get("extracted") or {}).get("proposed_time")
    if proposed:
        when = datetime.fromisoformat(proposed)
        slot = find_slot(free, when)
        out["proposed"] = (entry(slot) if slot else
                           {"start": when.isoformat(), "label": describe_slot(Slot(when, when + timedelta(minutes=30)), tz),
                            "free": False})
    picked = pick_slots(free, tz, hours, now, 3, int(meeting_cfg["min_notice_hours"]))
    out["suggestions"] = [entry(s) for s in picked]
    if not free:
        out["problem"] = f"The calendar has no free time in the next {meeting_cfg['days_ahead']} days"
    elif not picked:
        out["problem"] = (f"None of your free calendar time falls in this lead's business hours "
                          f"({out['business_hours']} {tz}). Check the lead's timezone, or add hours "
                          f"to your calendar that suit them.")
    return out


async def book_from_card(event_id: str, start: datetime, user: str,
                         now: Optional[datetime] = None) -> dict[str, Any]:
    """Book the meeting a person picked on a reply card, then write the
    confirmation for the lead (it follows the usual send rules)."""
    now = now or datetime.now(timezone.utc)
    event, lead = await _event_and_lead(event_id)
    lead_id = str(event["lead_id"])
    if (await repo.triage_context(lead_id)).get("has_meeting"):
        raise CardError("this lead already has a meeting booked")
    try:
        calendar = get_calendar()
    except CalendarError as exc:
        raise CardError(f"the calendar is not set up ({exc})")
    if calendar is None:
        raise CardError("no calendar is connected")
    if start <= now + timedelta(minutes=30):
        raise CardError("that time has passed or is too soon")
    try:
        free = await calendar.free_slots(start - timedelta(minutes=1), start + timedelta(minutes=1))
    except CalendarError as exc:
        raise CardError(f"the calendar could not be read ({exc})")
    slot = find_slot(free, start)
    if slot is None:
        raise CardError("that time is no longer free in your calendar")

    tz = lead.get("timezone") or "UTC"
    reply_to = event.get("from_email") or lead.get("email")
    try:
        b = await calendar.book(slot.start, _attendee(lead, reply_to), f"Booked by {user} from the console")
    except CalendarError as exc:
        raise CardError("that time was just taken" if exc.slot_taken else f"booking failed ({exc})")
    await repo.record_meeting(lead_id, event_id, b.provider, b.external_id, b.start, b.end, reply_to, b.meeting_url)
    await repo.cancel_lead_drafts(lead_id, "meeting booked")
    booked = describe_slot(Slot(b.start, b.end), tz)
    log.info("meeting booked from the console by %s for reply %s: %s", user, event_id, booked)

    # Tell the lead, like triage does after booking.
    result: dict[str, Any] = {"booked": booked, "meeting_url": b.meeting_url, "draft_id": None}
    inbox = next((i for i in await repo.cached_inboxes() if i.id == str(event["inbox_id"])), None)
    if inbox is None:
        result["note"] = "Booked. The inbox that received the reply is not active, so no confirmation was written."
        return result
    cfg = await settings_store.triage_config()
    hours = await settings_store.business_hours(tz)
    raw = event.get("snippet") or ""
    subject, body, _ = await write_draft(DraftInput(
        kind="booking_confirmation",
        first_name=lead.get("first_name"),
        sign_off=(inbox.display_name or "").split()[0] if inbox.display_name else "",
        reply_subject=event.get("subject") or "",
        reply_body=strip_quoted(raw) or raw,
        slot_lines=[],
        booking_link=calendar.booking_link(),
        booked=booked,
        meeting_url=b.meeting_url,
        examples=await repo.draft_examples("booking_confirmation"),
    ))
    auto = "booking_confirmation" in cfg["auto_send_kinds"]
    refs = [r for r in (event.get("in_reply_to"), event.get("message_id")) if r]
    result["draft_id"] = await repo.create_draft(
        event_id, lead_id, str(event["inbox_id"]), "booking_confirmation", reply_to, subject, body,
        [], event.get("message_id"), refs,
        auto_send_time(now, int(cfg["auto_send_delay_minutes"]), tz, hours) if auto else None,
    )
    return result
