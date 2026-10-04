"""Reply classification: which of the six kinds of reply this is, plus the
details needed to act on it (referral contact, return date, chosen slot).

One LLM call at temperature 0. The output is validated field by field; any
malformed or missing part degrades to "unknown" rather than guessing, and the
caller treats low confidence as "a person should look".
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from app.llm.factory import get_gateway

log = logging.getLogger(__name__)

CATEGORIES = (
    "interested", "not_now", "wrong_person", "objection", "out_of_office", "unsubscribe",
)
ALL_CATEGORIES = CATEGORIES + ("other",)

SYSTEM = """\
You triage replies to B2B cold emails sent by a security awareness training
company. Read the prospect's reply and classify it into exactly one category:

- "interested": they want to talk, see a demo, get pricing or details, or they
  accept or propose a meeting time. A question about the product counts.
- "not_now": the timing is wrong but the door is open ("next quarter", "after
  our audit", "check back in a few months", "busy until January").
- "wrong_person": they are not the right contact, or point to someone else.
- "objection": they push back with a reason (already have a vendor, no budget,
  no need, too expensive, doubts about value), or a flat "not interested" with
  no request to stop emailing.
- "out_of_office": an automatic away / vacation / leave message.
- "unsubscribe": they ask to stop, be removed, not be contacted, or report spam.
- "other": none of the above, or impossible to tell.

Precedence when several apply: unsubscribe > out_of_office > wrong_person >
not_now > objection > interested. ("Not interested, remove me" is unsubscribe.
"I'm not the right person, try Jane" is wrong_person.)

Also extract, ONLY when the reply states it explicitly (never guess or invent):
- referral: the person they point to: {"name", "email", "job_title"}; any
  field not given is null. Never construct an email address.
- return_date: for out_of_office, the date they are back, as YYYY-MM-DD.
- chosen_slot: if they accept one of the numbered time options we offered,
  its number (1, 2, ...). null otherwise.
- proposed_time: if they propose a specific other date and time, as ISO 8601
  with UTC offset, resolved using the lead's timezone and today's date given
  below. null if no specific time.
- objection: a few words naming their objection, for objections.

Return ONLY this JSON object:
{"category": "...", "confidence": 0.0-1.0, "summary": "one short sentence",
 "referral": null | {"name": ..., "email": ..., "job_title": ...},
 "return_date": null | "YYYY-MM-DD", "chosen_slot": null | 1,
 "proposed_time": null | "...", "objection": null | "..."}
"""


@dataclass
class TriageResult:
    category: str
    confidence: float
    summary: str
    extracted: dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None


def build_prompt(
    subject: str,
    body: str,
    *,
    lead_timezone: str,
    today: date,
    last_sent: Optional[dict] = None,
    offered: Optional[list[str]] = None,
    forced_category: Optional[str] = None,
    history: Optional[str] = None,
) -> str:
    parts = [f"Lead's timezone: {lead_timezone}. Today's date there: {today.isoformat()} ({today:%A})."]
    if history:
        parts.append(
            "The conversation so far, oldest first (for context; classify only the NEW reply below):\n"
            + history
        )
    elif last_sent:
        parts.append(
            "Our last email to them:\nSubject: "
            f"{last_sent.get('subject') or ''}\n{(last_sent.get('body') or '')[:1200]}"
        )
    if offered:
        parts.append("Time options we offered them:\n" + "\n".join(
            f"{i}. {text}" for i, text in enumerate(offered, start=1)))
    if forced_category:
        parts.append(
            f'A person has already decided the category is "{forced_category}". '
            "Use exactly that category and extract the details."
        )
    parts.append(f"Their NEW reply:\nSubject: {subject or ''}\n{(body or '')[:3000]}")
    return "\n\n".join(parts)


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _text(v: Any, limit: int = 200) -> Optional[str]:
    if isinstance(v, str) and v.strip() and v.strip().lower() not in ("null", "none", "n/a"):
        return v.strip()[:limit]
    return None


def parse_result(text: str, body: str = "") -> TriageResult:
    """Validate the model's JSON. Unusable output becomes category 'other'."""
    try:
        data = json.loads(_FENCE.sub("", text).strip())
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("unparseable triage output: %s", exc)
        return TriageResult("other", 0.0, "Could not read the classifier's answer.")

    category = str(data.get("category") or "").strip().lower()
    if category not in ALL_CATEGORIES:
        category = "other"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0

    extracted: dict[str, Any] = {}
    ref = data.get("referral")
    if isinstance(ref, dict):
        email = _text(ref.get("email"))
        # Only keep an email that literally appears in the reply: the model
        # must not invent addresses.
        if email and (not _EMAIL.match(email) or email.lower() not in body.lower()):
            email = None
        referral = {"name": _text(ref.get("name")), "email": email, "job_title": _text(ref.get("job_title"))}
        if any(referral.values()):
            extracted["referral"] = referral

    rd = _text(data.get("return_date"), 10)
    if rd:
        try:
            extracted["return_date"] = date.fromisoformat(rd).isoformat()
        except ValueError:
            pass

    cs = data.get("chosen_slot")
    if isinstance(cs, (int, float)) and not isinstance(cs, bool) and int(cs) == cs and cs >= 1:
        extracted["chosen_slot"] = int(cs)

    pt = _text(data.get("proposed_time"), 40)
    if pt:
        try:
            when = datetime.fromisoformat(pt.replace("Z", "+00:00"))
            if when.tzinfo is not None:
                extracted["proposed_time"] = when.isoformat()
        except ValueError:
            pass

    obj = _text(data.get("objection"))
    if obj:
        extracted["objection"] = obj

    return TriageResult(category, confidence, _text(data.get("summary"), 300) or "", extracted)


async def classify_reply(
    subject: str,
    body: str,
    *,
    lead_timezone: str,
    today: date,
    last_sent: Optional[dict] = None,
    offered: Optional[list[str]] = None,
    forced_category: Optional[str] = None,
    history: Optional[str] = None,
) -> TriageResult:
    prompt = build_prompt(
        subject, body, lead_timezone=lead_timezone, today=today,
        last_sent=last_sent, offered=offered, forced_category=forced_category,
        history=history,
    )
    try:
        completion = await get_gateway().complete_json(SYSTEM, prompt, temperature=0.0)
    except Exception:
        if not forced_category:
            raise
        # A person already decided the category; act on it even with the AI
        # down. Only the extra details (referral email, dates) are missing.
        return TriageResult(forced_category, 1.0, "Category set by a person.", {}, model=None)
    result = parse_result(completion.text, body)
    result.model = f"{completion.provider}:{completion.model}"
    if forced_category:
        result.category, result.confidence = forced_category, 1.0
    return result
