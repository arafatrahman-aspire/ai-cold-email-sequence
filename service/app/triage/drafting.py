"""Writing the reply drafts.

The LLM writes the body; the code guarantees the parts that must be exact:
offered times appear word for word, the booking link is present when there is
one, nothing unfilled ("[Name]") slips through, and the text never names a
meeting time or promises a link or invite that the calendar did not give us
(a reply once promised "5 PM Tuesday" that was not free). If the model fails
or its text does not pass, a plain template is used instead, so a reply is
never lost to a provider hiccup.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from app.llm.factory import get_gateway

log = logging.getLogger(__name__)

KINDS = (
    "meeting_offer", "booking_confirmation", "slot_unavailable",
    "objection_reply", "not_now_ack", "referral_ack", "referral_ask",
)

SYSTEM = """\
You write short email replies for a B2B sales rep at a security awareness
training company. The prospect has replied to a cold email; you answer them.

Rules:
- Plain text, 30-110 words. Warm, direct, human. No hype, no exclamation marks.
- Match their tone and length. Never argue.
- Never invent facts: no made-up customers, numbers, features or prices.
- No subject line, no greeting block beyond "Hi <first name>," (or "Hi," if
  the name is unknown), and end with the sign-off name given, on its own line.
- Copy any time options or links you are given exactly as written.
- Never propose, accept or confirm a day or time, and never promise to send a
  meeting link or calendar invite, unless the task below gives you that time
  or says the meeting is booked. Without time options, ask for theirs.
- Read the conversation so far: stay consistent with what we already said,
  do not repeat it, and answer what they actually asked.

