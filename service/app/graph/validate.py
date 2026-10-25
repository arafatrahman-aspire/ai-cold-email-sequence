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

# Email 1 opens with a greeting on its own line ("Hi Dana,"), then a blank line.
_GREETING = re.compile(r"^(hi|hello|hey|dear|good (morning|afternoon))\b[^\n]{0,40}\n\s*\n", re.IGNORECASE)
MAX_PARAGRAPH_WORDS = 70


def layout_problem(n: int, body: str) -> str | None:
    """Why the body does not read like a real email, or None. One wall of
    text (the model's habit) looks careless and unprofessional."""
    if n == 1:
        if not _GREETING.match(body):
            return f'email {n}: start with a greeting alone on the first line (e.g. "Hi Dana,") and a blank line after it'
        body = _GREETING.sub("", body, count=1)
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    if len(paras) < 2 or max(len(p.split()) for p in paras) > MAX_PARAGRAPH_WORDS:
        return (f"email {n}: write the body as 2-3 short paragraphs separated by a blank line, "
                f"each under {MAX_PARAGRAPH_WORDS} words, not one block of text")
    return None


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

    # A campaign's banned claims (playbook) are checked like spam words.
    banned = [str(b).strip().lower() for b in
              (((state.get("lead") or {}).get("playbook") or {}).get("banned_claims") or [])
              if str(b).strip()]

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

        elif (problem := layout_problem(int(n or 0), body)):
            errors.append(problem)

        said = [b for b in banned if b in f"{subject}\n{body}".lower()]
        if said:
            errors.append(f"email {n}: uses a claim the campaign bans: {', '.join(said)}")

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
