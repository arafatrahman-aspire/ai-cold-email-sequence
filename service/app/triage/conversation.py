"""The earlier messages of a thread, prepared for the AI.

Replies usually carry the whole previous email underneath ("On Mon, Alex
wrote: > ..."). That quoted part is cut, both to save tokens and so the model
does not read the same email twice and mistake our words for theirs.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Optional

from app.scheduling import resolve_timezone

# Where quoted history starts in common mail clients.
_QUOTE_MARKERS = [
    re.compile(r"^\s*On .{0,200}wrote:\s*$", re.IGNORECASE | re.MULTILINE),             # Gmail, Apple Mail
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE | re.MULTILINE),  # Outlook
    re.compile(r"^\s*From:\s.+\n\s*(Sent|Date):\s", re.IGNORECASE | re.MULTILINE),       # Outlook headers
    re.compile(r"^\s*_{10,}\s*$", re.MULTILINE),                                         # Outlook rule
    re.compile(r"^\s*Le .{0,200}a écrit\s*:\s*$", re.IGNORECASE | re.MULTILINE),         # French Gmail
    re.compile(r"^\s*Am .{0,200}schrieb .{0,100}:\s*$", re.IGNORECASE | re.MULTILINE),   # German Gmail
]

PER_MESSAGE_CHARS = 900


def strip_quoted(text: str) -> str:
    """The new part of an email, without the quoted thread below it."""
    if not text:
        return ""
    cut = len(text)
    for marker in _QUOTE_MARKERS:
        m = marker.search(text)
        if m and m.start() < cut:
            cut = m.start()
    kept = [line for line in text[:cut].splitlines() if not line.lstrip().startswith(">")]
    return "\n".join(kept).strip()


def _when(at: Optional[str | datetime], tz_name: Optional[str]) -> str:
    if not at:
        return ""
    dt = at if isinstance(at, datetime) else datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    local = dt.astimezone(resolve_timezone(tz_name))
    return f"{local:%a} {local.day} {local:%b %Y}, {local:%H:%M}"


def format_history(
    messages: Iterable[dict],
    *,
    lead_name: Optional[str] = None,
    lead_timezone: Optional[str] = None,
) -> str:
    """Oldest first, one block per message, marked us -> them / them -> us."""
    them = (lead_name or "").strip() or "Prospect"
    blocks = []
    for i, m in enumerate(messages, start=1):
        outbound = m.get("direction") == "out"
        who = f"Us -> {them}" if outbound else f"{them} -> us"
        body = (m.get("body") or "").strip()
        if not outbound:
            body = strip_quoted(body)
        if len(body) > PER_MESSAGE_CHARS:
            body = body[:PER_MESSAGE_CHARS].rstrip() + " ..."
        when = _when(m.get("at"), lead_timezone)
        blocks.append(
            f"[{i}] {who}{', ' + when if when else ''}\n"
            f"Subject: {m.get('subject') or '(no subject)'}\n{body or '(empty)'}"
        )
    return "\n\n".join(blocks)
