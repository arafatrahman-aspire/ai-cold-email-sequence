"""Intake worker: ready_for_outreach -> generated sequence -> sequence_ready.

Leads come from the shared ``public.leads`` table but only enter outreach once
enrolled in ``cold_email.enrollments`` (via POST /enroll, or auto-enroll when
it is switched on in settings).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import repository as repo
from app import settings_store
from app.graph.build import generate_sequence
from app.scheduling import build_schedule

log = logging.getLogger(__name__)


async def process_lead(lead: dict) -> str:
    """Generate and persist one lead's sequence. Returns the final lead status."""
    lead_id = str(lead["id"])

    if not lead.get("lead_exists"):
        await repo.mark_lead(lead_id, "failed", "lead no longer exists in public.leads")
        return "failed"
    if not lead.get("email"):
        await repo.mark_lead(lead_id, "failed", "lead has no email address")
        return "failed"
    if lead.get("lead_status") not in await settings_store.contactable_statuses():
        await repo.mark_lead(
            lead_id, "stopped", f"public.leads status is {lead.get('lead_status')}"
        )
        return "stopped"

    if await repo.is_suppressed(lead["email"]):
        log.info("lead %s is suppressed; skipping intake", lead_id)
        await repo.mark_lead(lead_id, "unsubscribed", "address on suppression list")
        return "unsubscribed"

    gaps = await settings_store.step_gaps()
    hours = await settings_store.business_hours(lead.get("timezone"))

    try:
        result = await generate_sequence(lead, step_count=len(gaps))
    except Exception as exc:
        log.exception("sequence generation crashed for lead %s", lead_id)
        await repo.mark_lead(lead_id, "failed", f"generation error: {exc}"[:1000])
        return "failed"

    status = result.get("status")
    persona = result.get("persona") or "ciso"

    if status != "ok":
        reason = result.get("error") or "validation failed"
        log.warning("lead %s needs manual review: %s", lead_id, reason)
        await repo.set_lead_persona(lead_id, persona)
        await repo.mark_lead(lead_id, "manual_review", str(reason)[:1000])
        return "manual_review"

    emails = result["emails"]
    due_dates = build_schedule(
        datetime.now(timezone.utc), lead.get("timezone"), gaps, hours
    )

    await repo.store_sequence(
        lead_id=lead_id,
        persona=persona,
        routing_mode=result.get("routing_mode", "keyword"),
        provider=result.get("provider"),
        model=result.get("model"),
        emails=emails,
        due_dates=due_dates,
    )
    log.info(
        "lead %s: %d-step %s sequence ready, timezone %s (from %s), first send %s",
        lead_id, len(emails), persona, lead.get("timezone"),
        lead.get("timezone_source"), due_dates[0].isoformat(),
    )
    return "sequence_ready"


async def _auto_enroll() -> int:
    cfg = await settings_store.get("auto_enroll") or {}
    if not cfg.get("enabled"):
        return 0
    added = await repo.auto_enroll(
        sources=[str(x) for x in cfg.get("sources") or []],
        contactable_statuses=await settings_store.contactable_statuses(),
        skip_if_in_nurture=bool(await settings_store.get("skip_if_in_email_nurture")),
        limit=int(cfg.get("batch_size", 50)),
    )
    if added:
        log.info("auto-enrolled %d lead(s)", added)
    return added


async def run_once() -> dict[str, int]:
    """One intake tick. Returns a count of leads by resulting status."""
    counts: dict[str, int] = {}
    enrolled = await _auto_enroll()
    if enrolled:
        counts["auto_enrolled"] = enrolled

    batch_size = int(await settings_store.get("intake_batch_size"))
    rows = await repo.claim_leads_for_intake(batch_size)
    if not rows:
        return counts

    log.info("intake claimed %d lead(s)", len(rows))
    try:
        referrals = await repo.referral_names([str(r["id"]) for r in rows])
    except Exception as exc:  # before 0006_triage.sql there is no such lookup
        log.warning("referral lookup unavailable (%s)", exc)
        referrals = {}
    for row in rows:
        lead = dict(row)
        lead["id"] = str(lead["id"])
        if lead["id"] in referrals:
            lead["referred_by"] = referrals[lead["id"]]
        try:
            status = await process_lead(lead)
        except Exception as exc:
            log.exception("unhandled intake error for lead %s", lead["id"])
            await repo.mark_lead(lead["id"], "failed", str(exc)[:1000])
            status = "failed"
        counts[status] = counts.get(status, 0) + 1
    return counts
