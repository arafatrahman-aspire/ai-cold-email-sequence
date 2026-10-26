"""FastAPI entrypoint.

Serves two purposes: it hosts the background workers, and it exposes the
LangGraph reasoning layer over HTTP so a sequence can be generated or previewed
on demand (useful for testing personas without touching lead data).
"""

from __future__ import annotations

import asyncio

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from app import db, regions, repository as repo, settings_store
from app.auth import SessionAuth, is_admin, read_auth_config
from app.config import get_settings
from app.graph.build import generate_sequence
from app.graph.persona import keyword_persona
from app.llm.factory import close_gateway
from app.mail.base import Inbox, IncomingMessage, MailError, OutgoingMessage
from app.mail.factory import close_transports, get_sender
from app.calendar.base import CalendarError
from app.calendar.factory import close_calendar, get_calendar
from app.nurture import api as nurture_api
from app.workers import alerts, calendar_sync, engagement, intake, poller, reminders, scheduler, sender
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


async def require_admin(user: dict = Depends(require_user)) -> dict:
    """Configuration, test and dev endpoints: admin / superadmin only."""
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="admin_only")
    return user


# Email Nurture: public click / unsubscribe pages (no login) and the console API.
app.include_router(nurture_api.public_router())
app.include_router(nurture_api.console_router(require_user, require_admin))


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


class RemoveRequest(BaseModel):
    lead_ids: list[UUID] = Field(min_length=1, max_length=500)


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


class EmailEdit(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)


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
        "environment": s.app_env,
        # Background jobs: interval and next run (empty when RUN_WORKERS=false).
        "schedule": scheduler.status(),
    }


@app.post("/preview", dependencies=[Depends(require_admin)])
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


@app.get("/route", dependencies=[Depends(require_admin)])
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


@app.get("/leads", dependencies=[Depends(require_user)])
async def browse_leads(
    search: Optional[str] = None, view: str = "all", source: Optional[str] = None,
    limit: int = 50, offset: int = 0,
) -> dict:
    """The shared leads, newest first, with their cold-sequence state.

    ``view``: all | not_enrolled | available | in_sequence | finished. Each
    lead not yet enrolled carries ``blocked`` (why it cannot be) or null.
    ``counts`` gives every view's size for the same search.
    """
    if view not in repo.LEAD_VIEWS:
        raise HTTPException(status_code=422, detail=f"view must be one of: {', '.join(repo.LEAD_VIEWS)}")
    contactable = await settings_store.contactable_statuses()
    skip_nurture = bool(await settings_store.get("skip_if_in_email_nurture"))
    search = (search or "").strip()[:100] or None

    async def page(v: str, n: int, skip: int):
        return await repo.browse_leads(search, v, source, contactable, skip_nurture, n, skip)

    (leads, total), *others = await asyncio.gather(
        page(view, max(1, min(limit, 200)), max(0, offset)),
        *(page(v, 1, 0) for v in repo.LEAD_VIEWS),
    )
    return {"leads": leads, "total": total,
            "counts": {v: t for v, (_, t) in zip(repo.LEAD_VIEWS, others)}}


@app.post("/enrollments/remove")
async def remove_from_sequence(request: RemoveRequest, user: dict = Depends(require_user)) -> dict:
    """Take leads out of the cold sequence. Unsent emails and reply drafts are
    cancelled; what was sent stays in the history. Enroll again any time
    (POST /enrollments/{id}/retry starts a fresh sequence)."""
    who = user.get("email") or user.get("id") or "someone"
    results = []
    for lead_id in request.lead_ids:
        outcome = await repo.remove_from_sequence(str(lead_id), f"removed from the sequence by {who}")
        results.append({"lead_id": str(lead_id), "outcome": outcome})
    return {"removed": sum(r["outcome"] == "removed" for r in results), "results": results}


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


@app.get("/enrollments/{lead_id}/emails", dependencies=[Depends(require_user)])
async def lead_emails(lead_id: UUID) -> dict:
    """The lead's drafted emails (and sent ones), in step order."""
    return {"emails": await repo.lead_emails(str(lead_id))}


# ---------------------------------------------------------------------------
# Email design: the signature and look of every email, and previews
# ---------------------------------------------------------------------------

