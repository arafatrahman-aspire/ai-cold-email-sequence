"""What goes out when: the console's nurture calendar, and the plain-words
reasons behind a hand-off.

The calendar has two kinds of entries: emails already written (or sent), and
the ones not written yet, whose dates are worked out from the same cadence the
send job uses. A projected date can still move (a reply, a track switch, the
daily limit), so it is labelled as planned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.nurture import cadence, options

LINKS = {"demo": "the demo link", "pricing": "the pricing link", "resource": "a resource"}
TRIGGER_WORDS = {
    "hot_score": "Their lead score became Hot",
    "pricing_click": "They clicked the pricing link",
    "demo_click": "They clicked the demo link",
    "reply_interested": "They replied with interest",
    "manual": "Handed off by a person",
}
CATEGORY_WORDS = {
    "interested": "interested", "not_now": "not now", "objection": "an objection",
    "wrong_person": "wrong person", "out_of_office": "out of office", "other": "unclear",
}


def _dt(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def calendar(data: dict[str, Any], cfg: dict[str, Any], start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Every nurture email due between ``start`` and ``end``, soonest first."""
    items: list[dict[str, Any]] = []
    for m in data.get("messages") or []:
        items.append({**m, "planned": False})

    hours = options.window(cfg)
    for e in data.get("live") or []:
        tz = cadence.lead_timezone(e.get("timezone"), cfg["default_timezone"])
        test = options.test_minutes(cfg) if e.get("test_mode") else None
        steps = cfg["cadence"].get(e["temperature"]) or []
        anchor = _dt(e["started_at"])
        first = max(int(e.get("step") or 0), int(e.get("written") or 0)) + 1
        for step in range(first, len(steps) + 1):
            at = (_dt(e.get("next_send_at")) if step == int(e.get("step") or 0) + 1 and e.get("next_send_at")
                  else cadence.due_at(anchor, float(steps[step - 1]), tz, hours, test))
            if at is None or not (start <= at < end):
                continue
            items.append({
                "enrollment_id": e["enrollment_id"], "lead_id": e.get("lead_id"), "step": step,
                "status": "planned", "at": at.isoformat(), "subject": None, "persona": e["persona"],
                "temperature": e["temperature"], "email": e.get("email"),
                "first_name": e.get("first_name"), "last_name": e.get("last_name"), "planned": True,
            })
    return sorted(items, key=lambda i: str(i["at"]))


def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def signals(row: dict[str, Any], trigger: Optional[str], resources: list[dict[str, Any]]) -> list[str]:
    """Why this lead is ready for sales, one short sentence per signal."""
    out = [TRIGGER_WORDS.get(trigger or "", trigger or "Handed off")]
    titles = {r.get("id"): r.get("title") for r in resources}
    by_link: dict[str, list[dict]] = {}
    for c in row.get("clicks") or []:
        by_link.setdefault(c.get("link") or "resource", []).append(c)
    for link in ("pricing", "demo"):
        if by_link.get(link):
            steps = sorted({str(c.get("step")) for c in by_link[link] if c.get("step")})
            out.append(f"Clicked {LINKS[link]} {_times(len(by_link[link]))}"
                       + (f" (email {', '.join(steps)})" if steps else ""))
    opened = [titles.get(c.get("resource_id")) or "a resource" for c in by_link.get("resource", [])]
    for title in dict.fromkeys(opened):  # each resource once, in click order
        out.append(f"Opened {title if title == 'a resource' else f'“{title}”'}")
    for r in row.get("replies") or []:
        text = (r.get("summary") or r.get("snippet") or "").strip()
        what = CATEGORY_WORDS.get(r.get("category") or "", "")
        out.append(f"Replied{f' ({what})' if what else ''}" + (f": {text[:160]}" if text else ""))
    if row.get("tier"):
        out.append(f"Lead score now: {row['tier']}")
    # The trigger often repeats a click; keep each sentence once.
    return list(dict.fromkeys(out))
