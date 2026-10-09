"""Email Nurture HTTP endpoints.

Public (no login; reached from emails, through NURTURE_PUBLIC_URL):
  GET  /n/c/{token}   tracked link: log the click, redirect
  GET  /n/u/{token}   unsubscribe page (a button, so link scanners do not
                      unsubscribe people by opening the link)
  POST /n/u/{token}   unsubscribe (also RFC 8058 one-click from mail clients)

Console (CMS login, like every other console endpoint): /nurture/*.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import repository as repo
from app.config import get_settings
from app.llm.factory import get_gateway
from app.nurture import checks, content, enroll, exits, jobs, options, replies, sending
from app.nurture import repo as nrepo
from app.nurture.options import PERSONAS, STEPS, TEMPERATURES
from app.nurture.safety import DATA_RULE, clean, data_block

log = logging.getLogger(__name__)

STAGES = ("awareness", "consideration", "decision")
KINDS = ("guide", "case_study", "checklist", "report", "webinar", "blog", "roi")
LINK_TRIGGERS = {"demo": "demo_click", "pricing": "pricing_click"}


# --- public pages ----------------------------------------------------------------------

def _page(title: str, message: str, form: str = "") -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{html.escape(title)}</title>
<style>body{{font-family:Arial,Helvetica,sans-serif;background:#f5f6f8;color:#1f2329;margin:0;padding:48px 16px}}
main{{max-width:460px;margin:0 auto;background:#fff;border-radius:10px;padding:28px}}
h1{{font-size:20px;margin:0 0 12px}}p{{line-height:1.5}}button{{font:inherit;padding:10px 18px;border:0;
border-radius:8px;background:#1f2329;color:#fff;cursor:pointer}}</style></head>
<body><main><h1>{html.escape(title)}</h1><p>{message}</p>{form}</main></body></html>""")


def _uuid(value: str) -> Optional[str]:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


async def handle_click(message_id: str, link: str, user_agent: str) -> Optional[str]:
    """Log a click and return where to send the reader (None: unknown link).
    A person clicking the pricing or demo link is handed to sales."""
    if not _uuid(message_id) or link not in ("demo", "pricing", "resource"):
        return None
    cfg = await options.load()
    info = await nrepo.click(message_id, link, user_agent, int(cfg["bot_click_seconds"]))
    if not info:
        return None
    trigger = LINK_TRIGGERS.get(link)
    if trigger and not info.get("bot") and info.get("status") in ("active", "paused", "held"):
        try:
            await exits.handoff(str(info["enrollment_id"]), trigger)
        except Exception:
            # The redirect matters more than the hand-off's timing.
            log.exception("nurture: hand-off on click of %s failed", message_id)
    if link == "resource":
        r = await content.resource(info.get("resource_id"))
        return (r or {}).get("url") or None
    branding = cfg["branding"]
    if link == "demo":
        return branding.get("demo_url") or get_settings().calcom_booking_url or None
    return branding.get("pricing_url") or None


async def _unsubscribe_target(enrollment_id: str) -> Optional[dict[str, Any]]:
    if not _uuid(enrollment_id):
        return None
    detail = await nrepo.detail(enrollment_id)
    return (detail or {}).get("enrollment")


def public_router() -> APIRouter:
    router = APIRouter(tags=["nurture-public"])

    @router.get("/n/c/{message_id}/{link}", include_in_schema=False)
    async def click(message_id: str, link: str, request: Request):
        url = await handle_click(message_id, link, request.headers.get("user-agent", ""))
        if not url:
            return _page("Link not found", "This link is no longer valid.")
        return RedirectResponse(url, status_code=302)

    @router.get("/n/u/{enrollment_id}", include_in_schema=False)
    async def unsubscribe_page(enrollment_id: str):
        e = await _unsubscribe_target(enrollment_id)
        if not e:
            return _page("Link not found", "This unsubscribe link is not valid.")
        if await repo.is_suppressed(e["email"]):
            return _page("You're unsubscribed", "You won't receive any more emails from us.")
        return _page(
            "Unsubscribe",
            f"Stop all emails to <strong>{html.escape(e['email'])}</strong>?",
            '<form method="post"><button type="submit">Unsubscribe</button></form>',
        )

    @router.post("/n/u/{enrollment_id}", include_in_schema=False)
    async def unsubscribe(enrollment_id: str, request: Request):
        e = await _unsubscribe_target(enrollment_id)
        if not e:
            return _page("Link not found", "This unsubscribe link is not valid.")
        one_click = "one-click" in (await request.body()).decode("utf-8", "ignore").lower()
        await nrepo.suppress(str(e["id"]), "unsubscribed")
        await nrepo.log(str(e["id"]), "unsubscribed", {"via": "one_click" if one_click else "page"})
        log.info("nurture: %s unsubscribed (%s)", e["email"], "one-click" if one_click else "page")
        return _page("You're unsubscribed", "You won't receive any more emails from us. Sorry to see you go.")

    return router


