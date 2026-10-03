"""Microsoft Graph reading driver (MAIL_READER=graph).

Graph has no UID, so the cursor is a received-timestamp stored as epoch
seconds in ``inboxes.poll_cursor``, the same column the IMAP reader keeps its
last UID in, so the two readers stay interchangeable.
"""

from __future__ import annotations

import email
import logging
from datetime import datetime, timezone

import httpx

from app.mail import parsing
from app.mail.base import Inbox, IncomingMessage, MailError, MailReader
from app.mail.graph_sender import GRAPH_BASE, get_token_cache

log = logging.getLogger(__name__)


class GraphReader(MailReader):
    name = "graph"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=60.0)

    async def current_cursor(self, inbox: Inbox) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    async def fetch_new(self, inbox: Inbox, limit: int = 50) -> list[IncomingMessage]:
        token = await get_token_cache().token(self._client)
        headers = {"Authorization": f"Bearer {token}"}

        since = datetime.fromtimestamp(max(inbox.poll_cursor, 0), tz=timezone.utc)
        params = {
            "$top": str(limit),
            "$orderby": "receivedDateTime asc",
            "$filter": f"receivedDateTime gt {since.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "$select": "id,internetMessageId,subject,from,toRecipients,"
                       "receivedDateTime,bodyPreview,conversationId",
        }
        resp = await self._client.get(
            f"{GRAPH_BASE}/users/{inbox.email}/mailFolders/inbox/messages",
            headers=headers,
            params=params,
        )
        if resp.status_code >= 400:
            raise MailError(
                f"Graph message fetch failed ({resp.status_code}): {resp.text[:300]}",
                permanent=400 <= resp.status_code < 500 and resp.status_code != 429,
            )

        out: list[IncomingMessage] = []
        for item in resp.json().get("value", []):
            received = parsing.parse_date(item.get("receivedDateTime"))
            mime = await self._fetch_mime(inbox, item["id"], headers)

            bounce = False
            bounced_recipient = None
            permanent = False
            auto_reply = False
            in_reply_to = None
            references: list[str] = []
            body = item.get("bodyPreview", "") or ""

            if mime is not None:
                body = parsing.extract_text(mime) or body
                bounce = parsing.is_bounce(mime)
                auto_reply = parsing.is_auto_reply(mime)
                in_reply_to = (mime.get("In-Reply-To") or "").strip() or None
                references = parsing.parse_references(mime.get("References"))
                if bounce:
                    bounced_recipient, permanent = parsing.classify_bounce(mime, body)

            sender = (item.get("from") or {}).get("emailAddress", {}).get("address", "")
            recipients = item.get("toRecipients") or []
            to_addr = (
                recipients[0].get("emailAddress", {}).get("address", "")
                if recipients else inbox.email
            )

            out.append(
                IncomingMessage(
                    # Cursor is an epoch second, monotonic with receivedDateTime.
                    uid=int(received.timestamp()),
                    message_id=item.get("internetMessageId"),
                    in_reply_to=in_reply_to,
                    references=references,
                    from_email=(sender or "").lower(),
                    to_email=(to_addr or "").lower(),
                    subject=item.get("subject") or "",
                    body_snippet=body[:2000],
                    received_at=received,
                    is_bounce_report=bounce,
                    bounced_recipient=bounced_recipient,
                    bounce_is_permanent=permanent,
                    is_auto_reply=auto_reply,
                )
            )
        return out

    async def _fetch_mime(self, inbox: Inbox, graph_id: str, headers: dict):
        """Pull the raw MIME so the shared bounce/auto-reply parsing applies."""
        try:
            resp = await self._client.get(
                f"{GRAPH_BASE}/users/{inbox.email}/messages/{graph_id}/$value",
                headers=headers,
            )
            if resp.status_code >= 400:
                log.warning("could not fetch MIME for %s: %s", graph_id, resp.status_code)
                return None
            return email.message_from_bytes(resp.content)
        except httpx.HTTPError as exc:
            log.warning("MIME fetch error for %s: %s", graph_id, exc)
            return None

    async def aclose(self) -> None:
        await self._client.aclose()
