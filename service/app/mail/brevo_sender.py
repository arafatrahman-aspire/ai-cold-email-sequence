"""Brevo (ex-Sendinblue) transactional email API driver.

Selected with MAIL_SENDER=brevo; needs BREVO_API_KEY. The From address is the
inbox's email, which must be a verified sender (or domain) in Brevo. Replies
are still read from the inbox over IMAP / Graph as before.

The Message-ID Brevo returns is the one the recipient sees, so it is what we
store: replies and follow-ups thread on it exactly as with SMTP.
"""

from __future__ import annotations

import logging
from email.utils import make_msgid
from typing import Any, Optional

import httpx

from app.config import get_settings
from app.mail.base import Inbox, MailError, MailSender, OutgoingMessage, SendResult

log = logging.getLogger(__name__)


def build_payload(inbox: Inbox, message: OutgoingMessage) -> dict[str, Any]:
    sender: dict[str, str] = {"email": inbox.email}
    if inbox.display_name:
        sender["name"] = inbox.display_name
    payload: dict[str, Any] = {
        "sender": sender,
        "to": [{"email": message.to_email}],
        "subject": message.subject,
        "textContent": message.body_text,
    }
    if message.body_html:
        payload["htmlContent"] = message.body_html
    if message.reply_to:
        payload["replyTo"] = {"email": message.reply_to}

    # Same headers the SMTP driver writes (threading and unsubscribe).
    headers: dict[str, str] = {}
    if message.in_reply_to:
        headers["In-Reply-To"] = message.in_reply_to
    if message.references:
        headers["References"] = " ".join(message.references)
    targets = []
    if message.unsubscribe_url:
        targets.append(f"<{message.unsubscribe_url}>")
    if message.unsubscribe_mailto:
        targets.append(f"<mailto:{message.unsubscribe_mailto}>")
    if targets:
        headers["List-Unsubscribe"] = ", ".join(targets)
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if headers:
        payload["headers"] = headers
    return payload


class BrevoSender(MailSender):
    name = "brevo"

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            s = get_settings()
            self._client = httpx.AsyncClient(base_url=s.brevo_base_url, timeout=s.smtp_timeout_seconds)
        return self._client

    async def send(self, inbox: Inbox, message: OutgoingMessage) -> SendResult:
        s = get_settings()
        if s.dry_run:
            message_id = make_msgid(domain=inbox.email.split("@", 1)[-1] or None)
            log.info("[dry-run] would send via Brevo to %s from %s: %r",
                     message.to_email, inbox.email, message.subject)
            return SendResult(message_id=message_id)
        if not s.brevo_api_key:
            raise MailError("MAIL_SENDER=brevo but BREVO_API_KEY is not set", permanent=True)

        try:
            resp = await self._http().post(
                "/v3/smtp/email", json=build_payload(inbox, message),
                headers={"api-key": s.brevo_api_key, "accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise MailError(f"Brevo unreachable: {exc}") from exc

        if resp.status_code >= 400:
            # 400 (bad request, unverified sender), 401/403 (key): retrying
            # will not help. 429 and 5xx: try again later.
            permanent = resp.status_code < 500 and resp.status_code != 429
            raise MailError(f"Brevo {resp.status_code}: {resp.text[:300]}", permanent=permanent)

        message_id = (resp.json() or {}).get("messageId")
        if not message_id:
            raise MailError("Brevo accepted the email but returned no messageId")
        log.info("sent via Brevo to %s from %s (%s)", message.to_email, inbox.email, message_id)
        return SendResult(message_id=message_id)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
