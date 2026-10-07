"""Nurture settings: cold_email.settings key "nurture" over these defaults.

Editable in the console (Email Nurture -> Settings). Nested objects are
merged one level deep, so saving one field never drops the others.
"""

from __future__ import annotations

from typing import Any

from app import settings_store
from app.settings_store import BusinessHours

PERSONAS = {"ciso": "CISO", "it": "IT Manager", "hr": "HR & Compliance"}
TEMPERATURES = ("warm", "cold")
STEPS = 6

DEFAULTS: dict[str, Any] = {
    # Pause-all: no writing and no sending while true.
    "paused": False,
    # Enroll automatically when a score changes to Warm or Cold.
    "auto_enroll": True,
    # Days become minutes; only the allow-listed addresses are emailed.
    "test_mode": {"enabled": False, "minutes_per_day": 1, "allow_list": []},
    "cadence": {"cold": [0, 5, 10, 16, 23, 30], "warm": [0, 3, 7, 12, 19, 28]},
    "cooldown_days": 90,
    "persona_min_confidence": 0.7,
    "banned_phrases": [
        "discount", "% off", "special offer", "limited time", "free trial",
        "guarantee", "guaranteed", "risk-free", "certified", "certification guaranteed",
        "makes you compliant", "ensures compliance", "fully compliant",
        "100% secure", "act now",
    ],
    # The lead's local time, when known; default_timezone otherwise.
    "sending_window": {"start_hour": 9, "end_hour": 17, "weekdays": [0, 1, 2, 3, 4]},
    "default_timezone": "America/New_York",
    "daily_cap": 50,
    "batch_size": 20,
    "generate_hours_ahead": 24,
    # Review: none | all (pilot mode: every AI draft waits for approval) |
    # sample (a random share does).
    "approval": {"mode": "none", "sample_rate": 0.2},
    "judge_min_score": 0.7,
    "max_ai_attempts": 2,
    "max_send_attempts": 3,
    "ooo_delay_days": 5,
    # A click this soon after sending is a mail scanner, not a person.
    "bot_click_seconds": 120,
    # public.leads.status values that are never nurtured.
    "blocked_lead_statuses": ["DNC", "Paid", "Payment Pending", "Consultation Booked", "Escalated to Human"],
    "skip_if_in_email_nurture": True,
    # Hand-off notifications and error alerts (empty: reply_alert_email).
    "sales_email": "",
    "alert_email": "",
    "fallback_alert_rate": 0.3,
    "branding": {
        "company_name": "Aspire Tech",
        "company_address": "",
        "logo_url": "",
        "sender_name": "",
        "sender_title": "",
        "demo_url": "",
        "pricing_url": "",
    },
    # USD per million tokens, for the cost estimate on the dashboard.
    "token_prices": {"default": {"in": 0.30, "out": 2.50}},
}

_NESTED = ("test_mode", "cadence", "sending_window", "approval", "branding", "token_prices")


def merge(stored: Any) -> dict[str, Any]:
    stored = stored if isinstance(stored, dict) else {}
    out = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    for key in _NESTED:
        value = stored.get(key)
        out[key] = {**DEFAULTS[key], **(value if isinstance(value, dict) else {})}
    return out


async def load(fresh: bool = False) -> dict[str, Any]:
    """The merged settings. ``fresh`` skips the one-minute cache (the
    pause switch must take effect within one scheduler cycle)."""
    if fresh:
        await settings_store.refresh()
    return merge(await settings_store.get("nurture"))


async def save(changes: dict[str, Any]) -> dict[str, Any]:
    """Store changed keys (unknown keys are ignored)."""
    stored = await settings_store.get("nurture")
    stored = dict(stored) if isinstance(stored, dict) else {}
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        if key in _NESTED and isinstance(value, dict):
            stored[key] = {**(stored.get(key) or {}), **value}
        else:
            stored[key] = value
    await settings_store.set_value("nurture", stored, "Email Nurture settings.")
    return merge(stored)


def window(cfg: dict[str, Any]) -> BusinessHours:
    w = cfg["sending_window"]
    return BusinessHours(
        start_hour=int(w.get("start_hour", 9)),
        end_hour=int(w.get("end_hour", 17)),
        weekdays=tuple(int(d) for d in w.get("weekdays", [0, 1, 2, 3, 4])),
    )


def test_minutes(cfg: dict[str, Any]) -> float | None:
    """Minutes per "day" in test mode, None outside it."""
    t = cfg["test_mode"]
    if not t.get("enabled"):
        return None
    return max(0.05, float(t.get("minutes_per_day") or 1))


def allow_list(cfg: dict[str, Any]) -> set[str]:
    return {str(a).strip().lower() for a in cfg["test_mode"].get("allow_list") or [] if str(a).strip()}