class EmailStyle(BaseModel):
    name: str = Field(default="", max_length=120)
    title: str = Field(default="", max_length=120)
    company: str = Field(default="", max_length=120)
    website: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=200)
    sign_off: str = Field(default="", max_length=40)
    opt_out: str = Field(default="", max_length=200)
    accent: str = Field(default="#2563eb", pattern=r"^#[0-9a-fA-F]{6}$")
    html: bool = True
    layout: str = Field(default="plain", pattern="^(plain|card)$")
    unsubscribe_header: bool = False


class RenderRequest(BaseModel):
    kind: str = Field(pattern="^(cold|reply|nurture|handoff|alert)$")
    subject: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=20000)
    preheader: str = Field(default="", max_length=300)
    first_name: Optional[str] = Field(default=None, max_length=80)
    resource_id: Optional[str] = Field(default=None, max_length=64)
    cta_type: str = Field(default="demo", max_length=20)
    message_id: Optional[str] = Field(default=None, max_length=64)
    # A nurture email: its enrollment, to find the resource it links to.
    enrollment_id: Optional[UUID] = None


SAMPLES = {
    "cold": ("Phishing reports without the chasing",
             "Hi Dana,\n\nMost IT teams I talk to run phishing simulations by hand: build the template, pick the "
             "targets, chase the results into a spreadsheet.\n\nWe automate that end to end, so a campaign "
             "takes minutes to set up and the report writes itself.\n\nWould it be worth a 15-minute look "
             "next week?"),
    "reply": ("Re: Phishing reports without the chasing",
              "Hi Dana,\n\nThanks for getting back to me. Here are two times that work on my side:\n\n"
              "Tuesday 14 October, 10:00-10:30 (Europe/London)\nWednesday 15 October, 14:00-14:30 "
              "(Europe/London)\n\nOr pick any time that suits you here: https://cal.com/aspire/30min\n\nAlex"),
    "nurture": ("The hidden IT cost of phishing",
                "Phishing rarely shows up as one big event for IT. It shows up as tickets, password resets "
                "and mailbox clean-ups.\n\nThe {{RESOURCE_LINK}} breaks down where that time goes and how "
                "teams cut it.\n\nIf you would like to see how other IT teams handle it, you can "
                "{{CTA_DEMO}} or {{CTA_PRICING}}."),
}


@app.get("/email-design", dependencies=[Depends(require_user)])
async def get_email_design() -> dict:
    return {"style": await settings_store.email_style()}


@app.put("/email-design")
async def save_email_design(style: EmailStyle, user: dict = Depends(require_user)) -> dict:
    """Marketing owns the look: signature, sign-off, opt-out line, colour."""
    value = {k: (v.strip() if isinstance(v, str) else v) for k, v in style.model_dump().items()}
    await settings_store.set_value("email_style", value, f"email design, last changed by {user.get('email') or 'someone'}")
    return {"style": await settings_store.email_style()}


@app.post("/email-design/render", dependencies=[Depends(require_user)])
async def render_email(req: RenderRequest) -> dict:
    """The email as it would arrive, plus who it would come from (for the
    inbox-style preview)."""
    from app.nurture import sending as nsending

    out = await _render_email(req)
    if req.kind == "nurture":
        box = await nsending.sending_inbox()
    else:
        inboxes = await repo.active_inboxes()
        box = inboxes[0] if inboxes else None
    style = await settings_store.email_style()
    out.setdefault("from_name", style.get("name") or (box.display_name if box else "") or "")
    out.setdefault("from_email", box.email if box else "")
    return out


