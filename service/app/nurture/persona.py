"""Nurture persona: CISO (ciso), IT Manager (it) or HR & Compliance (hr).

Keyword rules first (the cold sequence's rules, plus a nurture-only rule that
sends compliance and people roles to HR & Compliance unless the title is about
security or risk), then the LLM, which answers {persona, confidence, reason}.
Below the confidence threshold the lead gets IT Manager and a review flag.
The cold sequence's own routing is not changed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.graph.persona import _GENERIC_EXEC, keyword_persona
from app.llm.factory import get_gateway
from app.nurture.safety import DATA_RULE, clean, data_block

log = logging.getLogger(__name__)

DEFAULT = "it"
PROMPT_VERSION = "nurture-persona-v1"

_HR_COMPLIANCE = re.compile(
    r"\b(compliance|people|hrbp|hr business partner|employee relations|policy|ethics)\b",
    re.IGNORECASE,
)
_SECURITY = re.compile(r"secur|cyber|risk|infosec|ciso|information|privacy|\bit\b", re.IGNORECASE)
_LETTERS = re.compile(r"[a-zA-Z]{2,}")

SYSTEM = f"""\
You place a B2B lead into one of three buyer personas for security awareness
training, based on their job title:
- "ciso": security, risk, privacy and executive leadership
- "it": IT, infrastructure, operations and technical roles
- "hr": HR, people, learning, culture and compliance roles

{DATA_RULE}

If the title is not a real job title, or you cannot tell, say so with a low
confidence. Return ONLY: {{"persona": "ciso"|"it"|"hr", "confidence": 0.0-1.0,
"reason": "a few words"}}
"""


@dataclass(frozen=True)
class PersonaChoice:
    persona: str
    source: str  # keyword | llm | default
    confidence: float
    reason: str
    needs_review: bool = False


def keyword(title: str | None) -> str | None:
    """The keyword rules only (no LLM)."""
    text = clean(title, 120)
    if not text:
        return None
    if _HR_COMPLIANCE.search(text) and not _SECURITY.search(text):
        return "hr"
    persona = keyword_persona(text)
    if persona:
        return persona
    if _GENERIC_EXEC.search(text):
        return "ciso"
    return None


async def assign(title: str | None, min_confidence: float) -> PersonaChoice:
    text = clean(title, 120)
    persona = keyword(text)
    if persona:
        return PersonaChoice(persona, "keyword", 1.0, f"job title '{text}' matched a keyword rule")
    if not _LETTERS.search(text):
        return PersonaChoice(DEFAULT, "default", 0.0, "no usable job title", needs_review=True)

    try:
        completion = await get_gateway().complete_json(
            SYSTEM, data_block("lead_data", {"job_title": text}), temperature=0.0
        )
        data = json.loads(completion.text)
        persona = str(data.get("persona", "")).lower().strip()
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
        reason = clean(data.get("reason"), 200) or "no reason given"
    except Exception as exc:
        log.warning("persona LLM failed for %r (%s); using the default", text, exc)
        return PersonaChoice(DEFAULT, "default", 0.0, f"AI unavailable: {exc}"[:200], needs_review=True)

    if persona not in ("ciso", "it", "hr"):
        return PersonaChoice(DEFAULT, "default", 0.0, f"AI answered {persona!r}", needs_review=True)
    if confidence < min_confidence:
        return PersonaChoice(
            DEFAULT, "default", confidence,
            f"AI suggested {persona} at {confidence:.2f} (below {min_confidence:.2f}): {reason}",
            needs_review=True,
        )
    return PersonaChoice(persona, "llm", confidence, reason)
