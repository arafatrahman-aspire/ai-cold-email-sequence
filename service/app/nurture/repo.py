"""Database calls for Email Nurture (supabase/migrations/0010_nurture.sql).
Thin wrappers, like app.repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app import db


# --- leads and enrollment -----------------------------------------------------

async def lead(lead_id: str) -> Optional[dict[str, Any]]:
    return await db.rpc("nurture_lead", p_lead_id=lead_id)


async def why_not(lead_id: str, cooldown_days: int, blocked: list[str], skip_external: bool) -> Optional[str]:
    return await db.rpc("nurture_why_not", p_lead_id=lead_id, p_cooldown_days=cooldown_days,
                        p_blocked_statuses=blocked, p_skip_external=skip_external)


async def enroll(lead_id: str, persona: str, temperature: str, started_at: datetime, needs_review: bool,
                 note: Optional[str], test_mode: bool, detail: dict[str, Any], cooldown_days: int,
                 blocked: list[str], skip_external: bool) -> dict[str, Any]:
    return await db.rpc(
        "nurture_enroll", p_lead_id=lead_id, p_persona=persona, p_temperature=temperature,
        p_started_at=started_at, p_needs_review=needs_review, p_note=note, p_test_mode=test_mode,
        p_detail=detail, p_cooldown_days=cooldown_days, p_blocked_statuses=blocked,
        p_skip_external=skip_external,
    ) or {}


async def score_changes(since: datetime) -> list[dict[str, Any]]:
    return await db.rows("nurture_score_changes", p_since=since)


async def candidates(cooldown_days: int, blocked: list[str], skip_external: bool,
                     limit: int = 500) -> list[dict[str, Any]]:
    return await db.rows("nurture_candidates", p_cooldown_days=cooldown_days, p_blocked_statuses=blocked,
                         p_skip_external=skip_external, p_limit=limit)


async def live() -> list[dict[str, Any]]:
    return await db.rows("nurture_live")


# --- changing an enrollment ------------------------------------------------------

async def log(enrollment_id: str, kind: str, detail: Optional[dict[str, Any]] = None) -> None:
    await db.rpc("nurture_log", p_enrollment_id=enrollment_id, p_kind=kind, p_detail=detail or {})


async def set_status(enrollment_id: str, status: str, reason: str,
                     next_send_at: Optional[datetime] = None) -> bool:
    """active | paused | held | exited | completed"""
    return bool(await db.rpc("nurture_set_status", p_id=enrollment_id, p_status=status,
                             p_reason=reason, p_next_send_at=next_send_at))


async def handoff(enrollment_id: str, trigger: str) -> bool:
    return bool(await db.rpc("nurture_handoff", p_id=enrollment_id, p_trigger=trigger))


async def update(enrollment_id: str, *, persona: Optional[str] = None, temperature: Optional[str] = None,
                 needs_review: Optional[bool] = None, note: Optional[str] = None,
                 next_send_at: Optional[datetime] = None) -> bool:
    return bool(await db.rpc("nurture_update", p_id=enrollment_id, p_persona=persona,
                             p_temperature=temperature, p_needs_review=needs_review, p_note=note,
                             p_next_send_at=next_send_at))


async def suppress(enrollment_id: str, reason: str) -> bool:
    """unsubscribed (also marks the shared lead DNC) | bounced"""
    return bool(await db.rpc("nurture_suppress", p_id=enrollment_id, p_reason=reason))


# --- writing ----------------------------------------------------------------------

async def claim_writing(horizon: datetime, limit: int) -> list[str]:
    return [str(r["message_id"]) for r in await db.rows("nurture_claim_writing", p_horizon=horizon, p_limit=limit)]


async def message_context(message_id: str) -> Optional[dict[str, Any]]:
    return await db.rpc("nurture_message_context", p_message_id=message_id)


async def save_message(message_id: str, status: str, subject: str, preheader: str, body: str,
                       resource_id: Optional[str], source: str, ai_log: dict[str, Any]) -> bool:
    return bool(await db.rpc("nurture_save_message", p_id=message_id, p_status=status, p_subject=subject,
                             p_preheader=preheader, p_body=body, p_resource_id=resource_id,
                             p_source=source, p_ai_log=ai_log))


# --- sending ----------------------------------------------------------------------------

async def claim_sending(now: datetime, limit: int) -> list[dict[str, Any]]:
    return await db.rows("nurture_claim_sending", p_now=now, p_limit=limit)


async def send_check(enrollment_id: str) -> Optional[dict[str, Any]]:
    return await db.rpc("nurture_send_check", p_enrollment_id=enrollment_id)


async def mark_sent(message_id: str, smtp_message_id: str, next_send_at: Optional[datetime]) -> bool:
    return bool(await db.rpc("nurture_mark_sent", p_message_id=message_id,
                             p_smtp_message_id=smtp_message_id, p_next_send_at=next_send_at))


async def unsend(message_id: str, status: str, send_at: Optional[datetime] = None,
                 error: Optional[str] = None) -> None:
    """Put a claimed email back: ready (later, at send_at) | cancelled | failed."""
    await db.rpc("nurture_unsend", p_message_id=message_id, p_status=status, p_send_at=send_at, p_error=error)


async def sent_since(since: datetime) -> int:
    return int(await db.rpc("nurture_sent_since", p_since=since) or 0)


async def click(message_id: str, link: str, user_agent: str, bot_seconds: int) -> Optional[dict[str, Any]]:
    return await db.rpc("nurture_click", p_message_id=message_id, p_link=link, p_user_agent=user_agent,
                        p_bot_seconds=bot_seconds)


# --- replies ------------------------------------------------------------------------------

async def register_mailbox(email: str, name: str) -> Optional[dict[str, Any]]:
    rows = await db.rows("nurture_register_mailbox", p_email=email, p_name=name)
    return rows[0] if rows else None


async def match_reply(message_ids: list[str], email: str) -> Optional[str]:
    result = await db.rpc("nurture_match_reply", p_message_ids=message_ids, p_email=email)
    return str(result) if result else None


async def save_reply(inbox_id: str, enrollment_id: str, event_type: str, from_email: str, to_email: str,
                     subject: str, message_id: Optional[str], in_reply_to: Optional[str], snippet: str,
                     received_at: datetime) -> bool:
    return bool(await db.rpc(
        "nurture_save_reply", p_inbox_id=inbox_id, p_enrollment_id=enrollment_id, p_event_type=event_type,
        p_from_email=from_email, p_to_email=to_email, p_subject=subject, p_message_id=message_id,
        p_in_reply_to=in_reply_to, p_snippet=snippet, p_received_at=received_at,
    ))


async def claim_replies(limit: int = 10) -> list[dict[str, Any]]:
    return await db.rows("nurture_claim_replies", p_limit=limit)


async def thread(enrollment_id: str, before: Any, exclude_event: Optional[str], limit: int = 4) -> list[dict[str, Any]]:
    return await db.rows("nurture_thread", p_enrollment_id=enrollment_id, p_before=before,
                         p_exclude_event=exclude_event, p_limit=limit)


# --- hand-off notifications ------------------------------------------------------------

async def unnotified() -> list[str]:
    return [str(r["id"]) for r in await db.rows("nurture_unnotified")]


async def notified(enrollment_id: str, summary: str) -> None:
    await db.rpc("nurture_notified", p_id=enrollment_id, p_summary=summary)


# --- console ------------------------------------------------------------------------------

async def list_enrollments(search: Optional[str], status: Optional[str], persona: Optional[str],
                           temperature: Optional[str], review: Optional[bool], limit: int,
                           offset: int) -> tuple[list[dict[str, Any]], int]:
    rows = await db.rows("nurture_list", p_search=search, p_status=status, p_persona=persona,
                         p_temperature=temperature, p_review=review, p_limit=limit, p_offset=offset)
    total = int(rows[0]["total"]) if rows else 0
    return [{k: v for k, v in r.items() if k != "total"} for r in rows], total


LEAD_VIEWS = ("all", "can_join", "in_nurture", "finished")


async def browse(search: Optional[str], view: str, cooldown_days: int, blocked: list[str], skip_external: bool,
                 limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Every shared lead with its nurture state (0012_nurture_lead_list.sql)."""
    rows = await db.rows("nurture_browse", p_search=search, p_view=view, p_cooldown_days=cooldown_days,
                         p_blocked_statuses=blocked, p_skip_external=skip_external, p_limit=limit,
                         p_offset=offset)
    total = int(rows[0]["total"]) if rows else 0
    return [{k: v for k, v in r.items() if k != "total"} for r in rows], total


async def detail(enrollment_id: str) -> Optional[dict[str, Any]]:
    return await db.rpc("nurture_detail", p_id=enrollment_id)


async def review(hours: int = 72) -> list[dict[str, Any]]:
    return await db.rows("nurture_review", p_hours=hours)


async def review_message(message_id: str, action: str, subject: Optional[str] = None,
                         preheader: Optional[str] = None, body: Optional[str] = None) -> bool:
    """edit | approve | reject"""
    return bool(await db.rpc("nurture_review_message", p_id=message_id, p_action=action,
                             p_subject=subject, p_preheader=preheader, p_body=body))


async def handoff_list(limit: int = 100) -> list[dict[str, Any]]:
    return await db.rows("nurture_handoff_list", p_limit=limit)


async def stats(start: datetime, end: datetime) -> dict[str, Any]:
    return await db.rpc("nurture_stats", p_from=start, p_to=end) or {}