async def _render_email(req: RenderRequest) -> dict:
    """An email exactly as it would be sent (empty fields: a sample), as
    {subject, html, text}. Nothing is sent."""
    from app.mail import templates
    from app.nurture import content as ncontent, exits as nexits, options as noptions, sending as nsending
    from app.nurture.checks import Draft
    from app.nurture.render import render as nrender

    style = await settings_store.email_style()
    inboxes = await repo.active_inboxes()
    inbox = inboxes[0] if inboxes else None
    subject, body = SAMPLES.get(req.kind, ("", ""))
    subject, body = req.subject or subject, req.body or body

    if req.kind in ("cold", "reply"):
        fake = inbox or type("Box", (), {"display_name": "Alex Rivera"})()
        mail = await (sender.compose_cold(subject, body, fake) if req.kind == "cold"
                      else triage_worker.compose_answer(subject, body, fake))
        return {"subject": subject, "html": mail.html, "text": mail.text,
                "note": "" if mail.html else "HTML is turned off: this email goes out as plain text."}

    if req.kind == "nurture":
        cfg = await noptions.load()
        resources = {r["id"]: r for r in await ncontent.resources()}
        resource_id = req.resource_id
        if not resource_id and req.enrollment_id and req.message_id:
            from app.nurture import repo as nrepo
            messages = ((await nrepo.detail(str(req.enrollment_id))) or {}).get("messages") or []
            resource_id = next((m.get("resource_id") for m in messages if str(m.get("id")) == req.message_id), None)
        resource = resources.get(resource_id) if resource_id else next(iter(resources.values()), None)
        if resource is None:
            resource = {"id": "sample", "title": "IT phishing cost guide", "url": "https://example.com/guide"}
        if not style.get("name"):
            box = await nsending.sending_inbox()
            style = {**style, "name": (box.display_name if box else "") or ""}
        branding = {**cfg["branding"], "demo_url": cfg["branding"].get("demo_url") or "https://example.com/demo",
                    "pricing_url": cfg["branding"].get("pricing_url") or "https://example.com/pricing"}
        public = nsending.public_url() or "https://example.com"
        out = nrender(Draft(subject, req.preheader or "Where IT time goes, and how teams get it back", body,
                            resource.get("id"), req.cta_type), message_id=req.message_id or "preview",
                      first_name=req.first_name or "Dana", resource=resource, branding=branding,
                      public_url=public, unsubscribe_url=f"{public}/n/u/preview", style=style)
        return {"subject": subject, "html": out.html, "text": out.text, "note": "; ".join(out.problems)}

    if req.kind == "handoff":
        detail = {"enrollment": {"email": "dana@northwind.com", "persona": "it", "temperature": "warm",
                                 "exit_reason": "pricing_click"},
                  "lead": {"first_name": "Dana", "last_name": "Okafor", "job_title": "IT Manager",
                           "company": "Northwind", "tier": "Warm"},
                  "messages": [{"status": "sent", "step": i, "subject": f"Email {i}"} for i in (1, 2, 3)],
                  "events": [{"kind": "click", "detail": {"step": 3, "link": "pricing"}}],
                  "replies": []}
        summary = ("Dana runs IT at Northwind and has read three emails about phishing workload. She "
                   "clicked pricing in email 3. Open with how much ticket time a typical campaign saves.")
        console = os.environ.get("APP_PUBLIC_URL", "").rstrip("/")
        s, text = nexits.notification(detail, summary, console)
        return {"subject": s, "html": nexits.notification_html(detail, summary, console), "text": text, "note": ""}

    from app.mail.base import IncomingMessage
    msg = IncomingMessage(uid=1, message_id="<sample@x>", in_reply_to=None, references=[],
                          from_email="dana@northwind.com", to_email=inbox.email if inbox else "you@aspire.com",
                          subject="Re: Phishing reports without the chasing",
                          body_snippet="Sounds interesting. Could we talk on Tuesday at 10?",
                          received_at=datetime.now(timezone.utc))
    box = inbox or Inbox("x", "you@aspire.com", "Alex", "smtp", "X", None, 0)
    lead = {"first_name": "Dana", "last_name": "Okafor", "job_title": "IT Manager", "company": "Northwind"}
    s, text = alerts.build_alert(box, lead, msg)
    return {"subject": s, "html": alerts.alert_html(box, lead, msg), "text": text, "note": ""}


# ---------------------------------------------------------------------------
# Campaigns (0017_campaigns.sql): an audience, a playbook, start/stop, results
# ---------------------------------------------------------------------------

_Short = Field(default="", max_length=300)


class Audience(BaseModel):
    titles: list[str] = Field(default_factory=list, max_length=30)
    industries: list[str] = Field(default_factory=list, max_length=30)
    countries: list[str] = Field(default_factory=list, max_length=30)
    sources: list[str] = Field(default_factory=list, max_length=30)
    tiers: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("titles", "industries", "countries", "sources", "tiers")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return [s.strip()[:100] for s in v if s and s.strip()]


