"""Brevo engagement sync: what happened to the emails we sent through Brevo.

Every few minutes the transactional event log (GET /v3/smtp/statistics/events,
the last 2 days, so a missed run is caught up) is read and each event about
one of our emails (matched by Message-ID: cold step, nurture email or reply
answer) is stored once (0019's email_events). The Lead Journey shows them.

A new event can also act:
  hard bounce / invalid / blocked  the address is suppressed, the sequence stops
  spam complaint                   suppressed, the sequence stops, lead marked DNC
  unsubscribed                     suppressed, the sequence stops
Suppression also ends Email Nurture at its next send. Opens and clicks only
come in while open/click tracking is on in Brevo; bounces and complaints
always do.

Only runs with MAIL_SENDER=brevo.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app import repository as repo
from app.config import get_settings

log = logging.getLogger(__name__)

PAGE = 2500
MAX_PAGES = 8
LOOKBACK_DAYS = 2

# Brevo's event names -> ours. Anything else ("requests", "error") is skipped.
EVENTS = {
    "delivered": "delivered",
    "opened": "opened",
    "uniqueopened": "opened",
    "loadedbyproxy": "opened",
    "clicks": "clicked",
    "click": "clicked",
    "softbounces": "soft_bounce",
    "softbounce": "soft_bounce",
    "hardbounces": "hard_bounce",
    "hardbounce": "hard_bounce",
    "invalid": "hard_bounce",
    "blocked": "blocked",
    "spam": "spam",
    "complaint": "spam",
    "unsubscribed": "unsubscribed",
    "deferred": "deferred",
}


def _when(value: Any) -> Optional[datetime]:
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


async def fetch_events(client: httpx.AsyncClient, api_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        resp = await client.get(
            "/v3/smtp/statistics/events",
            params={"days": LOOKBACK_DAYS, "limit": PAGE, "offset": page * PAGE, "sort": "desc"},
            headers={"api-key": api_key, "accept": "application/json"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Brevo events {resp.status_code}: {resp.text[:300]}")
        batch = (resp.json() or {}).get("events") or []
        out.extend(batch)
        if len(batch) < PAGE:
            break
    return out


async def act(event: str, email: str, lead_id: Optional[str]) -> Optional[str]:
    """What a new bounce, complaint or unsubscribe does. Returns a label."""
    if event in ("hard_bounce", "blocked"):
        await repo.suppress(email, "hard_bounce")
        if lead_id:
            await repo.cancel_sequence(lead_id, "bounced", "hard bounce (Brevo)")
        return "stopped_bounce"
    if event == "spam":
        await repo.suppress(email, "complaint")
        if lead_id:
            await repo.cancel_sequence(lead_id, "unsubscribed", "marked our email as spam (Brevo)")
            await repo.mark_dnc(lead_id)
        return "stopped_spam"
    if event == "unsubscribed":
        await repo.suppress(email, "unsubscribed")
        if lead_id:
            await repo.cancel_sequence(lead_id, "unsubscribed", "unsubscribed (Brevo)")
        return "stopped_unsubscribe"
    return None


async def run_once(transport: Optional[httpx.AsyncBaseTransport] = None) -> dict[str, int]:
    s = get_settings()
    if s.mail_sender != "brevo" or not s.brevo_api_key:
        return {}
    async with httpx.AsyncClient(base_url=s.brevo_base_url, timeout=30, transport=transport) as client:
        events = await fetch_events(client, s.brevo_api_key)

    stats: dict[str, int] = {}
    for ev in events:
        kind = EVENTS.get(str(ev.get("event") or "").replace("_", "").lower())
        message_id, at = ev.get("messageId"), _when(ev.get("date"))
        if not kind or not message_id or at is None:
            continue
        result = await repo.record_email_event(message_id, ev.get("email"), kind, ev.get("link"),
                                               ev.get("reason"), at)
        if result.get("outcome") != "new":
            continue
        stats[kind] = stats.get(kind, 0) + 1
        try:
            done = await act(kind, (ev.get("email") or "").lower(), result.get("lead_id"))
        except Exception:
            log.exception("engagement: acting on %s for %s failed", kind, ev.get("email"))
            done = "failed"
        if done:
            stats[done] = stats.get(done, 0) + 1
            log.info("engagement: %s for %s -> %s", kind, ev.get("email"), done)
    if stats:
        log.info("Brevo engagement: %s", stats)
    return stats
