"""Enrollment: lead scores -> nurture.

There are no triggers on the shared tables, so every few minutes:
  * everyone in nurture is checked against their current score: Hot ->
    hand off to sales; Warm <-> Cold -> switch track from the next email
  * leads rescored since the last check (lead_scores.updated_at / scored_at)
    that are now Warm or Cold are enrolled, if they may join
The very first check only remembers the time: leads that were already Warm
or Cold are enrolled from the console ("Enroll eligible"), not automatically.
The hourly reconciliation looks back 48 hours, so a failed run is caught.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app import settings_store
from app.nurture import cadence, options, persona
from app.nurture import repo as nrepo
from app.nurture.exits import handoff

log = logging.getLogger(__name__)

TEMPERATURE = {"Warm": "warm", "Cold": "cold"}
SEEN_KEY = "nurture_scores_seen_at"
# What a run reports: actions taken. (Leads that may not join are skipped
# quietly; the overlap between runs would count them more than once.)
REPORTED = ("enrolled", "handoff", "switched", "failed")


async def enroll_lead(lead_id: str, tier: Optional[str], source: str, cfg: dict[str, Any],
                      now: Optional[datetime] = None) -> str:
    """Enroll one lead on the track for its tier. Returns 'enrolled' or why not."""
    now = now or datetime.now(timezone.utc)
    temperature = TEMPERATURE.get(tier or "")
    if temperature is None:
        return f"not_warm_or_cold ({tier})"
    blocked = list(cfg["blocked_lead_statuses"])
    skip = bool(cfg["skip_if_in_email_nurture"])
    reason = await nrepo.why_not(lead_id, int(cfg["cooldown_days"]), blocked, skip)
    if reason:
        return reason
    lead = await nrepo.lead(lead_id) or {}
    test = options.test_minutes(cfg)
    if test and (lead.get("email") or "").lower() not in options.allow_list(cfg):
        return "test_mode_not_allowed"

    choice = await persona.assign(lead.get("job_title"), float(cfg["persona_min_confidence"]))
    tz = cadence.lead_timezone(lead.get("timezone"), cfg["default_timezone"])
    start = cadence.day_zero(now, tz, options.window(cfg), test)
    result = await nrepo.enroll(
        lead_id, choice.persona, temperature, start, choice.needs_review,
        choice.reason if choice.needs_review else None, bool(test),
        {"persona_source": choice.source, "confidence": choice.confidence,
         "reason": choice.reason, "source": source},
        int(cfg["cooldown_days"]), blocked, skip,
    )
    outcome = result.get("outcome", "unknown")
    if outcome == "enrolled":
        log.info("nurture: enrolled lead %s as %s/%s (%s)", lead_id, choice.persona, temperature, choice.source)
    return outcome


async def check_live(cfg: dict[str, Any], now: datetime) -> dict[str, int]:
    """Everyone in nurture against their current score."""
    stats: dict[str, int] = {}
    for row in await nrepo.live():
        eid, tier = str(row["id"]), row.get("tier")
        try:
            if tier == "Hot":
                if await handoff(eid, "hot_score"):
                    stats["handoff"] = stats.get("handoff", 0) + 1
            elif TEMPERATURE.get(tier or "") not in (None, row["temperature"]):
                temperature = TEMPERATURE[tier]
                test = options.test_minutes(cfg) if row.get("test_mode") else None
                tz = cadence.lead_timezone(row.get("timezone"), cfg["default_timezone"])
                next_at = cadence.next_send(_as_dt(row["started_at"]), cfg["cadence"][temperature],
                                            int(row["step"]), tz, options.window(cfg), test, not_before=now)
                if await nrepo.update(eid, temperature=temperature, next_send_at=next_at):
                    await nrepo.log(eid, "track", {"from": row["temperature"], "to": temperature})
                    stats["switched"] = stats.get("switched", 0) + 1
        except Exception:
            log.exception("nurture: checking enrollment %s failed", eid)
            stats["failed"] = stats.get("failed", 0) + 1
    return stats


async def _enroll_changes(since: datetime, cfg: dict[str, Any], now: datetime, source: str,
                          stats: dict[str, int]) -> None:
    if not cfg["auto_enroll"] or cfg["paused"]:
        return
    live = {str(r["lead_id"]) for r in await nrepo.live()}
    for row in await nrepo.score_changes(since):
        lead_id = str(row["lead_id"])
        if lead_id in live or row.get("tier") not in TEMPERATURE:
            continue
        try:
            outcome = await enroll_lead(lead_id, row["tier"], source, cfg, now)
        except Exception:
            log.exception("nurture: enrolling lead %s failed", lead_id)
            outcome = "failed"
        stats[outcome] = stats.get(outcome, 0) + 1


async def score_tick(now: Optional[datetime] = None) -> dict[str, int]:
    """Every few minutes: live enrollments, then leads rescored since last time."""
    now = now or datetime.now(timezone.utc)
    checked_at = datetime.now(timezone.utc)
    cfg = await options.load(fresh=True)
    seen = await settings_store.get(SEEN_KEY)
    await settings_store.set_value(SEEN_KEY, checked_at.isoformat(), "When Email Nurture last checked lead scores.")
    if not seen:
        return {}  # first run: today's scores are the starting point
    stats = await check_live(cfg, now)
    # A few minutes of overlap: enrolling twice is impossible, missing one is not.
    await _enroll_changes(_as_dt(seen) - timedelta(minutes=5), cfg, now, "score", stats)
    stats = {k: v for k, v in stats.items() if k in REPORTED}
    if stats:
        log.info("nurture score tick: %s", stats)
    return stats


async def reconcile_tick(now: Optional[datetime] = None, lookback_hours: int = 48) -> dict[str, int]:
    """Hourly: the same checks, looking back 48 hours."""
    now = now or datetime.now(timezone.utc)
    cfg = await options.load(fresh=True)
    stats = await check_live(cfg, now)
    await _enroll_changes(datetime.now(timezone.utc) - timedelta(hours=lookback_hours), cfg, now, "reconcile", stats)
    stats = {k: v for k, v in stats.items() if k in REPORTED}
    if stats:
        log.info("nurture reconcile: %s", stats)
    return stats


async def enroll_eligible(lead_ids: Optional[list[str]] = None, limit: int = 500,
                          now: Optional[datetime] = None) -> dict[str, Any]:
    """From the console: the given leads, or every Warm/Cold lead that may join."""
    cfg = await options.load()
    if lead_ids:
        rows = [{"lead_id": i, "tier": (await nrepo.lead(i) or {}).get("tier")} for i in lead_ids[:limit]]
    else:
        rows = await nrepo.candidates(int(cfg["cooldown_days"]), list(cfg["blocked_lead_statuses"]),
                                      bool(cfg["skip_if_in_email_nurture"]), limit)
    results = []
    for row in rows:
        lead_id = str(row["lead_id"])
        try:
            outcome = await enroll_lead(lead_id, row.get("tier"), "manual", cfg, now)
        except Exception as exc:
            log.exception("nurture: manual enroll of %s failed", lead_id)
            outcome = f"error: {exc}"
        results.append({"lead_id": lead_id, "outcome": outcome})
    return {"enrolled": sum(r["outcome"] == "enrolled" for r in results), "results": results}


def _as_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