class Playbook(BaseModel):
    angle: str = Field(default="", max_length=500)
    value_points: list[str] = Field(default_factory=list, max_length=10)
    proof: list[str] = Field(default_factory=list, max_length=10)
    offer: str = _Short
    cta: str = _Short
    tone: str = _Short
    banned_claims: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("value_points", "proof", "banned_claims")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return [s.strip()[:300] for s in v if s and s.strip()]


class CampaignBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    audience: Audience = Field(default_factory=Audience)
    playbook: Playbook = Field(default_factory=Playbook)
    auto_add: bool = False


class CampaignEnroll(BaseModel):
    lead_ids: list[UUID] = Field(default_factory=list, max_length=500)
    # Every matching lead (up to 500) instead of the ticked ones.
    all_matching: bool = False


CAMPAIGN_ACTIONS = {"start": "active", "pause": "paused", "resume": "active", "end": "ended"}


async def _campaign(campaign_id: UUID) -> dict:
    c = await repo.campaign_get(str(campaign_id))
    if not c:
        raise HTTPException(status_code=404, detail="no such campaign")
    return c


@app.get("/campaigns", dependencies=[Depends(require_user)])
async def list_campaigns() -> dict:
    """Every campaign with its results, and General (leads in no campaign)."""
    campaigns, results = await asyncio.gather(repo.campaign_list(), repo.campaign_results())
    by_id = {str(r["campaign_id"]) if r["campaign_id"] else None: r for r in results}
    empty = {"leads": 0, "in_sequence": 0, "emails_sent": 0, "replied": 0, "interested": 0,
             "meetings": 0, "bounced": 0, "unsubscribed": 0}

    def numbers(key):
        r = by_id.get(key) or {}
        return {k: int(r.get(k) or 0) for k in empty}

    return {"campaigns": [{**c, "results": numbers(str(c["id"]))} for c in campaigns],
            "general": {"results": numbers(None)}}


@app.post("/campaigns")
async def create_campaign(body: CampaignBody, user: dict = Depends(require_user)) -> dict:
    try:
        cid = await repo.campaign_save(None, body.name.strip(), body.audience.model_dump(),
                                       body.playbook.model_dump(), body.auto_add, user.get("email") or "someone")
    except db.DatabaseError as exc:
        if "campaigns_name_idx" in str(exc) or "duplicate" in str(exc):
            raise HTTPException(status_code=409, detail="a campaign with this name already exists")
        raise
    return await repo.campaign_get(cid)


