"""Nurture's own sending identity.

Nurture sends through the same SMTP code as the cold sequence, but from its
own account (NURTURE_FROM_EMAIL, password NURTURE_SMTP_PASSWORD), never from
a cold inbox. Replies are sent to an inbox Reply Triage already reads
(NURTURE_REPLY_TO, default: the first active cold inbox), and links point at
NURTURE_PUBLIC_URL.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from app import repository as repo
from app.config import get_settings
from app.mail.base import Inbox, MailSender
from app.mail.smtp_sender import SMTPSender

_transport: Optional[MailSender] = None


def mailbox() -> Optional[Inbox]:
    """The nurture sending account, or None when NURTURE_FROM_EMAIL is unset."""
    s = get_settings()
    email = s.nurture_from_email.strip().lower()
    if not email:
        return None
    config: dict[str, Any] = {}
    if s.nurture_smtp_host:
        config["smtp_host"] = s.nurture_smtp_host
    if s.nurture_smtp_port:
        config["smtp_port"] = s.nurture_smtp_port
    if s.nurture_imap_host:
        config["imap_host"] = s.nurture_imap_host
    return Inbox(id="nurture", email=email, display_name=s.nurture_from_name or None, provider="smtp",
                 credential_ref="NURTURE", daily_cap=None, poll_cursor=0, config=config)


def transport() -> MailSender:
    """SMTP, whatever MAIL_SENDER the cold sequence uses."""
    global _transport
    if _transport is None:
        _transport = SMTPSender()
    return _transport


def public_url() -> str:
    s = get_settings()
    if s.nurture_public_url.strip():
        return s.nurture_public_url.strip().rstrip("/")
    app = os.environ.get("APP_PUBLIC_URL", "").strip().rstrip("/")
    return f"{app}/api" if app else ""


async def reply_to() -> Optional[str]:
    s = get_settings()
    if s.nurture_reply_to.strip():
        return s.nurture_reply_to.strip()
    inboxes = await repo.active_inboxes()
    return inboxes[0].email if inboxes else None


async def problems(cfg: dict[str, Any]) -> list[str]:
    """What stops nurture from sending. Empty when it can send."""
    out: list[str] = []
    box = mailbox()
    if box is None:
        out.append("NURTURE_FROM_EMAIL is not set in .env (nurture's own sending account)")
    elif not os.environ.get("NURTURE_SMTP_PASSWORD"):
        out.append("NURTURE_SMTP_PASSWORD is not set in .env")
    else:
        cold = {i.email.lower() for i in await repo.active_inboxes()}
        if box.email in cold:
            out.append("NURTURE_FROM_EMAIL is a cold-sequence inbox; nurture needs its own account")
    if not public_url():
        out.append("NURTURE_PUBLIC_URL (or APP_PUBLIC_URL) is not set: links and unsubscribe need it")
    branding = cfg["branding"]
    if not (branding.get("demo_url") or get_settings().calcom_booking_url):
        out.append("No demo link: set Settings -> Branding -> demo URL (or CALCOM_BOOKING_URL)")
    if not branding.get("pricing_url"):
        out.append("No pricing link: set Settings -> Branding -> pricing URL")
    if not await reply_to():
        out.append("No reply-to address: set NURTURE_REPLY_TO or add an active cold inbox")
    return out


def warnings() -> list[str]:
    """Allowed, but worth knowing."""
    out = []
    url = public_url()
    if url and not url.startswith("https://"):
        out.append("Links use plain HTTP; move NURTURE_PUBLIC_URL to an HTTPS subdomain before going live")
    if get_settings().dry_run:
        out.append("DRY_RUN=true: nothing actually leaves")
    return out