# --- console -------------------------------------------------------------------------------

class EnrollRequest(BaseModel):
    lead_ids: list[UUID] = Field(default_factory=list, max_length=500)
    all_eligible: bool = False


class PersonaRequest(BaseModel):
    persona: str


class MessageEdit(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    preheader: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=5000)


class BriefEdit(BaseModel):
    persona: str
    temperature: str
    step: int = Field(ge=1, le=STEPS)
    goal: str = Field(min_length=3, max_length=1000)
    angle: str = Field(min_length=3, max_length=1000)
    allowed_stages: list[str] = Field(default_factory=list)
    cta_emphasis: str = "soft"
    max_words: int = Field(ge=40, le=400)


class FallbackEdit(BaseModel):
    persona: str
    step: int = Field(ge=1, le=STEPS)
    subject: str = Field(min_length=1, max_length=200)
    preheader: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=5000)


class ResourceEdit(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=8, max_length=1000)
    description: str = Field(default="", max_length=2000)
    kind: str = "guide"
    personas: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    approved: bool = False


def _resource_fields(body: "ResourceEdit") -> dict[str, Any]:
    return {"title": body.title.strip(), "url": body.url.strip(), "description": body.description.strip(),
            "kind": body.kind, "personas": body.personas, "stages": body.stages, "approved": body.approved}


def _who(user: dict) -> str:
    return user.get("email") or user.get("id") or "someone"


def _check_values(values: list[str], allowed: tuple[str, ...], what: str) -> None:
    bad = sorted(set(values) - set(allowed))
    if bad:
        raise HTTPException(status_code=422, detail=f"unknown {what}: {', '.join(bad)}")


def _cost(ai: dict[str, Any], prices: dict[str, Any]) -> float:
    total = 0.0
    for model, t in (ai.get("by_model") or {}).items():
        name = str(model).split(":", 1)[-1]
        p = prices.get(name) or prices.get(model) or prices.get("default") or {}
        total += (float(t.get("in") or 0) * float(p.get("in") or 0)
                  + float(t.get("out") or 0) * float(p.get("out") or 0)) / 1_000_000
    return round(total, 4)