@app.put("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: UUID, body: CampaignBody, user: dict = Depends(require_user)) -> dict:
    await _campaign(campaign_id)
    try:
        cid = await repo.campaign_save(str(campaign_id), body.name.strip(), body.audience.model_dump(),
                                       body.playbook.model_dump(), body.auto_add, user.get("email") or "someone")
    except db.DatabaseError as exc:
        if "campaigns_name_idx" in str(exc) or "duplicate" in str(exc):
            raise HTTPException(status_code=409, detail="a campaign with this name already exists")
        raise
    if not cid:
        raise HTTPException(status_code=409, detail="an ended campaign cannot be changed")
    return await repo.campaign_get(cid)


@app.get("/campaigns/{campaign_id}/audience", dependencies=[Depends(require_user)])
async def campaign_audience(campaign_id: UUID, limit: int = 50, offset: int = 0) -> dict:
    """Shared leads that match the campaign and are in no cold sequence yet."""
    c = await _campaign(campaign_id)
    leads, total = await repo.campaign_audience(
        c.get("audience") or {}, await settings_store.contactable_statuses(), max(1, min(limit, 200)), max(0, offset))
    return {"leads": leads, "total": total}


@app.post("/campaigns/{campaign_id}/enroll", dependencies=[Depends(require_user)])
async def campaign_enroll(campaign_id: UUID, body: CampaignEnroll) -> dict:
    c = await _campaign(campaign_id)
    if c["status"] == "ended":
        raise HTTPException(status_code=409, detail="this campaign has ended")
    contactable = await settings_store.contactable_statuses()
    skip_nurture = bool(await settings_store.get("skip_if_in_email_nurture"))
    ids = [str(i) for i in body.lead_ids]
    if body.all_matching:
        leads, _ = await repo.campaign_audience(c.get("audience") or {}, contactable, 500)
        ids = [str(lead["id"]) for lead in leads]
    results = []
    for ref in ids:
        outcome, lead_uuid = await repo.campaign_enroll(str(campaign_id), ref, contactable, skip_nurture)
        results.append({"lead_id": lead_uuid or ref, "outcome": outcome})
    return {"enrolled": sum(r["outcome"] == "enrolled" for r in results), "results": results}


class CampaignPreview(BaseModel):
    lead_id: Optional[UUID] = None


@app.post("/campaigns/{campaign_id}/preview", dependencies=[Depends(require_user)])
async def campaign_preview(campaign_id: UUID, body: CampaignPreview | None = None) -> dict:
    """Write one sample sequence with the campaign's playbook, for a lead from
    its audience (or the one given). Nothing is saved or sent."""
    c = await _campaign(campaign_id)
    if body and body.lead_id:
        # A lead already in a cold sequence (its details include corrections).
        lead = await repo.lead_for_step(str(body.lead_id))
        if lead is None:
            raise HTTPException(status_code=404, detail="this lead is not in the cold sequence")
    else:
        # The first lead of the audience: not enrolled yet, so its shared details.
        leads, _ = await repo.campaign_audience(c.get("audience") or {}, await settings_store.contactable_statuses(), 1)
        if not leads:
            raise HTTPException(status_code=409, detail="no lead matches this audience to preview with")
        lead = leads[0]
    lead = {**lead, "id": "preview", "playbook": c.get("playbook") or {}}
    result = await generate_sequence(lead, step_count=len(await settings_store.step_gaps()))
    return {
        "lead": {k: lead.get(k) for k in ("first_name", "last_name", "email", "job_title", "company")},
        "persona": result.get("persona"),
        "status": result.get("status"),
        "validation_errors": result.get("validation_errors") or [],
        "emails": result.get("emails") or [],
    }


# Declared after /enroll and /preview, which it would otherwise catch.
@app.post("/campaigns/{campaign_id}/{action}", dependencies=[Depends(require_user)])
async def campaign_action(campaign_id: UUID, action: str) -> dict:
    """start | pause | resume | end. Ending stops the campaign's sequences
    (what is unsent is cancelled; sent emails and replies stay)."""
    if action not in CAMPAIGN_ACTIONS:
        raise HTTPException(status_code=404, detail=f"actions: {', '.join(CAMPAIGN_ACTIONS)}")
    await _campaign(campaign_id)
    stopped = await repo.campaign_set_status(str(campaign_id), CAMPAIGN_ACTIONS[action])
    if stopped < 0:
        raise HTTPException(status_code=409, detail="not possible now (an ended campaign stays ended; only a running one can be paused)")
    return {"campaign": await repo.campaign_get(str(campaign_id)), "stopped": stopped}


@app.get("/leads/{lead_id}/journey", dependencies=[Depends(require_user)])
async def lead_journey(lead_id: UUID) -> dict:
    """One lead's whole story: cold emails, replies and how they were read,
    our answers, meetings, Email Nurture emails, clicks and hand-off."""
    journey = await repo.lead_journey(str(lead_id))
    if not journey.get("lead") and not journey.get("items"):
        raise HTTPException(status_code=404, detail="no such lead")
    journey["campaign"] = await repo.campaign_for_lead(str(lead_id))
    # What Brevo saw: delivered, opened, clicked, bounced (newest first).
    journey["engagement"] = await repo.lead_engagement(str(lead_id))
    return journey


@app.put("/emails/{email_id}", dependencies=[Depends(require_user)])
async def edit_email(email_id: UUID, request: EmailEdit) -> dict:
    """Change an email that has not been sent yet. It goes out as edited."""
    if not await repo.edit_email(str(email_id), request.subject.strip(), request.body.strip()):
        raise HTTPException(status_code=409, detail="this email is no longer waiting to be sent")
    return {"ok": True}


@app.post("/run/intake", dependencies=[Depends(require_user)])
async def run_intake() -> dict:
    return {"result": await intake.run_once()}


@app.post("/run/sender", dependencies=[Depends(require_admin)])
async def run_sender() -> dict:
    return {"result": await sender.run_once()}


@app.post("/send-now", dependencies=[Depends(require_admin)])
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


@app.post("/replies/test-alert", dependencies=[Depends(require_admin)])
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


@app.put("/settings/{key}", dependencies=[Depends(require_admin)])
async def write_setting(key: str, update: SettingUpdate) -> dict:
    await settings_store.set_value(key, update.value, update.description)
    return {"key": key, "value": update.value}


@app.post("/suppress", dependencies=[Depends(require_admin)])
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


class BookRequest(BaseModel):
    start: datetime

    @field_validator("start")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("start needs a timezone, e.g. 2026-10-14T09:00:00+00:00")
        return v


class TimezoneRequest(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)


class Snippet(BaseModel):
    id: Optional[str] = Field(default=None, max_length=40)
    title: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)


