"""Draft node: generate the full email sequence in one LLM call."""

from __future__ import annotations

import json
import logging
import re

from app.config import get_settings
from app.graph.prompts import SYSTEM_PROMPT, build_user_prompt
from app.graph.state import DraftedEmail, SequenceState
from app.llm.factory import get_gateway

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _parse_emails(text: str, step_count: int) -> list[DraftedEmail]:
    """Parse the model's JSON, tolerating a stray markdown fence."""
    cleaned = _FENCE.sub("", text).strip()
    data = json.loads(cleaned)

    raw = data.get("emails") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ValueError("expected an 'emails' array")

    emails: list[DraftedEmail] = []
    for idx, item in enumerate(raw[:step_count], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"email {idx} is not an object")
        emails.append(
            DraftedEmail(
                step_number=int(item.get("step_number") or idx),
                subject=str(item.get("subject") or "").strip(),
                body=str(item.get("body") or "").strip(),
            )
        )
    # Renumber defensively: the model occasionally repeats or skips a number.
    for i, email in enumerate(emails, start=1):
        email["step_number"] = i
    return emails


async def draft_node(state: SequenceState) -> SequenceState:
    settings = get_settings()
    lead = state.get("lead", {})
    persona = state.get("persona") or "ciso"
    step_count = state.get("step_count", 4)
    attempts = state.get("draft_attempts", 0) + 1

    user_prompt = build_user_prompt(persona, dict(lead), step_count)

    # On a retry, tell the model exactly what was wrong the first time.
    errors = state.get("validation_errors") or []
    if errors:
        user_prompt += (
            "\nYour previous attempt was rejected for these reasons. "
            "Fix every one of them:\n- " + "\n- ".join(errors) + "\n"
        )

    try:
        completion = await get_gateway().complete_json(
            SYSTEM_PROMPT, user_prompt, temperature=settings.llm_temperature
        )
    except Exception as exc:  # provider-level failure
        log.exception("draft call failed for lead %s", lead.get("id"))
        return {
            **state,
            "draft_attempts": attempts,
            "emails": [],
            "error": f"llm call failed: {exc}",
        }

    try:
        emails = _parse_emails(completion.text, step_count)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        log.warning("draft output unparseable for lead %s: %s", lead.get("id"), exc)
        return {
            **state,
            "draft_attempts": attempts,
            "emails": [],
            "provider": completion.provider,
            "model": completion.model,
            "error": f"unparseable draft output: {exc}",
        }

    return {
        **state,
        "draft_attempts": attempts,
        "emails": emails,
        "provider": completion.provider,
        "model": completion.model,
        "error": None,
    }