def console_router(require_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/nurture", tags=["nurture"], dependencies=[Depends(require_user)])

    # Status, dashboard, settings -------------------------------------------------
    @router.get("/status")
    async def status() -> dict:
        cfg = await options.load()
        box = await sending.sending_inbox()
        return {
            "paused": cfg["paused"],
            "environment": get_settings().app_env,
            "test_mode": cfg["test_mode"],
            "problems": await sending.problems(cfg),
            "warnings": await sending.warnings(),
            "from_email": box.email if box else None,
            "reply_to": await sending.reply_to(),
            "public_url": sending.public_url(),
        }

    @router.get("/stats")
    async def stats(start: Optional[datetime] = None, end: Optional[datetime] = None) -> dict:
        end = end or datetime.now(timezone.utc) + timedelta(days=1)
        start = start or end - timedelta(days=91)
        data = await nrepo.stats(start, end)
        cfg = await options.load()
        ai = data.get("ai") or {}
        written = int(ai.get("written") or 0)
        data["ai"] = {**ai,
                      "fallback_rate": (int(ai.get("fallbacks") or 0) / written) if written else None,
                      "regeneration_rate": (int(ai.get("regenerated") or 0) / written) if written else None,
                      "cost_usd": _cost(ai, cfg["token_prices"])}
        data["range"] = {"start": start.isoformat(), "end": end.isoformat()}
        return data

    @router.get("/settings")
    async def get_settings_() -> dict:
        return {"settings": await options.load(fresh=True), "defaults": options.DEFAULTS}

    @router.put("/settings")
    async def put_settings(changes: dict[str, Any]) -> dict:
        unknown = sorted(set(changes) - set(options.DEFAULTS))
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown settings: {', '.join(unknown)}")
        merged = options.merge({**(await options.load()), **changes})
        cad = merged["cadence"]
        for temp in TEMPERATURES:
            days = cad.get(temp)
            if (not isinstance(days, list) or len(days) != STEPS
                    or any(not isinstance(d, (int, float)) or d < 0 for d in days) or days != sorted(days)):
                raise HTTPException(status_code=422, detail=f"cadence.{temp} must be {STEPS} rising day numbers")
        if merged["approval"].get("mode") not in ("none", "all", "sample"):
            raise HTTPException(status_code=422, detail="approval.mode must be none, all or sample")
        w = merged["sending_window"]
        if not (0 <= int(w["start_hour"]) < int(w["end_hour"]) <= 24) or not w["weekdays"]:
            raise HTTPException(status_code=422, detail="sending window: start < end, at least one weekday")
        return {"settings": await options.save(changes)}

    # Enrollments -------------------------------------------------------------------
    @router.get("/enrollments")
    async def enrollments(search: Optional[str] = None, status: Optional[str] = None,
                          persona: Optional[str] = None, temperature: Optional[str] = None,
                          review: Optional[bool] = None, limit: int = 50, offset: int = 0) -> dict:
        rows, total = await nrepo.list_enrollments((search or "").strip()[:100] or None, status, persona,
                                                   temperature, review, max(1, min(limit, 200)), max(0, offset))
        return {"enrollments": rows, "total": total}

    @router.get("/enrollments/{enrollment_id}")
    async def detail(enrollment_id: UUID) -> dict:
        data = await nrepo.detail(str(enrollment_id))
        if not data:
            raise HTTPException(status_code=404, detail="no such enrollment")
        return data

    async def _expect(ok: bool, what: str) -> dict:
        if not ok:
            raise HTTPException(status_code=409, detail=f"could not {what}: the enrollment has ended or changed")
        return {"ok": True}

    @router.post("/enrollments/{enrollment_id}/pause")
    async def pause(enrollment_id: UUID, user: dict = Depends(require_user)) -> dict:
        return await _expect(await nrepo.set_status(str(enrollment_id), "paused", f"paused by {_who(user)}"), "pause")

    @router.post("/enrollments/{enrollment_id}/resume")
    async def resume(enrollment_id: UUID, user: dict = Depends(require_user)) -> dict:
        ok = await nrepo.set_status(str(enrollment_id), "active", f"resumed by {_who(user)}")
        if ok:
            await nrepo.update(str(enrollment_id), needs_review=False)
        return await _expect(ok, "resume")

    @router.post("/enrollments/{enrollment_id}/remove")
    async def remove(enrollment_id: UUID, user: dict = Depends(require_user)) -> dict:
        return await _expect(await nrepo.set_status(str(enrollment_id), "exited", f"removed by {_who(user)}"), "remove")

    @router.post("/enrollments/{enrollment_id}/handoff")
    async def handoff_now(enrollment_id: UUID, user: dict = Depends(require_user)) -> dict:
        return await _expect(await exits.handoff(str(enrollment_id), "manual"), "hand off")

    @router.post("/enrollments/{enrollment_id}/persona")
    async def persona(enrollment_id: UUID, body: PersonaRequest, user: dict = Depends(require_user)) -> dict:
        _check_values([body.persona], tuple(PERSONAS), "persona")
        ok = await nrepo.update(str(enrollment_id), persona=body.persona, needs_review=False)
        if ok:
            await nrepo.log(str(enrollment_id), "persona", {"to": body.persona, "by": _who(user)})
        return await _expect(ok, "change persona")

    @router.post("/enrollments/{enrollment_id}/reviewed")
    async def reviewed(enrollment_id: UUID, user: dict = Depends(require_user)) -> dict:
        return await _expect(await nrepo.update(str(enrollment_id), needs_review=False), "clear the flag")

    @router.get("/leads")
    async def leads(search: Optional[str] = None, view: str = "all", limit: int = 100, offset: int = 0) -> dict:
        """Every shared lead with its nurture state, and each view's size."""
        if view not in nrepo.LEAD_VIEWS:
            raise HTTPException(status_code=422, detail=f"view must be one of: {', '.join(nrepo.LEAD_VIEWS)}")
        cfg = await options.load()
        args = ((search or "").strip()[:100] or None, int(cfg["cooldown_days"]),
                list(cfg["blocked_lead_statuses"]), bool(cfg["skip_if_in_email_nurture"]))

        async def page(v: str, n: int, skip: int):
            return await nrepo.browse(args[0], v, *args[1:], n, skip)

        (rows, total), *others = await asyncio.gather(
            page(view, max(1, min(limit, 200)), max(0, offset)), *(page(v, 1, 0) for v in nrepo.LEAD_VIEWS))
        return {"leads": rows, "total": total,
                "counts": {v: t for v, (_, t) in zip(nrepo.LEAD_VIEWS, others)}}

    @router.get("/eligible")
    async def eligible() -> dict:
        """Warm and Cold leads that may join now (their ids, for "select all")."""
        cfg = await options.load()
        rows = await nrepo.candidates(int(cfg["cooldown_days"]), list(cfg["blocked_lead_statuses"]),
                                      bool(cfg["skip_if_in_email_nurture"]), 2000)
        tiers: dict[str, int] = {}
        for r in rows:
            tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
        return {"count": len(rows), "by_tier": tiers, "lead_ids": [str(r["lead_id"]) for r in rows]}

    @router.post("/enroll")
    async def enroll_now(body: EnrollRequest) -> dict:
        if not body.lead_ids and not body.all_eligible:
            raise HTTPException(status_code=422, detail="give lead_ids or all_eligible: true")
        return await enroll.enroll_eligible([str(i) for i in body.lead_ids] or None)

    # Review queue ----------------------------------------------------------------------
    @router.get("/review")
    async def review(hours_ahead: int = 72) -> dict:
        return {"messages": await nrepo.review(max(1, min(hours_ahead, 24 * 14)))}

    @router.put("/messages/{message_id}")
    async def edit_message(message_id: UUID, body: MessageEdit, user: dict = Depends(require_user)) -> dict:
        cfg = await options.load()
        draft = checks.Draft(body.subject, body.preheader, body.body, None, "demo")
        # A person's edit keeps the link placeholders and the safety rules;
        # the resource (if any) stays as written.
        problems = [p for p in checks.check(draft, allowed_resources=set(), max_words=400,
                                            banned_phrases=list(cfg["banned_phrases"]), min_words=10)
                    if "RESOURCE_LINK" not in p and "resource" not in p]
        if problems:
            raise HTTPException(status_code=422, detail="; ".join(problems))
        ok = await nrepo.review_message(str(message_id), "edit", body.subject, body.preheader, body.body)
        if not ok:
            raise HTTPException(status_code=409, detail="this email can no longer be edited")
        return {"ok": True}

    @router.post("/messages/{message_id}/{decision}")
    async def decide(message_id: UUID, decision: str, user: dict = Depends(require_user)) -> dict:
        if decision == "send-now":
            # Testing only: production sends on schedule.
            if get_settings().app_env != "dev":
                raise HTTPException(status_code=403, detail="Send now is only available when APP_ENV=dev")
            outcome = await jobs.send_now(str(message_id))
            if outcome == "not_waiting":
                raise HTTPException(status_code=409, detail="this email is not waiting to be sent")
            if outcome != "sent":
                raise HTTPException(status_code=409, detail=f"not sent: {outcome}")
            return {"ok": True, "outcome": outcome}
        if decision not in ("approve", "reject"):
            raise HTTPException(status_code=404, detail="unknown action")
        if not await nrepo.review_message(str(message_id), decision):
            raise HTTPException(status_code=409, detail="this email is no longer waiting")
        return {"ok": True}

    @router.get("/handoffs")
    async def handoffs(limit: int = 100) -> dict:
        return {"handoffs": await nrepo.handoff_list(max(1, min(limit, 500)))}

    # Content -------------------------------------------------------------------------
    @router.get("/content/briefs")
    async def briefs() -> dict:
        return {"briefs": await content.all_briefs()}

    @router.put("/content/briefs")
    async def save_brief(body: BriefEdit, user: dict = Depends(require_user)) -> dict:
        _check_values([body.persona], tuple(PERSONAS), "persona")
        _check_values([body.temperature], TEMPERATURES, "temperature")
        _check_values(body.allowed_stages, STAGES, "stage")
        _check_values([body.cta_emphasis], ("soft", "demo", "pricing"), "CTA emphasis")
        version = await content.save_brief(body.persona, body.temperature, body.step, {
            "goal": body.goal.strip(), "angle": body.angle.strip(), "allowed_stages": body.allowed_stages,
            "cta_emphasis": body.cta_emphasis, "max_words": body.max_words}, _who(user))
        return {"version": version}

    @router.get("/content/fallbacks")
    async def fallbacks() -> dict:
        return {"fallbacks": await content.all_fallbacks()}

    @router.put("/content/fallbacks")
    async def save_fallback(body: FallbackEdit, user: dict = Depends(require_user)) -> dict:
        _check_values([body.persona], tuple(PERSONAS), "persona")
        cfg = await options.load()
        draft = checks.Draft(body.subject, body.preheader, body.body, None, "demo")
        problems = checks.check(draft, allowed_resources=set(), max_words=250,
                                banned_phrases=list(cfg["banned_phrases"]), min_words=15)
        if problems:
            raise HTTPException(status_code=422, detail="; ".join(problems))
        version = await content.save_fallback(body.persona, body.step, {
            "subject": body.subject.strip(), "preheader": body.preheader.strip(), "body": body.body.strip()},
            _who(user))
        return {"version": version}

    @router.get("/content/resources")
    async def resources() -> dict:
        return {"resources": await content.resources()}

    def _resource_checks(body: ResourceEdit) -> None:
        _check_values(body.personas, tuple(PERSONAS), "persona")
        _check_values(body.stages, STAGES, "stage")
        _check_values([body.kind], KINDS, "kind")
        if not body.url.startswith(("https://", "http://")):
            raise HTTPException(status_code=422, detail="url must start with https://")

    @router.post("/content/resources")
    async def add_resource(body: ResourceEdit) -> dict:
        _resource_checks(body)
        rid = await content.save_resource(None, _resource_fields(body))
        return {"id": rid}

    @router.put("/content/resources/{resource_id}")
    async def edit_resource(resource_id: str, body: ResourceEdit) -> dict:
        _resource_checks(body)
        rid = await content.save_resource(resource_id, _resource_fields(body))
        if not rid:
            raise HTTPException(status_code=404, detail="no such resource")
        return {"id": rid}

    @router.delete("/content/resources/{resource_id}")
    async def delete_resource(resource_id: str) -> dict:
        if not await content.delete_resource(resource_id):
            raise HTTPException(status_code=404, detail="no such resource")
        return {"ok": True}

    @router.post("/content/resources/auto-tag")
    async def auto_tag() -> dict:
        """Ask the AI to suggest persona and stage tags. Suggestions wait for
        a person to accept them; nothing is offered to the writer until then."""
        suggested = 0
        errors = []
        for r in await content.resources():
            if r.get("approved"):
                continue
            try:
                suggestion = await suggest_tags(r)
                await content.suggest_tags(r["id"], suggestion)
                suggested += 1
            except Exception as exc:
                errors.append(f"{r.get('title')}: {exc}")
        return {"suggested": suggested, "errors": errors}

    # Running the jobs by hand ------------------------------------------------------
    @router.post("/run/{job}")
    async def run(job: str) -> dict:
        runners = {"score": enroll.score_tick, "reconcile": enroll.reconcile_tick,
                   "generate": jobs.generate_tick, "send": jobs.send_tick, "poll": replies.poll_tick}
        if job not in runners:
            raise HTTPException(status_code=404, detail=f"jobs: {', '.join(runners)}")
        return {"result": await runners[job]()}

    return router


TAG_SYSTEM = f"""\
You tag a marketing resource of a security awareness training company for an
email nurture sequence.
- personas: which readers it suits, any of "ciso" (security and risk
  leaders), "it" (IT managers), "hr" (HR, people and compliance)
- stages: any of "awareness" (educational), "consideration" (how to solve
  it, comparisons, case studies), "decision" (ROI, pricing, implementation)
- kind: one of {", ".join(KINDS)}
{DATA_RULE}
Return ONLY: {{"personas": [...], "stages": [...], "kind": "...", "reason": "a few words"}}
"""


async def suggest_tags(resource: dict[str, Any]) -> dict[str, Any]:
    completion = await get_gateway().complete_json(TAG_SYSTEM, data_block("resources", {
        "title": clean(resource.get("title"), 200), "description": clean(resource.get("description"), 800),
        "url_path": clean(resource.get("url"), 200),
    }), temperature=0.0)
    data = json.loads(completion.text)
    return {
        "personas": [p for p in data.get("personas") or [] if p in PERSONAS],
        "stages": [s for s in data.get("stages") or [] if s in STAGES],
        "kind": data.get("kind") if data.get("kind") in KINDS else resource.get("kind"),
        "reason": clean(data.get("reason"), 200),
    }
