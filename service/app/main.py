"""FastAPI entrypoint.

Serves two purposes: it hosts the background workers, and it exposes the
LangGraph reasoning layer over HTTP so a sequence can be generated or previewed
on demand (useful for testing personas without touching lead data).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from app import db, regions, repository as repo, settings_store
from app.auth import SessionAuth, read_auth_config
from app.config import get_settings
from app.graph.build import generate_sequence
from app.graph.persona import keyword_persona
from app.llm.factory import close_gateway
from app.mail.base import IncomingMessage, MailError, OutgoingMessage
from app.mail.factory import close_transports, get_sender
from app.calendar.base import CalendarError
from app.calendar.factory import close_calendar, get_calendar
from app.workers import alerts, intake, poller, scheduler, sender
from app.workers import triage as triage_worker

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("out01")

# CMS-linked login (app/auth.py). Read at import so a missing or unsafe login
# setting stops the service from starting rather than leaving it open.
session_auth = SessionAuth(read_auth_config(), api_key=get_settings().api_key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await settings_store.refresh()
    if get_settings().run_workers:
        scheduler.start()
    else:
        log.info("RUN_WORKERS=false — HTTP only, no background jobs")
    try:
        yield
    finally:
        scheduler.shutdown()
        await close_gateway()
        await close_transports()
        await close_calendar()
        await session_auth.close()
        await db.disconnect()


app = FastAPI(
    title="OUT-01 Cold Email Automation",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(db.DatabaseError)
async def database_error(_: Request, exc: db.DatabaseError) -> JSONResponse:
    """Surface the database's reason instead of a bare 500."""
    log.error("database error: %s", exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.include_router(session_auth.router, prefix="/auth")

# Every console endpoint needs a CMS session (or the optional API_KEY bearer
# for scripts). Only /health stays open, for the container healthcheck.
require_user = session_auth.require_session


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LeadPayload(BaseModel):
    email: EmailStr = "prospect@example.com"
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    timezone: str = "UTC"


class PreviewRequest(BaseModel):
    lead: LeadPayload
    step_count: int = Field(default=4, ge=1, le=8)


class EnrollItem(BaseModel):
    # public.leads.id (uuid) or public.leads.lead_id (text) — either works.
    lead_id: str
    # Optional overrides when the shared tables lack or misstate these.
    job_title: Optional[str] = None
    company: Optional[str] = None
    # IANA name, e.g. "Asia/Dhaka". Only needed when the lead's region data is
    # missing or wrong; otherwise the timezone is worked out automatically.
    timezone: Optional[str] = None

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not regions.is_valid_timezone(value):
            raise ValueError(f"unknown timezone {value!r}; use an IANA name like 'Asia/Dhaka'")
        return value.strip() if value else None


class EnrollRequest(BaseModel):
    leads: list[EnrollItem] = Field(min_length=1, max_length=500)


class RetryRequest(BaseModel):
    # Optional corrections applied before drafting again.
    job_title: Optional[str] = None
    company: Optional[str] = None
    timezone: Optional[str] = None

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value and not regions.is_valid_timezone(value):
            raise ValueError(f"unknown timezone {value!r}; use an IANA name like 'Asia/Dhaka'")
        return value.strip() if value else None


ENROLLMENT_STATUSES = {
    "ready_for_outreach", "processing", "sequence_ready", "sending", "sent",
    "replied", "bounced", "unsubscribed", "stopped", "manual_review", "failed",
}


class SettingUpdate(BaseModel):
    value: object
    description: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    try:
        database = "ok" if await repo.ping() else "error: unexpected ping reply"
    except Exception as exc:
        database = f"error: {exc}"

    s = get_settings()
    return {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "llm_provider": s.llm_provider,
        "mail_sender": s.mail_sender,
        "mail_reader": s.mail_reader,
        "workers": s.run_workers,
        "dry_run": s.dry_run,
        # Background jobs: interval and next run (empty when RUN_WORKERS=false).
        "schedule": scheduler.status(),
    }


@app.post("/preview", dependencies=[Depends(require_user)])
async def preview(request: PreviewRequest) -> dict:
    """Draft a sequence for an arbitrary lead without persisting anything.

    The fastest way to eyeball persona routing and copy quality.
    """
    lead = request.lead.model_dump()
    lead["id"] = "preview"
    result = await generate_sequence(lead, step_count=request.step_count)
    return {
        "persona": result.get("persona"),
        "routing_mode": result.get("routing_mode"),
        "status": result.get("status"),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "validation_errors": result.get("validation_errors") or [],
        "emails": result.get("emails") or [],
    }


@app.get("/route", dependencies=[Depends(require_user)])
async def route(job_title: str) -> dict:
    """Show which persona a job title lands on, and by which mechanism."""
    persona = keyword_persona(job_title)
    if persona:
        return {"job_title": job_title, "persona": persona, "routing_mode": "keyword"}
    from app.graph.persona import classify_with_llm

    persona, confidence = await classify_with_llm(job_title)
    return {
        "job_title": job_title,
        "persona": persona,
        "routing_mode": "llm",
        "confidence": confidence,
    }


@app.post("/enroll", dependencies=[Depends(require_user)])
async def enroll(request: EnrollRequest) -> dict:
    """Put existing shared leads into cold outreach.

    Nothing is written to public.leads. Each lead is checked for an email,
    a contactable status, an existing email-nurture journey and suppression;
    the response says which were enrolled and why the rest were not.
    """
    contactable = await settings_store.contactable_statuses()
    skip_nurture = bool(await settings_store.get("skip_if_in_email_nurture"))
    results = []
    for item in request.leads:
        outcome, lead_uuid = await repo.enroll_lead(
            item.lead_id.strip(), contactable, skip_nurture,
            job_title=item.job_title, company=item.company, timezone=item.timezone,
        )
        results.append({"lead_id": item.lead_id, "id": lead_uuid, "outcome": outcome})
    enrolled = sum(1 for r in results if r["outcome"] == "enrolled")
    return {"enrolled": enrolled, "results": results}


@app.get("/enrollments", dependencies=[Depends(require_user)])
async def list_enrollments(status: Optional[str] = None, limit: int = 100) -> dict:
    """Enrolled leads, newest activity first.

    ``status`` takes one or more comma-separated values, e.g.
    ``?status=manual_review,failed`` for the ones that need attention; each
    comes with ``last_error`` saying why.
    """
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
    unknown = set(statuses or []) - ENROLLMENT_STATUSES
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown status: {', '.join(sorted(unknown))}")
    return {"enrollments": await repo.list_enrollments(statuses, max(1, min(limit, 500)))}


@app.post("/enrollments/{lead_id}/retry", dependencies=[Depends(require_user)])
async def retry_enrollment(lead_id: UUID, request: RetryRequest | None = None) -> dict:
    """Draft a stuck lead's sequence again (manual_review, failed or stopped).

    Pass a job title / company / timezone to correct the data first. The lead
    is picked up by the next intake run.
    """
    body = request or RetryRequest()
    ok = await repo.retry_enrollment(
        str(lead_id), body.job_title, body.company, body.timezone
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="only enrollments in manual_review, failed or stopped can be retried",
        )
    return {"lead_id": str(lead_id), "status": "ready_for_outreach"}


@app.post("/run/intake", dependencies=[Depends(require_user)])
async def run_intake() -> dict:
    return {"result": await intake.run_once()}


@app.post("/run/sender", dependencies=[Depends(require_user)])
async def run_sender() -> dict:
    return {"result": await sender.run_once()}


@app.post("/send-now", dependencies=[Depends(require_user)])
async def send_now(lead_id: Optional[UUID] = None) -> dict:
    """Send the next email of every scheduled lead now (or of ``lead_id``).

    Ignores business hours only; the kill switch, lead status, suppression and
    daily caps still apply, and leads emailed in the last 24 hours are
    skipped. With DRY_RUN=true nothing actually leaves.
    """
    return {"result": await sender.send_now(str(lead_id) if lead_id else None)}


INBOX_EVENT_TYPES = {"reply", "unsubscribe", "bounce", "auto_reply", "unknown"}


@app.get("/replies", dependencies=[Depends(require_user)])
async def replies(type: str = "reply", limit: int = 50) -> dict:
    """What prospects sent back, newest first, with the lead it belongs to.

    ``type`` takes comma-separated values from reply, unsubscribe, bounce,
    auto_reply, or ``all``.
    """
    types = None if type == "all" else [t.strip() for t in type.split(",") if t.strip()]
    unknown = set(types or []) - INBOX_EVENT_TYPES
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown type: {', '.join(sorted(unknown))}")
    events = await repo.list_inbox_events(types, max(1, min(limit, 200)))
    for e in events:
        e["gmail_link"] = alerts.gmail_link(e["inbox_email"], e["message_id"])
    return {"replies": events}


@app.post("/replies/test-alert", dependencies=[Depends(require_user)])
async def test_reply_alert() -> dict:
    """Send a sample reply alert to reply_alert_email, to check the setup."""
    to = str(await settings_store.get("reply_alert_email") or "").strip()
    if not to:
        raise HTTPException(status_code=400, detail="set an alert address first")
    inboxes = await repo.active_inboxes()
    if not inboxes:
        raise HTTPException(status_code=400, detail="no active inbox to send from")
    inbox = inboxes[0]
    sample = IncomingMessage(
        uid=0, message_id=None, in_reply_to=None, references=[],
        from_email="prospect@example.com", to_email=inbox.email,
        subject="Re: quick question (test alert)",
        body_snippet="This is a test alert. A real one shows the prospect's reply here.",
        received_at=datetime.now(timezone.utc), is_bounce_report=False,
        bounced_recipient=None, bounce_is_permanent=False, is_auto_reply=False,
    )
    subject, body = alerts.build_alert(
        inbox, {"first_name": "Test", "last_name": "Prospect", "company": "Example Co"}, sample
    )
    try:
        await get_sender().send(inbox, OutgoingMessage(to_email=to, subject=subject, body_text=body))
    except MailError as exc:
        raise HTTPException(status_code=502, detail=f"could not send: {exc}")
    return {"sent_to": to, "from": inbox.email, "dry_run": get_settings().dry_run}


@app.get("/upcoming", dependencies=[Depends(require_user)])
async def upcoming(limit: int = 10) -> dict:
    """The next scheduled emails and when they are due (UTC)."""
    return {"emails": await repo.upcoming_emails(max(1, min(limit, 100)))}


@app.post("/run/poller", dependencies=[Depends(require_user)])
async def run_poller() -> dict:
    return {"result": await poller.run_once()}


@app.get("/stats", dependencies=[Depends(require_user)])
async def stats() -> dict:
    return await repo.stats()


@app.get("/settings", dependencies=[Depends(require_user)])
async def read_settings() -> dict:
    return {"settings": await repo.get_settings()}


@app.put("/settings/{key}", dependencies=[Depends(require_user)])
async def write_setting(key: str, update: SettingUpdate) -> dict:
    await settings_store.set_value(key, update.value, update.description)
    return {"key": key, "value": update.value}


@app.post("/suppress", dependencies=[Depends(require_user)])
async def suppress(email: EmailStr, reason: str = "manual") -> dict:
    await repo.suppress(str(email), reason)
    lead_id = await repo.match_lead_by_email(str(email))
    cancelled = 0
    if lead_id:
        cancelled = await repo.cancel_sequence(lead_id, "unsubscribed", reason)
    return {"email": email, "reason": reason, "steps_cancelled": cancelled}


# ---------------------------------------------------------------------------
# Reply triage (OUT-05)
# ---------------------------------------------------------------------------

TRIAGE_CATEGORIES = {"interested", "not_now", "wrong_person", "objection", "out_of_office", "unsubscribe", "other"}
DRAFT_STATUSES = {"pending", "approved", "sending", "sent", "rejected", "cancelled", "failed"}


def _csv(value: Optional[str], allowed: set[str], what: str) -> Optional[list[str]]:
    if not value or value == "all":
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    unknown = set(items) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown {what}: {', '.join(sorted(unknown))}")
    return items


class RetriageRequest(BaseModel):
    # A person's correction; the reply is re-run with this category.
    category: Optional[str] = None

    @field_validator("category")
    @classmethod
    def _known(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in TRIAGE_CATEGORIES:
            raise ValueError(f"unknown category {value!r}")
        return value


class DraftEdit(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=5000)


@app.get("/triage/stats", dependencies=[Depends(require_user)])
async def triage_stats() -> dict:
    """Counts by category, drafts by status, and the reply-to-meeting rate."""
    stats = await repo.triage_stats()
    replied = stats.get("replied_leads") or 0
    stats["reply_to_meeting_rate"] = round((stats.get("meeting_leads") or 0) / replied, 4) if replied else None
    return stats


@app.get("/triage/events", dependencies=[Depends(require_user)])
async def triage_events(category: Optional[str] = None, type: str = "reply,auto_reply,unsubscribe",
                        limit: int = 50) -> dict:
    """Replies with their triage result, newest first."""
    events = await repo.list_inbox_events(
        _csv(type, INBOX_EVENT_TYPES, "type"), max(1, min(limit, 200)),
        _csv(category, TRIAGE_CATEGORIES, "category"),
    )
    for e in events:
        e["gmail_link"] = alerts.gmail_link(e["inbox_email"], e["message_id"])
    return {"events": events}


@app.post("/triage/events/{event_id}/rerun", dependencies=[Depends(require_user)])
async def triage_rerun(event_id: UUID, request: RetriageRequest | None = None) -> dict:
    """Triage a reply again, optionally with the correct category.

    It is picked up by the next triage run (or POST /run/triage).
    """
    category = (request or RetriageRequest()).category
    if not await repo.retriage_event(str(event_id), category):
        raise HTTPException(status_code=404, detail="no reply with that id can be triaged")
    return {"event_id": str(event_id), "status": "pending", "category": category}


@app.get("/triage/drafts", dependencies=[Depends(require_user)])
async def triage_drafts(status: str = "pending,approved,failed", limit: int = 50) -> dict:
    return {"drafts": await repo.list_drafts(_csv(status, DRAFT_STATUSES, "status"), max(1, min(limit, 200)))}


@app.put("/triage/drafts/{draft_id}", dependencies=[Depends(require_user)])
async def triage_edit_draft(draft_id: UUID, edit: DraftEdit) -> dict:
    if not await repo.update_draft(str(draft_id), edit.subject.strip(), edit.body.strip()):
        raise HTTPException(status_code=409, detail="only a waiting or failed draft can be edited")
    return {"id": str(draft_id), "saved": True}


@app.post("/triage/drafts/{draft_id}/approve")
async def triage_approve_draft(draft_id: UUID, user: dict = Depends(require_user)) -> dict:
    """Send it at the next triage run (within a minute)."""
    if not await repo.decide_draft(str(draft_id), "approve", user.get("email") or user.get("id", "")):
        raise HTTPException(status_code=409, detail="this draft is no longer waiting")
    return {"id": str(draft_id), "status": "approved"}


@app.post("/triage/drafts/{draft_id}/reject")
async def triage_reject_draft(draft_id: UUID, user: dict = Depends(require_user)) -> dict:
    if not await repo.decide_draft(str(draft_id), "reject", user.get("email") or user.get("id", "")):
        raise HTTPException(status_code=409, detail="this draft is no longer waiting")
    return {"id": str(draft_id), "status": "rejected"}


@app.get("/triage/meetings", dependencies=[Depends(require_user)])
async def triage_meetings(limit: int = 100) -> dict:
    return {"meetings": await repo.list_meetings(max(1, min(limit, 500)))}


@app.get("/triage/snoozed", dependencies=[Depends(require_user)])
async def triage_snoozed(limit: int = 100) -> dict:
    return {"snoozed": await repo.list_snoozed(max(1, min(limit, 500)))}


@app.post("/run/triage", dependencies=[Depends(require_user)])
async def run_triage() -> dict:
    return {"result": await triage_worker.run_once()}


@app.post("/triage/evaluate", dependencies=[Depends(require_user)])
async def triage_evaluate() -> dict:
    """Run the 50-reply test set through the classifier (takes a minute or two)."""
    from app.triage.evaluate import evaluate

    report = await evaluate()
    await settings_store.set_value("triage_last_eval", report, "Last accuracy check of the reply classifier.")
    return report


@app.get("/triage/evaluation", dependencies=[Depends(require_user)])
async def triage_evaluation() -> dict:
    rows = {r["key"]: r["value"] for r in await repo.get_settings()}
    return {"report": rows.get("triage_last_eval")}


@app.get("/calendar/status", dependencies=[Depends(require_user)])
async def calendar_status() -> dict:
    """Which calendar is configured, and whether it can be built."""
    name = get_settings().calendar_provider
    try:
        cal = get_calendar()
    except CalendarError as exc:
        return {"provider": name, "ready": False, "error": str(exc), "booking_link": None}
    return {"provider": name, "ready": cal is not None,
            "booking_link": cal.booking_link() if cal else None, "error": None}


@app.get("/calendar/slots", dependencies=[Depends(require_user)])
async def calendar_slots(timezone_name: str = "UTC", days: int = 7) -> dict:
    """Read-only check: the next free slots and what a lead would be offered."""
    from app.calendar.slots import describe_slot, pick_slots

    if not regions.is_valid_timezone(timezone_name):
        raise HTTPException(status_code=422, detail="unknown timezone")
    try:
        cal = get_calendar()
    except CalendarError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if cal is None:
        raise HTTPException(status_code=400, detail="CALENDAR_PROVIDER is none")
    now = datetime.now(timezone.utc)
    try:
        free = await cal.free_slots(now, now + timedelta(days=max(1, min(days, 30))))
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    cfg = (await settings_store.triage_config())["meeting"]
    hours = await settings_store.business_hours(timezone_name)
    offer = pick_slots(free, timezone_name, hours, now, int(cfg["slots_to_offer"]), int(cfg["min_notice_hours"]))
    return {
        "provider": cal.name,
        "free_count": len(free),
        "free": [s.to_json() for s in free[:20]],
        "would_offer": [describe_slot(s, timezone_name) for s in offer],
    }
