"""Leaving nurture.

Hand-off to sales (Hot score, pricing or demo click, interested reply, or a
person's "hand off now") is one database call that ends the enrollment and
cancels unsent emails, atomically: whichever signal arrives first wins and
the rest find the enrollment already ended. Sales is emailed afterwards by
the send job (notify_pending), so a click's redirect never waits for it.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.llm.factory import get_gateway
from app.nurture import alerts
from app.nurture import repo as nrepo
from app.nurture.options import PERSONAS
from app.nurture.safety import DATA_RULE, clean, data_block

log = logging.getLogger(__name__)

# What started a hand-off, in the words the console and notification use.
TRIGGERS = {
    "hot_score": "Lead score became Hot",
    "pricing_click": "Clicked the pricing link",
    "demo_click": "Clicked the demo link",
    "reply_interested": "Replied with interest",
    "manual": "Handed off by a person",
}


async def handoff(enrollment_id: str, trigger: str) -> bool:
    """Hand the lead to sales. False when it had already been handed off or ended."""
    done = await nrepo.handoff(enrollment_id, trigger)
    if done:
        log.info("nurture: enrollment %s handed off (%s)", enrollment_id, trigger)
    return done


async def unsubscribe_enrollment(enrollment_id: str, reason: str = "unsubscribed") -> bool:
    """Blocked for every module (the suppression list), the shared lead marked
    DNC for an unsubscribe, and the enrollment ended."""
    return await nrepo.suppress(enrollment_id, reason)


# --- notifying sales ------------------------------------------------------------------

SUMMARY_SYSTEM = """\
You brief a salesperson about a lead that a nurture email sequence has just
handed over. In 3-5 plain sentences: who they are (role, company), what they
engaged with, what triggered the hand-off, and a sensible first step for the
call. Use only the facts given; do not invent anything. {data_rule}
Return ONLY: {{"summary": "..."}}
"""


def _facts(detail: dict[str, Any]) -> dict[str, Any]:
    e, lead = detail.get("enrollment") or {}, detail.get("lead") or {}
    sent = [m for m in detail.get("messages") or [] if m.get("status") == "sent"]
    clicks = [v.get("detail") or {} for v in detail.get("events") or []
              if v.get("kind") == "click" and not (v.get("detail") or {}).get("bot")]
    return {
        "name": clean(" ".join(p for p in (lead.get("first_name"), lead.get("last_name")) if p), 80),
        "email": e.get("email"),
        "job_title": clean(lead.get("job_title"), 120),
        "company": clean(lead.get("company"), 120),
        "persona": PERSONAS.get(e.get("persona"), e.get("persona")),
        "track": e.get("temperature"),
        "current_score": lead.get("tier"),
        "trigger": TRIGGERS.get(e.get("exit_reason"), e.get("exit_reason")),
        "emails_received": [{"step": m["step"], "subject": clean(m.get("subject"), 120)} for m in sent],
        "clicked": [{"step": c.get("step"), "link": c.get("link")} for c in clicks],
        "replies": [{"category": r.get("category"), "text": clean(r.get("snippet"), 400)}
                    for r in detail.get("replies") or [] if r.get("event_type") == "reply"],
    }


def plain_summary(facts: dict[str, Any]) -> str:
    who = facts["name"] or facts["email"]
    role = ", ".join(p for p in (facts["job_title"], facts["company"]) if p)
    clicked = ", ".join(sorted({c["link"] for c in facts["clicked"] if c.get("link")})) or "nothing yet"
    return (f"{who}{f' ({role})' if role else ''} was on the {facts['persona']} {facts['track']} nurture track "
            f"and received {len(facts['emails_received'])} email(s). Clicked: {clicked}. "
            f"Handed over because: {facts['trigger']}.")


async def summarise(detail: dict[str, Any]) -> str:
    """AI summary for sales; a plain one if the AI is unavailable."""
    facts = _facts(detail)
    try:
        completion = await get_gateway().complete_json(
            SUMMARY_SYSTEM.format(data_rule=DATA_RULE), data_block("lead_data", facts), temperature=0.3)
        summary = clean(json.loads(completion.text).get("summary"), 1200)
        if summary:
            return summary
    except Exception as exc:
        log.warning("nurture: AI hand-off summary failed (%s); using a plain one", exc)
    return plain_summary(facts)


def notification(detail: dict[str, Any], summary: str, console_url: str) -> tuple[str, str]:
    facts = _facts(detail)
    who = facts["name"] or facts["email"]
    lines = [
        f"{who} is ready for sales: {facts['trigger']}.",
        "",
        summary,
        "",
        f"Email:    {facts['email']}",
        f"Role:     {', '.join(p for p in (facts['job_title'], facts['company']) if p) or 'unknown'}",
        f"Persona:  {facts['persona']} ({facts['track']} track)",
        f"Emails:   {len(facts['emails_received'])} of 6 received",
    ]
    for c in facts["clicked"]:
        lines.append(f"Clicked:  {c['link']} in email {c['step']}")
    for r in facts["replies"]:
        lines.append(f"Replied ({r['category'] or 'unclassified'}): {r['text'][:300]}")
    lines += ["", "Nurture emails to this lead have stopped."]
    if console_url:
        lines.append(f"Details: {console_url}/#nurture/handoffs")
    return f"Nurture hand-off: {who} ({facts['trigger']})", "\n".join(lines)


async def notify_pending(cfg: dict[str, Any]) -> int:
    """Email sales about new hand-offs (retried each cycle for 3 days)."""
    to = await alerts.recipient(cfg, "sales_email")
    done = 0
    for eid in await nrepo.unnotified():
        try:
            detail = await nrepo.detail(eid) or {}
            summary = await summarise(detail)
            if to:
                subject, body = notification(detail, summary, os.environ.get("APP_PUBLIC_URL", "").rstrip("/"))
                await alerts.send_internal(to, subject, body)
                done += 1
            else:
                summary += " (Sales was not emailed: set a sales email in Settings.)"
            await nrepo.notified(eid, summary)
        except Exception:
            log.exception("nurture: notifying sales about %s failed; retrying next cycle", eid)
    return done
