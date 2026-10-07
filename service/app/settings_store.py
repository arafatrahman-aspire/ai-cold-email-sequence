"""Reads operator-tunable settings out of the ``cold_email.settings`` table.

Values are cached briefly so a tight worker loop does not hit the database on
every iteration, while an operator's change still takes effect within a minute
without a redeploy.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app import regions, repository as repo

log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, Any] = {}
_cache_loaded_at: float = 0.0

DEFAULTS: dict[str, Any] = {
    "business_hours": {"start_hour": 9, "end_hour": 17, "weekdays": [0, 1, 2, 3, 4]},
    "business_hours_by_region": {},
    "step_gaps_business_days": [0, 3, 7, 12],
    "default_daily_cap": 15,
    "intake_batch_size": 10,
    "send_batch_size": 25,
    "max_send_attempts": 3,
    "sequence_enabled": True,
    "llm_max_body_chars": 2200,
    "llm_min_body_chars": 120,
    "contactable_lead_statuses": ["New", "No Answer", "Busy", "Call Later", "In Progress"],
    "skip_if_in_email_nurture": True,
    "auto_enroll": {"enabled": False, "sources": [], "batch_size": 50},
    "reply_alert_email": "",
    "triage": {
        "enabled": True,
        "auto_send_delay_minutes": 120,
        "auto_send_kinds": [
            "meeting_offer", "booking_confirmation", "slot_unavailable",
            "objection_reply", "not_now_ack", "referral_ack", "referral_ask",
        ],
        "min_confidence": 0.7,
        "not_now_days": 60,
        "ooo_default_days": 7,
        "context_messages": 4,
        "meeting": {"slots_to_offer": 2, "days_ahead": 7, "min_notice_hours": 12},
    },
    # Email Nurture: stored overrides only; defaults in app.nurture.options
    # and app.nurture.content.
    "nurture": {},
    "nurture_briefs": {},
    "nurture_fallbacks": {},
    "nurture_resources": [],
    # When lead scores were last checked (None until the first check).
    "nurture_scores_seen_at": None,
}


async def refresh() -> None:
    """Reload the cache from the database.

    A failure here is not fatal: callers fall back to the last known values, or
    to DEFAULTS. A transient database blip should slow the system down, not
    crash a worker tick.
    """
    global _cache, _cache_loaded_at
    try:
        rows = await repo.get_settings()
    except Exception as exc:
        log.warning("could not refresh cold_email.settings (%s); using cached/default values", exc)
        # Push the next attempt out a little so a hard outage does not turn
        # every settings read into a failed query.
        _cache_loaded_at = time.monotonic() - _CACHE_TTL_SECONDS + 10.0
        return
    _cache = {r["key"]: r["value"] for r in rows}
    _cache_loaded_at = time.monotonic()


async def get(key: str) -> Any:
    if time.monotonic() - _cache_loaded_at > _CACHE_TTL_SECONDS:
        await refresh()
    if key in _cache:
        return _cache[key]
    if key in DEFAULTS:
        return DEFAULTS[key]
    raise KeyError(f"unknown setting: {key}")


async def set_value(key: str, value: Any, description: str | None = None) -> None:
    await repo.set_setting(key, value, description)
    await refresh()


@dataclass(frozen=True)
class BusinessHours:
    start_hour: int
    end_hour: int
    weekdays: tuple[int, ...]  # 0 = Monday


def hours_for_timezone(
    default: dict[str, Any], by_region: dict[str, Any], timezone_name: str | None
) -> BusinessHours:
    """The business-hours window for a lead in ``timezone_name``.

    A regional entry (timezone key first, then the zone's country code) only
    replaces the fields it sets; everything else comes from ``default``.
    """
    raw = dict(default or {})
    by_region = by_region or {}
    country = regions.country_for_timezone(timezone_name)
    override = by_region.get(timezone_name or "") or by_region.get(country or "")
    if isinstance(override, dict):
        raw.update(override)
    return BusinessHours(
        start_hour=int(raw.get("start_hour", 9)),
        end_hour=int(raw.get("end_hour", 17)),
        weekdays=tuple(int(d) for d in raw.get("weekdays", [0, 1, 2, 3, 4])),
    )


async def business_hours(timezone_name: str | None = None) -> BusinessHours:
    """Business hours for a lead's timezone (the default window if None)."""
    return hours_for_timezone(
        await get("business_hours"),
        await get("business_hours_by_region"),
        timezone_name,
    )


async def step_gaps() -> list[int]:
    return [int(g) for g in await get("step_gaps_business_days")]


async def contactable_statuses() -> list[str]:
    return [str(x) for x in await get("contactable_lead_statuses")]


async def triage_config() -> dict[str, Any]:
    """The triage setting merged over its defaults (one level deep for 'meeting')."""
    default = DEFAULTS["triage"]
    stored = await get("triage")
    stored = stored if isinstance(stored, dict) else {}
    merged = {**default, **stored}
    merged["meeting"] = {**default["meeting"], **(stored.get("meeting") or {})}
    return merged
