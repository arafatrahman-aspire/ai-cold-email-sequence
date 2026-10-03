"""Every database operation the workers need, in one place.

Each function here is a thin call to a ``cold_email.*`` Postgres function over
the Supabase REST API (see :mod:`app.db`). The SQL itself — including the
``FOR UPDATE SKIP LOCKED`` claims that let several workers run without
double-sending — lives in supabase/migrations/0001_init.sql.

The database is shared with other projects. This service writes only to the
``cold_email`` schema; the ``public`` lead tables are read, never written.
A "lead id" throughout is ``public.leads.id``, which is also the primary key
of ``cold_email.enrollments``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from app import db, regions
from app.mail.base import Inbox

log = logging.getLogger(__name__)


def _with_timezone(lead: dict[str, Any]) -> dict[str, Any]:
    """Add the lead's resolved ``timezone`` (and where it came from)."""
    lead["timezone"], lead["timezone_source"] = regions.resolve_lead_timezone(lead)
    return lead


# ---------------------------------------------------------------------------
# Enrollment (the only way a shared lead enters cold outreach)
# ---------------------------------------------------------------------------

async def enroll_lead(
    ref: str,
    contactable_statuses: list[str],
    skip_if_in_nurture: bool,
    job_title: str | None = None,
    company: str | None = None,
    source: str = "api",
    timezone: str | None = None,
) -> tuple[str, Optional[str]]:
    """Enroll one shared lead, identified by ``leads.id`` or ``leads.lead_id``.

    Returns ``(outcome, lead_uuid)``. Outcome is ``enrolled`` or the reason it
    was refused, so the caller can report per-lead results.
    """
    result = await db.rows(
        "enroll_lead",
        p_ref=ref,
        p_contactable=contactable_statuses,
        p_skip_nurture=skip_if_in_nurture,
        p_job_title=job_title,
        p_company=company,
        p_source=source,
        p_timezone=timezone,
    )
    row = result[0]
    return row["outcome"], row["lead_id"]


async def auto_enroll(
    sources: list[str],
    contactable_statuses: list[str],
    skip_if_in_nurture: bool,
    limit: int,
) -> int:
    """Enroll up to ``limit`` contactable leads from the given sources."""
    if not sources:
        return 0
    return int(await db.rpc(
        "auto_enroll",
        p_sources=sources,
        p_contactable=contactable_statuses,
        p_skip_nurture=skip_if_in_nurture,
        p_limit=limit,
    ))


