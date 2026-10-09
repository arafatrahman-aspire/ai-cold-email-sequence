"""The nurture background jobs (registered in app.workers.scheduler).

  generate_tick   write the emails due within about a day
  send_tick       send what is due: pause switch, eligibility re-check,
                  sending window, daily cap and test-mode allow-list first;
                  then hand-off notifications and health alerts
  poll_tick       read nurture's own mailbox (bounces, direct replies)
Score changes and reconciliation are in app.nurture.enroll.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.mail.base import MailError, OutgoingMessage
from app.nurture import alerts, cadence, content, exits, options, sending, writer
from app.nurture import repo as nrepo
from app.nurture.checks import Draft
from app.nurture.render import render
from app.scheduling import within_business_hours
from app.config import get_settings

log = logging.getLogger(__name__)


def _count(stats: dict[str, Any], key: str) -> None:
    stats[key] = stats.get(key, 0) + 1


def _as_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def needs_approval(cfg: dict[str, Any], source: str) -> bool:
    """Pilot mode: every AI draft waits for a person; sample mode: some do.
    Fallback emails are pre-approved."""
    if source != "ai":
        return False
    approval = cfg["approval"]
    if approval.get("mode") == "all":
        return True
    if approval.get("mode") == "sample":
        return random.random() < float(approval.get("sample_rate") or 0)
    return False


# --- writing ------------------------------------------------------------------------

async def generate_tick(now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cfg = await options.load(fresh=True)
    if cfg["paused"]:
        return {"paused": 1}
    stats: dict[str, Any] = {}
    horizon = cadence.generation_horizon(now, float(cfg["generate_hours_ahead"]), options.test_minutes(cfg))
    approved_resources = [r for r in await content.resources() if r.get("approved")]
    for message_id in await nrepo.claim_writing(horizon, int(cfg["batch_size"])):
        try:
            ctx = await nrepo.message_context(message_id)
            if not ctx or ctx["message"]["status"] != "writing":
                continue
            e, step = ctx["enrollment"], ctx["message"]["step"]
            ctx["brief"] = await content.brief(e["persona"], e["temperature"], step)
            ctx["fallback"] = await content.fallback(e["persona"], step)
            ctx["resources"] = approved_resources
            result = await writer.write(ctx, cfg)
            d = result.draft
            saved = await nrepo.save_message(
                message_id, "needs_approval" if needs_approval(cfg, result.source) else "ready",
                d.subject, d.preheader, d.body, d.resource_id, result.source,
                {"model": result.model, "prompt_version": writer.PROMPT_VERSION,
                 "brief_version": ctx["brief"].get("version"), "persona": e["persona"],
                 "temperature": e["temperature"], "attempts": result.attempts, "checks": result.log,
                 "judge": result.judge, "judge_score": result.judge_score,
                 "tokens_in": result.tokens_in, "tokens_out": result.tokens_out},
            )
            _count(stats, f"written_{result.source}" if saved else "discarded")
        except Exception:
            # Left 'writing': handed out again after 15 minutes.
            log.exception("nurture: writing message %s failed", message_id)
            _count(stats, "failed")
    if stats:
        log.info("nurture write tick: %s", stats)
        await alerts.check_fallback_rate(cfg)
    return stats


# --- sending -------------------------------------------------------------------------

async def _blocked(m: dict[str, Any], cfg: dict[str, Any]) -> Optional[str]:
    """The last check before a send. Acts on what it finds; None = may send."""
    eid = str(m["enrollment_id"])
    check = await nrepo.send_check(eid) or {}
    if check.get("status") != "active":
        return "not_active"
    if check.get("tier") == "Hot":
        await exits.handoff(eid, "hot_score")
        return "handoff"
    if check.get("suppressed"):
        await nrepo.set_status(eid, "exited", "suppressed")
        return "suppressed"
    if check.get("lead_status") in cfg["blocked_lead_statuses"]:
        await nrepo.set_status(eid, "exited", "lead_status")
        return "lead_status"
    if str(check.get("payment_status") or "").lower() in ("paid", "succeeded", "complete", "completed"):
        await nrepo.set_status(eid, "exited", "customer")
        return "customer"
    if check.get("in_cold_sequence"):
        await nrepo.set_status(eid, "held", "the lead is in the cold sequence")
        return "held_cold"
    return None


async def _send_one(m: dict[str, Any], cfg: dict[str, Any], ctx: dict[str, Any], now: datetime,
                    manual: bool = False) -> str:
    """Send one claimed email. ``manual`` (Send now) skips the sending window;
    every other check still applies."""
    mid, eid = str(m["message_id"]), str(m["enrollment_id"])
    blocked = await _blocked(m, cfg)
    if blocked:
        await nrepo.unsend(mid, "cancelled", error=f"not sent: {blocked}")
        return blocked

    test = options.test_minutes(cfg)
    if test and (m["email"] or "").lower() not in ctx["allow"]:
        await nrepo.unsend(mid, "ready", now + timedelta(minutes=5))
        return "test_mode_skip"
    minutes = test if m.get("test_mode") else None
    tz = cadence.lead_timezone(m.get("timezone"), cfg["default_timezone"])
    hours = options.window(cfg)
    if not manual and not minutes and not within_business_hours(now, tz, hours):
        await nrepo.unsend(mid, "ready", cadence.into_window(now, tz, hours))
        return "deferred_hours"
    if ctx["sent"] >= int(cfg["daily_cap"]):
        await nrepo.unsend(mid, "ready", cadence.into_window(now + timedelta(days=1), tz, hours))
        return "deferred_cap"

    resource = ctx["resources"].get(m["resource_id"]) if m.get("resource_id") else None
    unsubscribe_url = f"{ctx['public']}/n/u/{eid}"
    rendered = render(
        Draft(m["subject"] or "", m["preheader"] or "", m["body"] or "", m.get("resource_id"), "demo"),
        message_id=mid, first_name=m.get("first_name"), resource=resource, branding=cfg["branding"],
        public_url=ctx["public"], unsubscribe_url=unsubscribe_url,
        demo_fallback=get_settings().calcom_booking_url,
    )
    if rendered.problems:
        # e.g. its resource was deleted: cancelled, and the next write tick writes it again.
        await nrepo.unsend(mid, "cancelled", error="; ".join(rendered.problems))
        return "render_failed"

    try:
        result = await sending.transport().send(ctx["box"], OutgoingMessage(
            to_email=m["email"], subject=m["subject"], body_text=rendered.text,
            body_html=rendered.html, reply_to=ctx["reply_to"], unsubscribe_url=unsubscribe_url,
        ))
    except MailError as exc:
        if exc.permanent and "recipient refused" in str(exc).lower():
            await nrepo.unsend(mid, "failed", error=str(exc))
            await exits.unsubscribe_enrollment(eid, "bounced")
            return "bounced"
        if exc.permanent:
            await nrepo.unsend(mid, "failed", error=str(exc))
            await nrepo.set_status(eid, "held", f"send failed: {exc}")
            await nrepo.update(eid, needs_review=True, note=f"send failed: {exc}")
            return "send_failed"
        await nrepo.unsend(mid, "ready", now + timedelta(minutes=15), str(exc))
        return "send_retry"

    next_at = cadence.next_send(_as_dt(m["started_at"]), cfg["cadence"][m["temperature"]], int(m["step"]),
                                tz, hours, minutes, last_sent_at=now)
    await nrepo.mark_sent(mid, result.message_id, next_at)
    ctx["sent"] += 1
    return "sent"


async def _send_context(cfg: dict[str, Any], now: datetime) -> dict[str, Any]:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "sent": await nrepo.sent_since(day_start),
        "box": await sending.sending_inbox(),
        "reply_to": await sending.reply_to(),
        "public": sending.public_url(),
        "allow": options.allow_list(cfg),
        "resources": {r["id"]: r for r in await content.resources()},
    }


async def send_now(message_id: str) -> str:
    """Send one email immediately (APP_ENV=dev, the Review tab's "Send now").
    The sending window is skipped; the pause switch, eligibility re-check,
    test-mode allow-list and daily limit still apply."""
    now = datetime.now(timezone.utc)
    cfg = await options.load(fresh=True)
    if cfg["paused"]:
        return "paused"
    problems = await sending.problems(cfg)
    if problems:
        return "not configured: " + "; ".join(problems)
    rows = await nrepo.send_now(message_id)
    if not rows:
        return "not_waiting"
    m = rows[0]
    try:
        return await _send_one(m, cfg, await _send_context(cfg, now), now, manual=True)
    except Exception as exc:
        log.exception("nurture: send now of %s failed", message_id)
        await nrepo.unsend(message_id, "ready", now + timedelta(minutes=15), f"unexpected: {exc}")
        return "failed"


async def send_tick(now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cfg = await options.load(fresh=True)
    if cfg["paused"]:
        return {"paused": 1}
    stats: dict[str, Any] = {}
    problems = await sending.problems(cfg)
    if problems:
        return {"not_configured": problems}

    if get_settings().app_env == "dev":
        # Dev: nothing goes out on its own; "Send now" in Review sends one email.
        notified = await exits.notify_pending(cfg)
        return {"dev": "automatic sending is off (APP_ENV=dev)", **({"notified": notified} if notified else {})}

    ctx = await _send_context(cfg, now)
    room = int(cfg["daily_cap"]) - ctx["sent"]
    if room > 0:
        for m in await nrepo.claim_sending(now, min(int(cfg["batch_size"]), room)):
            try:
                _count(stats, await _send_one(m, cfg, ctx, now))
            except Exception as exc:
                log.exception("nurture: sending message %s failed", m.get("message_id"))
                await nrepo.unsend(str(m["message_id"]), "ready", now + timedelta(minutes=15), f"unexpected: {exc}")
                _count(stats, "failed")
    else:
        stats["daily_cap"] = 1

    notified = await exits.notify_pending(cfg)
    if notified:
        stats["notified"] = notified
    if stats:
        log.info("nurture send tick: %s", stats)
    return stats
