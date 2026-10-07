"""Deterministic checks on a nurture email, before the AI judge sees it.

A draft fails if any check fails; each failure is a short sentence that is
logged with the message and fed back to the model for its second attempt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

DEMO, PRICING, RESOURCE = "{{CTA_DEMO}}", "{{CTA_PRICING}}", "{{RESOURCE_LINK}}"
PLACEHOLDERS = (DEMO, PRICING, RESOURCE)
CTA_TYPES = ("demo", "pricing", "resource", "reply")

_ANY_PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}")
_BRACES = re.compile(r"[{}]|\[[A-Z][A-Za-z ]{1,30}\]")
_URL = re.compile(
    r"https?://|www\.|\b[\w.+-]+@[\w-]+\.[\w.]+|"
    r"\b[a-z0-9-]+\.(?:com|net|org|io|co|ai|gov|edu|info|biz|app|dev|uk|us|de|in|bd|au|ca)\b",
    re.IGNORECASE,
)
_HTML = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)
# Prices and offers, whatever the banned-phrase list says.
_PRICE = re.compile(
    r"[$€£¥]\s?\d|\b\d+(?:[.,]\d+)?\s?(?:usd|eur|gbp|dollars|euros|pounds)\b|\b\d+\s?%\s?off\b|"
    r"\bper (?:user|seat|employee|month)\b",
    re.IGNORECASE,
)


@dataclass
class Draft:
    subject: str
    preheader: str
    body: str
    resource_id: Optional[str]
    cta_type: str


def words(body: str) -> int:
    return len(_ANY_PLACEHOLDER.sub(" ", body).split())


def check(draft: Draft, *, allowed_resources: set[str], max_words: int,
          banned_phrases: list[str], min_words: int = 25) -> list[str]:
    """Every reason the draft cannot be sent (empty list = it passes)."""
    problems: list[str] = []
    subject, pre, body = draft.subject.strip(), draft.preheader.strip(), draft.body.strip()

    if not subject or len(subject) > 90:
        problems.append("subject must be 1-90 characters")
    if len(pre) > 140:
        problems.append("preheader must be at most 140 characters")
    n = words(body)
    if n > max_words:
        problems.append(f"body has {n} words; the limit is {max_words}")
    if n < min_words:
        problems.append(f"body has only {n} words")

    for text, where in ((subject, "subject"), (pre, "preheader"), (body, "body")):
        if _URL.search(text):
            problems.append(f"{where} contains a raw URL, domain or email address")
        if _HTML.search(text):
            problems.append(f"{where} contains HTML")
        if where != "body" and "{" in text:
            problems.append(f"{where} must not contain placeholders")

    unknown = sorted({p for p in _ANY_PLACEHOLDER.findall(body) if p not in PLACEHOLDERS})
    if unknown:
        problems.append("unknown placeholders: " + ", ".join(unknown))
    if _BRACES.search(_ANY_PLACEHOLDER.sub(" ", body)):
        problems.append("body contains stray braces or a [Placeholder]")
    for required in (DEMO, PRICING):
        count = body.count(required)
        if count == 0:
            problems.append(f"body must include {required}")
        elif count > 2:
            problems.append(f"{required} appears {count} times")

    if draft.resource_id:
        if draft.resource_id not in allowed_resources:
            problems.append("resource_id is not one of the allowed resources")
        if body.count(RESOURCE) != 1:
            problems.append(f"with a resource, body must include {RESOURCE} exactly once")
    else:
        if RESOURCE in body:
            problems.append(f"{RESOURCE} used without choosing a resource")
        if allowed_resources:
            problems.append("choose one of the allowed resources")
    if draft.cta_type not in CTA_TYPES:
        problems.append(f"cta_type must be one of {', '.join(CTA_TYPES)}")

    lowered = " ".join([subject, pre, body]).lower()
    hits = [p for p in banned_phrases if p and p.lower() in lowered]
    if hits:
        problems.append("banned phrases: " + ", ".join(sorted(set(hits))))
    if _PRICE.search(" ".join([subject, pre, body])):
        problems.append("mentions a price, discount or per-seat cost")
    return problems


def unresolved(text: str) -> list[str]:
    """After filling in the links: anything that still looks like a placeholder."""
    found = _ANY_PLACEHOLDER.findall(text)
    return [f"unresolved placeholder {p}" for p in sorted(set(found))]
