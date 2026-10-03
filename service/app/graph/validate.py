"""Validation node: structural checks, one retry, then manual review."""

from __future__ import annotations

import logging
import re

from app.graph.state import SequenceState
from app.settings_store import get as get_setting

log = logging.getLogger(__name__)

MAX_DRAFT_ATTEMPTS = 2  # initial attempt + one retry, per OUT-01

# Placeholders a model leaves behind when it has nothing real to say.
# Any short bracketed or braced phrase counts: in a cold email body these are
# effectively always an unfilled slot ("[First Name]", "{{company}}"), and the
# cost of a false positive is one retry, not a dropped lead.
_PLACEHOLDER = re.compile(
    r"\[[A-Za-z][A-Za-z0-9 ._/'-]{0,40}\]"
    r"|\{\{[^}]{0,60}\}\}"
    r"|<[A-Za-z][A-Za-z0-9 ._-]{0,40}>"
    r"|\bLorem ipsum\b"
    r"|\bTBD\b",
    re.IGNORECASE,
)

_BANNED_SUBJECT_WORDS = {
    "free", "guarantee", "guaranteed", "urgent", "act now", "limited time",
    "!!!", "$$$", "risk-free", "winner",
}


async def validate_node(state: SequenceState) -> SequenceState:
    emails = state.get("emails") or []
    expected = state.get("step_count", 4)
    min_body = int(await get_setting("llm_min_body_chars"))
    max_body = int(await get_setting("llm_max_body_chars"))

    errors: list[str] = []

    if state.get("error"):
        errors.append(str(state["error"]))

    if len(emails) != expected:
        errors.append(f"expected {expected} emails, got {len(emails)}")

    seen_subjects: set[str] = set()
    for email in emails:
        n = email.get("step_number")
        subject = (email.get("subject") or "").strip()
        body = (email.get("body") or "").strip()

        if not subject:
            errors.append(f"email {n}: subject is empty")
        elif len(subject) > 120:
            errors.append(f"email {n}: subject is too long ({len(subject)} chars)")
        else:
            lowered = subject.lower()
            hits = [w for w in _BANNED_SUBJECT_WORDS if w in lowered]
            if hits:
                errors.append(
                    f"email {n}: subject contains spam-trigger wording: {', '.join(hits)}"
                )
            key = lowered
            if key in seen_subjects:
                errors.append(f"email {n}: subject duplicates an earlier email")
            seen_subjects.add(key)

        if not body:
            errors.append(f"email {n}: body is empty")
        elif len(body) < min_body:
            errors.append(
                f"email {n}: body is too short ({len(body)} chars, minimum {min_body})"
            )
        elif len(body) > max_body:
            errors.append(
                f"email {n}: body is too long ({len(body)} chars, maximum {max_body})"
            )

        placeholder = _PLACEHOLDER.search(f"{subject}\n{body}")
        if placeholder:
            errors.append(
                f"email {n}: contains an unfilled placeholder {placeholder.group(0)!r}"
            )

    if not errors:
        return {**state, "validation_errors": [], "status": "ok", "error": None}

    attempts = state.get("draft_attempts", 0)
    log.warning(
        "validation failed for lead %s (attempt %d): %s",
        state.get("lead", {}).get("id"), attempts, "; ".join(errors),
    )

    if attempts >= MAX_DRAFT_ATTEMPTS:
        return {
            **state,
            "validation_errors": errors,
            "status": "manual_review",
            "error": "; ".join(errors)[:1000],
        }

    # Leave status unset so the graph routes back to the draft node.
    return {**state, "validation_errors": errors}


def route_after_validation(state: SequenceState) -> str:
    status = state.get("status")
    if status in ("ok", "manual_review"):
        return "end"
    return "retry"
