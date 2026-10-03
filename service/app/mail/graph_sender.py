"""Microsoft Graph sending driver.

Used when MAIL_SENDER=graph. Requires an Entra ID app with the application
permission ``Mail.Send`` (admin-consented) and, for reading, ``Mail.Read``.

Rather than the one-shot ``/sendMail`` endpoint — which discards the resulting
message id — this creates a draft so it can set ``internetMessageId`` and the
threading headers explicitly, then sends the draft. That keeps the ``sends``
log joinable against inbound replies exactly as the SMTP path does.
"""

from __future__ import annotations

import logging
import time
from email.utils import make_msgid

import httpx

from app.config import get_settings
from app.mail.base import Inbox, MailError, MailSender, OutgoingMessage, SendResult

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_BASE = "https://login.microsoftonline.com"


class GraphTokenCache:
    """Client-credentials token, refreshed a minute before expiry."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str) -> None:
        if not (tenant_id and client_id and client_secret):
            raise MailError(
                "GRAPH_TENANT_ID, GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET are "
                "required when a Graph transport is selected",
                permanent=True,
            )
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token

        resp = await client.post(
            f"{LOGIN_BASE}/{self._tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        if resp.status_code >= 400:
            raise MailError(
                f"Entra token request failed ({resp.status_code}): {resp.text[:300]}",
                permanent=resp.status_code < 500,
            )
        data = resp.json()
        self._token = data["access_token"]
        self._expires_at = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._token


_token_cache: GraphTokenCache | None = None


def get_token_cache() -> GraphTokenCache:
    global _token_cache
    if _token_cache is None:
        s = get_settings()
        _token_cache = GraphTokenCache(
            s.graph_tenant_id, s.graph_client_id, s.graph_client_secret
        )
    return _token_cache


class GraphSender(MailSender):
    name = "graph"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=60.0)

    async def _headers(self) -> dict[str, str]:
        token = await get_token_cache().token(self._client)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def send(self, inbox: Inbox, message: OutgoingMessage) -> SendResult:
        domain = inbox.email.split("@", 1)[-1] or None
        message_id = make_msgid(domain=domain)

        if get_settings().dry_run:
            log.info(
                "[dry-run] would send via Graph to %s from %s: %r",
                message.to_email, inbox.email, message.subject,
            )
            return SendResult(message_id=message_id)

        headers = await self._headers()
        internet_headers = [
            {"name": "x-out01-message-id", "value": message_id},
        ]
        if message.unsubscribe_mailto:
            internet_headers.append({
                "name": "x-out01-unsubscribe",
                "value": f"<mailto:{message.unsubscribe_mailto}>",
            })

        draft = {
            "subject": message.subject,
            "body": {"contentType": "Text", "content": message.body_text},
            "toRecipients": [{"emailAddress": {"address": message.to_email}}],
            "internetMessageId": message_id,
            # Graph rejects the standard reserved headers, so custom x- headers
            # carry what the SMTP path puts in List-Unsubscribe.
            "singleValueExtendedProperties": [],
            "internetMessageHeaders": internet_headers,
        }
        if message.reply_to:
            draft["replyTo"] = [{"emailAddress": {"address": message.reply_to}}]

        create = await self._client.post(
            f"{GRAPH_BASE}/users/{inbox.email}/messages", headers=headers, json=draft
        )
        if create.status_code >= 400:
            raise MailError(
                f"Graph draft creation failed ({create.status_code}): {create.text[:300]}",
                permanent=400 <= create.status_code < 500 and create.status_code != 429,
            )
        created = create.json()
        graph_id = created["id"]
        conversation_id = created.get("conversationId")

        send = await self._client.post(
            f"{GRAPH_BASE}/users/{inbox.email}/messages/{graph_id}/send",
            headers=headers,
        )
        if send.status_code >= 400:
            raise MailError(
                f"Graph send failed ({send.status_code}): {send.text[:300]}",
                permanent=400 <= send.status_code < 500 and send.status_code != 429,
            )

        log.info("sent via Graph to %s from %s (%s)", message.to_email, inbox.email, message_id)
        return SendResult(
            message_id=created.get("internetMessageId") or message_id,
            thread_id=conversation_id,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