class SnippetList(BaseModel):
    snippets: list[Snippet] = Field(max_length=50)


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


@app.get("/triage/events/{event_id}/times", dependencies=[Depends(require_user)])
async def triage_event_times(event_id: UUID) -> dict:
    """Times worth one click on this reply's card, and why none fit, if so."""
    try:
        return await triage_worker.times_for_event(str(event_id))
    except triage_worker.CardError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/triage/events/{event_id}/book")
async def triage_event_book(event_id: UUID, request: BookRequest, user: dict = Depends(require_user)) -> dict:
    """Book the time a person picked on the reply card; a confirmation for
    the lead is written and follows the usual send rules."""
    who = user.get("email") or user.get("id") or "someone"
    try:
        return await triage_worker.book_from_card(str(event_id), request.start, who)
    except triage_worker.CardError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.put("/leads/{lead_id}/timezone", dependencies=[Depends(require_user)])
async def set_lead_timezone(lead_id: UUID, request: TimezoneRequest) -> dict:
    """Correct an enrolled lead's timezone (send hours and meeting times)."""
    tz = request.timezone.strip()
    if not regions.is_valid_timezone(tz):
        raise HTTPException(status_code=422, detail="unknown timezone, e.g. Asia/Dhaka or America/New_York")
    if not await repo.set_lead_timezone(str(lead_id), tz):
        raise HTTPException(status_code=404, detail="this lead is not in the cold sequence")
    return {"lead_id": str(lead_id), "timezone": tz}


@app.get("/triage/snippets", dependencies=[Depends(require_user)])
async def triage_snippets() -> dict:
    """The answer library: approved wording the AI reuses."""
    return {"snippets": await settings_store.triage_snippets()}


@app.put("/triage/snippets")
async def save_triage_snippets(request: SnippetList, user: dict = Depends(require_user)) -> dict:
    import uuid as _uuid

    snippets = [{"id": s.id or _uuid.uuid4().hex[:12], "title": s.title.strip(), "text": s.text.strip()}
                for s in request.snippets]
    who = user.get("email") or "someone"
    await settings_store.set_value("triage_snippets", snippets, f"answer library, last changed by {who}")
    return {"snippets": snippets}


class RedraftRequest(RetriageRequest):
    # Optional steer for the AI: "shorter", "offer Thursday instead".
    instructions: str = Field(default="", max_length=800)


@app.post("/triage/events/{event_id}/handled", dependencies=[Depends(require_user)])
async def triage_mark_handled(event_id: UUID) -> dict:
    """A person dealt with this reply (e.g. outside the app): it stops
    showing as "Needs you"."""
    return {"event_id": str(event_id), "changed": await repo.mark_event_handled(event_id=str(event_id))}


@app.post("/triage/events/{event_id}/redraft", dependencies=[Depends(require_user)])
async def triage_redraft(event_id: UUID, request: RedraftRequest | None = None) -> dict:
    """Write a new answer to a reply (after a rejected one, or to replace the
    one waiting), keeping its category, with optional instructions. The new
    draft replaces any still waiting and follows the usual send rules."""
    request = request or RedraftRequest()
    if request.instructions.strip():
        triage_worker.set_guidance(str(event_id), request.instructions.strip())
    if not await repo.retriage_event(str(event_id), request.category):
        raise HTTPException(status_code=404, detail="no reply with that id can be answered again")
    result = await triage_worker.run_once()
    return {"event_id": str(event_id), "result": result}


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


