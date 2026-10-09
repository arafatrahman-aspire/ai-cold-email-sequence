"""Internal emails from nurture: hand-off notifications and error alerts.

They go to Settings -> sales_email / alert_email, or the console-wide
reply_alert_email when those are empty, the same channel the reply alerts
already use. They are sent from the nurture account when it is set up,
otherwise from the first cold inbox (internal mail, not prospect mail).
Alerts of one kind are sent at most once an hour.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app import repository as repo
from app import settings_store
from app.mail.base import Inbox, OutgoingMessage
from app.mail.factory import get_sender
from app.nurture import repo as nrepo
from app.nurture import sending

log = logging.getLogger(__name__)

_last_sent: dict[str, float] = {}
ALERT_EVERY_SECONDS = 3600


async def recipient(cfg: dict[str, Any], key: str) -> str:
    to = str(cfg.get(key) or "").strip()
    if not to:
        to = str(await settings_store.get("reply_alert_email") or "").strip()
    return to


async def _from() -> tuple[Optional[Inbox], Any]:
    box = await sending.sending_inbox()
    if box is not None:
        return box, sending.transport()
    inboxes = await repo.active_inboxes()
    return (inboxes[0] if inboxes else None), get_sender()


async def send_internal(to: str, subject: str, body: str) -> None:
    """Raises if it cannot be sent (the caller records why)."""
    inbox, transport = await _from()
    if inbox is None:
        raise RuntimeError("no account to send internal email from")
    await transport.send(inbox, OutgoingMessage(to_email=to, subject=subject[:200], body_text=body))


async def alert(cfg: dict[str, Any], kind: str, subject: str, body: str) -> bool:
    """An error alert, at most once an hour per kind. Never raises."""
    now = time.monotonic()
    if now - _last_sent.get(kind, -ALERT_EVERY_SECONDS) < ALERT_EVERY_SECONDS:
        return False
    to = await recipient(cfg, "alert_email")
    if not to:
        log.warning("nurture alert (%s) not sent: no alert address set: %s", kind, subject)
        return False
    try:
        await send_internal(to, f"[Email Nurture] {subject}", body)
        _last_sent[kind] = now
        return True
    except Exception:
        log.exception("could not send nurture alert %s", kind)
        return False


async def check_fallback_rate(cfg: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    try:
        r = (await nrepo.stats(now - timedelta(hours=24), now + timedelta(minutes=1))).get("ai") or {}
    except Exception:
        return
    written, fallbacks = int(r.get("written") or 0), int(r.get("fallbacks") or 0)
    threshold = float(cfg["fallback_alert_rate"])
    if written >= 10 and fallbacks / written > threshold:
        await alert(cfg, "fallback_rate", "Many AI drafts are failing",
                    f"In the last 24 hours {fallbacks} of {written} nurture emails used the "
                    f"pre-approved fallback instead of an AI draft ({fallbacks / written:.0%}, "
                    f"threshold {threshold:.0%}).\n\nCheck the AI provider (quota, model name) and the "
                    "check results on recent messages in Email Nurture -> Leads.")


async def job_failed(cfg: dict[str, Any], job: str, error: BaseException) -> None:
    await alert(cfg, f"job:{job}", f"The {job} job failed",
                f"The nurture {job} job raised an error:\n\n{type(error).__name__}: {error}\n\n"
                "It runs again on its next cycle; see the service logs for details.")