async def list_enrollments(
    statuses: list[str] | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Enrollments, most recently updated first (needs 0003_review.sql)."""
    return await db.rows("list_enrollments", p_statuses=statuses, p_limit=limit)


async def retry_enrollment(
    lead_id: str,
    job_title: str | None = None,
    company: str | None = None,
    timezone: str | None = None,
) -> bool:
    """Send a manual_review / failed / stopped enrollment back to intake."""
    return bool(await db.rpc(
        "retry_enrollment",
        p_lead_id=lead_id,
        p_job_title=job_title,
        p_company=company,
        p_timezone=timezone,
    ))


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------

async def claim_leads_for_intake(limit: int) -> list[dict[str, Any]]:
    """Atomically move up to ``limit`` ready enrollments into 'processing'.

    Returns the lead details for each claimed enrollment.
    """
    return [_with_timezone(r) for r in await db.rows("claim_leads_for_intake", p_limit=limit)]


async def mark_lead(lead_id: str, status: str, error: str | None = None) -> None:
    await db.rpc("mark_lead", p_lead_id=lead_id, p_status=status, p_error=error)


async def set_lead_persona(lead_id: str, persona: str) -> None:
    await db.rpc("set_lead_persona", p_lead_id=lead_id, p_persona=persona)


# ---------------------------------------------------------------------------
# Sequence persistence
# ---------------------------------------------------------------------------

async def store_sequence(
    lead_id: str,
    persona: str,
    routing_mode: str,
    provider: str | None,
    model: str | None,
    emails: list[dict[str, Any]],
    due_dates: list[datetime],
) -> str:
    """Write a sequence and its steps, then flip the lead to 'sequence_ready'.

    One server-side transaction: a lead is never left claiming to have a
    sequence that was only half-written.
    """
    steps = [
        {
            "step_number": e["step_number"],
            "subject": e["subject"],
            "body": e["body"],
            "due_at": due,
        }
        for e, due in zip(emails, due_dates)
    ]
    return str(await db.rpc(
        "store_sequence",
        p_lead_id=lead_id,
        p_persona=persona,
        p_routing_mode=routing_mode,
        p_provider=provider,
        p_model=model,
        p_steps=steps,
    ))


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

async def claim_due_steps(limit: int) -> list[dict[str, Any]]:
    """Claim steps that are due, skipping leads that are no longer contactable.

    The suppression check happens here *and* again immediately before the send,
    per OUT-01's requirement that every send verify suppression independently.
    """
    return await db.rows("claim_due_steps", p_limit=limit)


async def claim_next_emails(
    lead_id: str | None, limit: int, min_gap_hours: int = 24
) -> list[dict[str, Any]]:
    """Claim each sendable lead's next unsent email regardless of due time,
    for "send now"; leads emailed within ``min_gap_hours`` are skipped
    (needs 0004_send_now.sql)."""
    return await db.rows(
        "claim_next_emails", p_lead_id=lead_id, p_limit=limit, p_min_gap_hours=min_gap_hours
    )


async def list_inbox_events(
    types: list[str] | None = None, limit: int = 50, categories: list[str] | None = None
) -> list[dict[str, Any]]:
    """Incoming replies / opt-outs / bounces with their lead and triage
    result, newest first (needs 0006_triage.sql)."""
    return await db.rows(
        "list_inbox_events", p_types=types, p_limit=limit, p_categories=categories
    )


async def upcoming_emails(limit: int = 10) -> list[dict[str, Any]]:
    """The next scheduled emails, soonest first (needs 0004_send_now.sql)."""
    return await db.rows("upcoming_emails", p_limit=limit)


async def lead_for_step(lead_id: str) -> Optional[dict[str, Any]]:
    """Current lead details, or None if the lead was never enrolled.

    ``lead_exists`` is false when the lead has since been deleted from the
    shared ``public.leads`` table.
    """
    result = await db.rows("lead_for_step", p_lead_id=lead_id)
    return _with_timezone(result[0]) if result else None


async def thread_anchor(lead_id: str) -> Optional[str]:
    """Message-ID of the first email sent to this lead, to thread follow-ups."""
    return await db.rpc("thread_anchor", p_lead_id=lead_id)


async def is_suppressed(email: str) -> bool:
    return bool(await db.rpc("is_suppressed", p_email=email))


async def active_inboxes() -> list[Inbox]:
    return [
        Inbox(
            id=str(r["id"]),
            email=r["email"],
            display_name=r["display_name"],
            provider=r["provider"],
            credential_ref=r["credential_ref"],
            daily_cap=r["daily_cap"],
            poll_cursor=r["poll_cursor"],
            config=r["config"] or {},
        )
        for r in await db.rows("active_inboxes")
    ]


async def reserve_inbox_slot(email_id: str, inbox_id: str, day: date, cap: int) -> bool:
    """Reserve today's slot on ``inbox_id`` for a claimed email, within ``cap``.

    A failed, deferred or cancelled send gives the slot back automatically.
    """
    return bool(await db.rpc(
        "reserve_inbox_slot",
        p_email_id=email_id, p_inbox_id=inbox_id, p_day=day, p_cap=cap,
    ))


async def record_send(
    email_id: str,
    to_email: str,
    message_id: str | None,
    thread_id: str | None,
) -> None:
    """Mark the email sent and advance the enrollment, in one transaction."""
    await db.rpc(
        "record_send",
        p_email_id=email_id,
        p_to_email=to_email,
        p_message_id=message_id,
        p_thread_id=thread_id,
    )


async def fail_step(step_id: str, error: str, permanent: bool, max_attempts: int) -> None:
    """Return a step to 'pending' for another try, or bury it as failed."""
    await db.rpc(
        "fail_step",
        p_step_id=step_id,
        p_error=error,
        p_permanent=permanent,
        p_max_attempts=max_attempts,
    )


async def defer_step(step_id: str, due_at: datetime) -> None:
    """Push a claimed step back to pending with a later due time (cap hit)."""
    await db.rpc("defer_step", p_step_id=step_id, p_due_at=due_at)


async def reclaim_stuck_steps(stale_minutes: int = 30) -> int:
    """Recover steps left in 'sending' by a crashed worker."""
    return int(await db.rpc("reclaim_stuck_steps", p_stale_minutes=stale_minutes))


# ---------------------------------------------------------------------------
# Inbound handling
# ---------------------------------------------------------------------------

async def update_inbox_cursor(inbox_id: str, cursor: int) -> None:
    await db.rpc("update_inbox_cursor", p_inbox_id=inbox_id, p_cursor=cursor)


async def match_lead_by_message_ids(message_ids: list[str]) -> Optional[str]:
    """Find the lead a reply belongs to via In-Reply-To / References."""
    if not message_ids:
        return None
    return await db.rpc("match_lead_by_message_ids", p_message_ids=message_ids)


async def match_lead_by_email(address: str) -> Optional[str]:
    """Find an enrolled lead by address. Only leads we have enrolled match:
    mail from anyone else in the shared leads table is not ours to act on."""
    if not address:
        return None
    return await db.rpc("match_lead_by_email", p_address=address)


async def record_inbox_event(
    inbox_id: str,
    lead_id: str | None,
    event_type: str,
    from_email: str,
    to_email: str,
    subject: str,
    message_id: str | None,
    in_reply_to: str | None,
    snippet: str,
    received_at: datetime,
) -> bool:
    """Insert an event. Returns False if it was already recorded."""
    return bool(await db.rpc(
        "record_inbox_event",
        p_inbox_id=inbox_id,
        p_lead_id=lead_id,
        p_event_type=event_type,
        p_from_email=from_email,
        p_to_email=to_email,
        p_subject=subject,
        p_message_id=message_id,
        p_in_reply_to=in_reply_to,
        p_snippet=snippet,
        p_received_at=received_at,
    ))


async def cancel_sequence(lead_id: str, lead_status: str, reason: str) -> int:
    """Stop everything outstanding for a lead. Returns steps cancelled."""
    return int(await db.rpc(
        "cancel_sequence",
        p_lead_id=lead_id,
        p_lead_status=lead_status,
        p_reason=reason,
    ))


async def suppress(email: str, reason: str) -> None:
    await db.rpc("suppress", p_email=email, p_reason=reason)


# ---------------------------------------------------------------------------
# Settings and reporting
# ---------------------------------------------------------------------------

async def get_settings() -> list[dict[str, Any]]:
    return await db.rows("get_settings")


async def set_setting(key: str, value: Any, description: str | None = None) -> None:
    await db.rpc("set_setting", p_key=key, p_value=value, p_description=description)


async def stats() -> dict[str, Any]:
    return await db.rpc("stats")


async def ping() -> bool:
    return await db.rpc("ping") == "ok"


# ---------------------------------------------------------------------------
# Reply triage (needs 0006_triage.sql)
# ---------------------------------------------------------------------------

async def claim_untriaged(limit: int = 10) -> list[dict[str, Any]]:
    return await db.rows("claim_untriaged", p_limit=limit)


async def triage_context(lead_id: str) -> dict[str, Any]:
    return await db.rpc("triage_context", p_lead_id=lead_id) or {}


async def save_triage(
    event_id: str, category: str, confidence: float, summary: str,
    extracted: dict[str, Any], status: str, error: str | None = None,
) -> None:
    await db.rpc(
        "save_triage", p_event_id=event_id, p_category=category, p_confidence=confidence,
        p_summary=summary, p_extracted=extracted, p_status=status, p_error=error,
    )


async def retriage_event(event_id: str, category: str | None = None) -> bool:
    return bool(await db.rpc("retriage_event", p_event_id=event_id, p_category=category))


async def snooze_lead(lead_id: str, until: datetime, reason: str) -> None:
    await db.rpc("snooze_lead", p_lead_id=lead_id, p_until=until, p_reason=reason)


async def wake_snoozed(limit: int = 50) -> int:
    return int(await db.rpc("wake_snoozed", p_limit=limit) or 0)


async def pause_sequence_until(lead_id: str, until: datetime) -> int:
    return int(await db.rpc("pause_sequence_until", p_lead_id=lead_id, p_until=until) or 0)


async def add_referral(
    from_lead_id: str, email: str, first_name: str | None, last_name: str | None,
    job_title: str | None, company: str | None, timezone: str | None, referred_by_name: str | None,
) -> tuple[str, Optional[str]]:
    [row] = await db.rows(
        "add_referral", p_from_lead_id=from_lead_id, p_email=email,
        p_first_name=first_name, p_last_name=last_name, p_job_title=job_title,
        p_company=company, p_timezone=timezone, p_referred_by_name=referred_by_name,
    )
    return row["outcome"], row["lead_id"]


async def referral_names(lead_ids: list[str]) -> dict[str, str]:
    """lead_id -> name of whoever referred them, for referred leads only."""
    if not lead_ids:
        return {}
    rows = await db.rows("referral_names", p_ids=lead_ids)
    return {str(r["lead_id"]): r["referred_by_name"] for r in rows}


async def mark_dnc(lead_id: str) -> bool:
    """Sets public.leads.status = 'DNC' (the only write to a shared table)."""
    return bool(await db.rpc("mark_dnc", p_lead_id=lead_id))


async def record_meeting(
    lead_id: str, event_id: str | None, provider: str, external_id: str,
    start_at: datetime, end_at: datetime | None, attendee_email: str, meeting_url: str | None,
) -> str:
    return str(await db.rpc(
        "record_meeting", p_lead_id=lead_id, p_event_id=event_id, p_provider=provider,
        p_external_id=external_id, p_start_at=start_at, p_end_at=end_at,
        p_attendee_email=attendee_email, p_meeting_url=meeting_url,
    ))


async def create_draft(
    event_id: str, lead_id: str, inbox_id: str, kind: str, to_email: str,
    subject: str, body: str, offered_slots: list[dict], in_reply_to: str | None,
    references: list[str], auto_send_at: datetime | None,
) -> str:
    return str(await db.rpc(
        "create_draft", p_event_id=event_id, p_lead_id=lead_id, p_inbox_id=inbox_id,
        p_kind=kind, p_to_email=to_email, p_subject=subject, p_body=body,
        p_offered_slots=offered_slots, p_in_reply_to=in_reply_to,
        p_references=references, p_auto_send_at=auto_send_at,
    ))


async def cancel_lead_drafts(lead_id: str, reason: str) -> int:
    return int(await db.rpc("cancel_lead_drafts", p_lead_id=lead_id, p_reason=reason) or 0)


async def list_drafts(statuses: list[str] | None, limit: int = 50) -> list[dict[str, Any]]:
    return await db.rows("list_drafts", p_statuses=statuses, p_limit=limit)


async def update_draft(draft_id: str, subject: str, body: str) -> bool:
    return bool(await db.rpc("update_draft", p_id=draft_id, p_subject=subject, p_body=body))


async def decide_draft(draft_id: str, decision: str, user: str) -> bool:
    return bool(await db.rpc("decide_draft", p_id=draft_id, p_decision=decision, p_user=user))


async def claim_drafts_to_send(limit: int = 10) -> list[dict[str, Any]]:
    return await db.rows("claim_drafts_to_send", p_limit=limit)


async def finish_draft(
    draft_id: str, status: str, message_id: str | None = None, error: str | None = None
) -> None:
    await db.rpc("finish_draft", p_id=draft_id, p_status=status, p_message_id=message_id, p_error=error)


async def newer_reply_exists(lead_id: str, since: datetime | str) -> bool:
    return bool(await db.rpc("newer_reply_exists", p_lead_id=lead_id, p_since=since))


async def list_meetings(limit: int = 100) -> list[dict[str, Any]]:
    return await db.rows("list_meetings", p_limit=limit)


async def list_snoozed(limit: int = 100) -> list[dict[str, Any]]:
    return await db.rows("list_snoozed", p_limit=limit)


async def triage_stats() -> dict[str, Any]:
    return await db.rpc("triage_stats") or {}