Return ONLY: {"body": "..."}
"""

INSTRUCTIONS = {
    "meeting_offer": (
        "They are interested. Thank them briefly, then offer these times as a short "
        "list and ask them to reply with the one that works. {link_line}"
    ),
    "slot_unavailable": (
        "The time they asked for is not available. Say so briefly, offer these times "
        "instead as a short list, and ask which works. {link_line}"
    ),
    "booking_confirmation": (
        "The meeting is booked for {booked}. Confirm that time, say a calendar invite "
        "is on its way{meeting_line}, and that you look forward to it. No pitch."
    ),
    "objection_reply": (
        "They raised an objection: {objection}. If it is a flat 'not interested', close "
        "graciously in one or two sentences with no pitch and no question. Otherwise "
        "acknowledge it, make one short relevant point, and ask one low-pressure question."
    ),
    "not_now_ack": (
        "The timing is wrong for them. Acknowledge it, say you will check back in about "
        "{months} months, and wish them well. No pitch, no question."
    ),
    "referral_ack": (
        "They pointed you to {referral}. Thank them and say you will reach out to them. "
        "Keep it to two or three sentences."
    ),
    "referral_ask": (
        "They are not the right person. Thank them and ask, in one sentence, who handles "
        "security awareness training and how best to reach them."
    ),
}

_PLACEHOLDER = re.compile(r"\[[A-Za-z _]{2,30}\]|\{\{.*?\}\}|<[A-Za-z _]{2,30}>")
# A clock time ("5 PM", "10:30", "9am").
_CLOCK = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm|a\.m\.|p\.m\.)(?![a-z])|\b\d{1,2}:\d{2}\b", re.IGNORECASE)
# "I'll send (over) the link / an invite" without a booking behind it.
_PROMISE = re.compile(
    r"\b(?:i'll|i will|i'm going to|we'll|we will)\s+(?:\w+\s+){0,3}?send\b[^.?!\n]{0,40}\b(?:link|invite|invitation)",
    re.IGNORECASE,
)


def _clock(text: str) -> str:
    return re.sub(r"[\s.]", "", text.lower())
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


@dataclass
class DraftInput:
    kind: str
    first_name: Optional[str]
    sign_off: str
    reply_subject: str
    reply_body: str
    slot_lines: list[str]
    booking_link: Optional[str] = None
    booked: Optional[str] = None
    meeting_url: Optional[str] = None
    objection: Optional[str] = None
    referral: Optional[str] = None
    snooze_days: int = 60
    # The earlier messages of the thread, already formatted (oldest first).
    history: Optional[str] = None


def reply_subject(subject: str) -> str:
    s = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", subject or "", flags=re.IGNORECASE).strip()
    return f"Re: {s}" if s else "Re: our conversation"


def _greeting(first_name: Optional[str]) -> str:
    name = (first_name or "").strip()
    return f"Hi {name}," if name else "Hi,"


def template(d: DraftInput) -> str:
    """Plain fallback text for each kind."""
    slots = "\n".join(f"- {line}" for line in d.slot_lines)
    link = f"\n\nIf none of those suit, you can pick any time here: {d.booking_link}" if d.booking_link else ""
    body = {
        "meeting_offer": (
            f"Thanks for getting back to me. Would one of these work for a short call?\n\n{slots}{link}\n\n"
            "Just reply with the one that suits you." if d.slot_lines else
            f"Thanks for getting back to me. What times work for a short call this week or next?{link}"
        ),
        "slot_unavailable": (
            f"Sorry, that time is no longer free. Would one of these work instead?\n\n{slots}{link}"
            if d.slot_lines else f"Sorry, that time is no longer free. What other times suit you?{link}"
        ),
        "booking_confirmation": (
            f"Great, you're booked for {d.booked}. A calendar invite is on its way"
            + (f", with the meeting link: {d.meeting_url}" if d.meeting_url else "")
            + ". Looking forward to it."
        ),
        "objection_reply": "Thanks for letting me know, I appreciate the honest answer. I won't take more of your time.",
        "not_now_ack": (
            f"Understood, thanks for letting me know. I'll check back in about {max(1, round(d.snooze_days / 30))} "
            "months. All the best until then."
        ),
        "referral_ack": f"Thanks for pointing me to {d.referral or 'them'}. I'll reach out to them directly.",
        "referral_ask": "Thanks for letting me know. Who would be the best person to speak to about security awareness training?",
    }[d.kind]
    return f"{_greeting(d.first_name)}\n\n{body}\n\n{d.sign_off}".strip()


def _ensure(body: str, d: DraftInput) -> Optional[str]:
    """Make the must-have parts exact; None if the text cannot be used."""
    body = body.strip()
    if not body or len(body) > 1800 or _PLACEHOLDER.search(body):
        return None
    if d.kind in ("meeting_offer", "slot_unavailable") and d.slot_lines:
        if not all(line in body for line in d.slot_lines):
            return None
    if d.booking_link and d.kind in ("meeting_offer", "slot_unavailable") and d.booking_link not in body:
        body = body.rstrip() + f"\n\nOr pick any time that suits you here: {d.booking_link}"
    if d.kind == "booking_confirmation" and d.booked and d.booked not in body:
        return None
    if _invents_meeting(body, d):
        log.warning("AI %s draft named a time or promised an invite it was not given", d.kind)
        return None
    return body


def _invents_meeting(body: str, d: DraftInput) -> bool:
    """True if the text names a time we did not give it, or promises a link or
    invite with no booking behind it. Only when saying their time is not free
    may it repeat the time they wrote."""
    rest = body
    for given in (*d.slot_lines, d.booked, d.booking_link, d.meeting_url):
        if given:
            rest = rest.replace(given, " ")
    theirs = ({_clock(m.group()) for m in _CLOCK.finditer(d.reply_body or "")}
              if d.kind == "slot_unavailable" else set())
    if any(_clock(m.group()) not in theirs for m in _CLOCK.finditer(rest)):
        return True
    return d.kind != "booking_confirmation" and bool(_PROMISE.search(rest))


def _prompt(d: DraftInput) -> str:
    link_line = (f"Also mention they can pick any time here: {d.booking_link}" if d.booking_link
                 else "")
    template_text = INSTRUCTIONS[d.kind]
    if d.kind in ("meeting_offer", "slot_unavailable") and not d.slot_lines:
        # No calendar slots to offer: ask them for times instead.
        template_text = (
            "They are interested. Thank them briefly and ask which days and times suit "
            "them for a short call. {link_line}" if d.kind == "meeting_offer" else
            "The time they asked for is not available. Apologise briefly and ask which "
            "other days and times suit them. {link_line}"
        )
    instruction = template_text.format(
        link_line=link_line,
        booked=d.booked or "",
        meeting_line=f" with the meeting link {d.meeting_url}" if d.meeting_url else "",
        objection=d.objection or "not stated",
        months=max(1, round(d.snooze_days / 30)),
        referral=d.referral or "someone else",
    )
    parts = [
        f"Prospect's first name: {d.first_name or 'unknown'}",
        f"Sign off as: {d.sign_off}",
        f"Task: {instruction}",
    ]
    if d.slot_lines:
        parts.append("Time options (copy exactly, one per line):\n" + "\n".join(d.slot_lines))
    if d.history:
        parts.append("The conversation so far, oldest first:\n" + d.history)
    parts.append(f"Their new reply (the one you are answering):\nSubject: {d.reply_subject}\n{d.reply_body[:2000]}")
    return "\n\n".join(parts)


async def write_draft(d: DraftInput) -> tuple[str, str, str]:
    """Returns (subject, body, source) where source is 'ai' or 'template'."""
    subject = reply_subject(d.reply_subject)
    try:
        completion = await get_gateway().complete_json(SYSTEM, _prompt(d), temperature=0.4)
        data = json.loads(_FENCE.sub("", completion.text).strip())
        body = _ensure(str(data.get("body") or ""), d) if isinstance(data, dict) else None
        if body:
            return subject, body, "ai"
        log.warning("AI %s draft failed checks; using the template", d.kind)
    except Exception as exc:  # provider down, bad JSON, ...
        log.warning("AI %s draft failed (%s); using the template", d.kind, exc)
    return subject, template(d), "template"
