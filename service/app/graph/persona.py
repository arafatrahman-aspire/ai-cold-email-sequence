"""Persona routing: keyword match on job title, LLM fallback."""

from __future__ import annotations

import json
import logging
import re

from app.graph.prompts import CLASSIFIER_SYSTEM
from app.graph.state import SequenceState
from app.llm.factory import get_gateway

log = logging.getLogger(__name__)

# Ordered most-specific first: a "Security Awareness Training Manager" should
# route to ciso on "security", not to hr on "training".
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    ("ciso", [
        "ciso", "chief information security", "chief security",
        "information security", "infosec", "cyber security", "cybersecurity",
        "security", "risk", "compliance", "audit", "governance", "grc",
        "privacy", "data protection", "dpo",
    ]),
    ("it", [
        "sysadmin", "system administrator", "systems administrator",
        "infrastructure", "network", "it manager", "it director",
        "it operations", "it admin", "head of it", "vp of it", "cio",
        "helpdesk", "help desk", "service desk", "endpoint", "devops",
        "technology", "technical operations", " it ",
    ]),
    ("hr", [
        "chro", "human resources", "people operations", "people ops",
        "head of people", "talent", "recruit", "learning and development",
        "l&d", "training", "onboarding", "culture", "employee experience",
        "hr ", " hr", "workforce",
    ]),
]

_GENERIC_EXEC = re.compile(
    r"\b(ceo|coo|cfo|founder|co-founder|owner|managing director|president|"
    r"general manager|partner)\b",
    re.IGNORECASE,
)


def keyword_persona(job_title: str | None) -> str | None:
    """Return a persona if the title matches a keyword rule, else None."""
    if not job_title:
        return None
    # Pad so rules containing a leading/trailing space (" hr") can match at the
    # string boundaries too.
    title = f" {job_title.lower().strip()} "

    for persona, keywords in KEYWORD_RULES:
        for kw in keywords:
            if kw in title:
                return persona
    return None


async def classify_with_llm(job_title: str | None) -> tuple[str, float]:
    """Ask the LLM to place an unmatched title into one of the three personas."""
    title = (job_title or "").strip()
    if not title:
        # Nothing at all to go on. Risk & compliance is the safest default: it
        # is the angle least likely to read as absurd to an unknown recipient.
        return "ciso", 0.0

    gateway = get_gateway()
    completion = await gateway.complete_json(
        CLASSIFIER_SYSTEM,
        f"Job title: {title}",
        temperature=0.0,
    )
    try:
        data = json.loads(completion.text)
        persona = str(data["persona"]).lower().strip()
        confidence = float(data.get("confidence", 0.5))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        log.warning("classifier returned unparseable output (%s); defaulting", exc)
        return "ciso", 0.0

    if persona not in {"ciso", "it", "hr"}:
        log.warning("classifier returned unknown persona %r; defaulting", persona)
        return "ciso", 0.0
    return persona, confidence


async def persona_node(state: SequenceState) -> SequenceState:
    lead = state.get("lead", {})
    title = lead.get("job_title")

    persona = keyword_persona(title)
    if persona is not None:
        log.info("lead %s routed to %s by keyword", lead.get("id"), persona)
        return {**state, "persona": persona, "routing_mode": "keyword"}

    if title and _GENERIC_EXEC.search(title):
        log.info("lead %s is a generic exec title; routing to ciso", lead.get("id"))
        return {**state, "persona": "ciso", "routing_mode": "keyword"}

    persona, confidence = await classify_with_llm(title)
    log.info(
        "lead %s routed to %s by LLM (confidence %.2f)",
        lead.get("id"), persona, confidence,
    )
    return {**state, "persona": persona, "routing_mode": "llm"}