@app.put("/triage/drafts/{draft_id}")
async def triage_edit_draft(draft_id: UUID, edit: DraftEdit, user: dict = Depends(require_user)) -> dict:
    # The AI's original is kept, so the AI can learn the team's style.
    who = user.get("email") or user.get("id") or "someone"
    if not await repo.edit_draft(str(draft_id), edit.subject.strip(), edit.body.strip(), who):
        raise HTTPException(status_code=409, detail="only a waiting or failed draft can be edited")
    return {"id": str(draft_id), "saved": True}


@app.post("/triage/drafts/{draft_id}/approve")
async def triage_approve_draft(draft_id: UUID, user: dict = Depends(require_user)) -> dict:
    """Send it at the next triage run (within a minute)."""
    if not await repo.decide_draft(str(draft_id), "approve", user.get("email") or user.get("id", "")):
        raise HTTPException(status_code=409, detail="this draft is no longer waiting")
    # A person answered it: the reply no longer needs anyone.
    await repo.mark_event_handled(draft_id=str(draft_id))
    return {"id": str(draft_id), "status": "approved"}


@app.post("/triage/drafts/{draft_id}/reject")
async def triage_reject_draft(draft_id: UUID, user: dict = Depends(require_user)) -> dict:
    if not await repo.decide_draft(str(draft_id), "reject", user.get("email") or user.get("id", "")):
        raise HTTPException(status_code=409, detail="this draft is no longer waiting")
    return {"id": str(draft_id), "status": "rejected"}


@app.get("/triage/meetings", dependencies=[Depends(require_user)])
async def triage_meetings(limit: int = 100) -> dict:
    """Booked meetings (from replies and through the link), plus the leads who
    were offered times and have not answered yet."""
    return {
        "meetings": await repo.meetings_overview(max(1, min(limit, 500))),
        "open_offers": await repo.open_offers(50),
        "sync": calendar_sync.last_run or None,
    }


@app.get("/triage/snoozed", dependencies=[Depends(require_user)])
async def triage_snoozed(limit: int = 100) -> dict:
    return {"snoozed": await repo.list_snoozed(max(1, min(limit, 500)))}


@app.post("/run/triage", dependencies=[Depends(require_user)])
async def run_triage() -> dict:
    return {"result": await triage_worker.run_once()}


@app.post("/run/reminders", dependencies=[Depends(require_user)])
async def run_reminders() -> dict:
    """Send due day-before meeting reminders now (normally every 15 minutes)."""
    return {"result": await reminders.run_once()}


@app.post("/run/engagement", dependencies=[Depends(require_user)])
async def run_engagement() -> dict:
    """Read Brevo's email events now (normally every 10 minutes)."""
    return {"result": await engagement.run_once()}


@app.post("/run/calendar-sync", dependencies=[Depends(require_user)])
async def run_calendar_sync() -> dict:
    """Read the calendar's bookings now (normally every few minutes)."""
    return {"result": await calendar_sync.run_once(), "sync": calendar_sync.last_run}


@app.post("/triage/evaluate", dependencies=[Depends(require_admin)])
async def triage_evaluate() -> dict:
    """Run the 50-reply test set through the classifier (takes a minute or two)."""
    from app.triage.evaluate import evaluate

    report = await evaluate()
    await settings_store.set_value("triage_last_eval", report, "Last accuracy check of the reply classifier.")
    return report


@app.get("/triage/evaluation", dependencies=[Depends(require_admin)])
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


@app.get("/calendar/microsoft/discover", dependencies=[Depends(require_admin)])
async def calendar_microsoft_discover() -> dict:
    """Setup help: every Bookings business the Graph app can see, with its
    services and staff, i.e. the values for MSBOOKINGS_BUSINESS_ID,
    MSBOOKINGS_SERVICE_ID and MSBOOKINGS_STAFF_IDS. Works before those are set."""
    from app.calendar.msbookings import discover

    s = get_settings()
    try:
        return {"businesses": await discover(s.graph_tenant_id, s.graph_client_id, s.graph_client_secret)}
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/calendar/microsoft/check", dependencies=[Depends(require_admin)])
async def calendar_microsoft_check() -> dict:
    """Setup check for CALENDAR_PROVIDER=microsoft: the service, the
    round-robin team and the booking page, as the service will use them."""
    try:
        cal = get_calendar()
    except CalendarError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if cal is None or cal.name != "microsoft":
        raise HTTPException(status_code=400, detail="CALENDAR_PROVIDER is not microsoft")
    try:
        return await cal.describe()
    except CalendarError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
