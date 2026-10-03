"""IMAP polling driver.

imaplib is blocking, so a poll runs in a worker thread. The inbox row keeps the
last UID seen in ``poll_cursor``, which makes each poll cheap and idempotent.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re

from app.config import get_settings
from app.mail import parsing
from app.mail.base import Inbox, IncomingMessage, MailError, MailReader

log = logging.getLogger(__name__)


class IMAPReader(MailReader):
    name = "imap"

    def _fetch_blocking(self, inbox: Inbox, limit: int) -> list[IncomingMessage]:
        s = get_settings()
        cfg = inbox.config or {}
        host = cfg.get("imap_host", s.imap_host)
        port = int(cfg.get("imap_port", s.imap_port))
        use_ssl = bool(cfg.get("imap_ssl", s.imap_ssl))
        folder = cfg.get("imap_folder", s.imap_folder)
        username = cfg.get("imap_username", inbox.email)
        password = inbox.optional_secret("IMAP_PASSWORD") or inbox.secret("SMTP_PASSWORD")

        try:
            client = (
                imaplib.IMAP4_SSL(host, port, timeout=s.imap_timeout_seconds)
                if use_ssl
                else imaplib.IMAP4(host, port, timeout=s.imap_timeout_seconds)
            )
        except OSError as exc:
            raise MailError(f"IMAP connect failed for {inbox.email}: {exc}") from exc

        messages: list[IncomingMessage] = []
        try:
            try:
                client.login(username, password)
            except imaplib.IMAP4.error as exc:
                raise MailError(
                    f"IMAP auth failed for {inbox.email}: {exc}", permanent=True
                ) from exc

            status, _ = client.select(folder, readonly=True)
            if status != "OK":
                raise MailError(f"cannot select IMAP folder {folder!r}")

            # UID search is inclusive of the low bound, so start one past the
            # cursor to avoid re-reading the last message every poll.
            since_uid = inbox.poll_cursor + 1
            status, data = client.uid("search", None, f"UID {since_uid}:*")
            if status != "OK":
                raise MailError(f"IMAP search failed for {inbox.email}")

            uids = [int(u) for u in (data[0] or b"").split()]
            # The "UID n:*" form always returns at least the highest UID even
            # when nothing is new; filter those out explicitly.
            uids = sorted(u for u in uids if u >= since_uid)[:limit]

            for uid in uids:
                status, payload = client.uid("fetch", str(uid), "(RFC822)")
                if status != "OK" or not payload or not isinstance(payload[0], tuple):
                    log.warning("could not fetch UID %s from %s", uid, inbox.email)
                    continue
                raw = payload[0][1]
                messages.append(_to_incoming(uid, raw))
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

        return messages

    async def fetch_new(self, inbox: Inbox, limit: int = 50) -> list[IncomingMessage]:
        return await asyncio.to_thread(self._fetch_blocking, inbox, limit)

    def _current_cursor_blocking(self, inbox: Inbox) -> int:
        s = get_settings()
        cfg = inbox.config or {}
        host = cfg.get("imap_host", s.imap_host)
        port = int(cfg.get("imap_port", s.imap_port))
        use_ssl = bool(cfg.get("imap_ssl", s.imap_ssl))
        folder = cfg.get("imap_folder", s.imap_folder)
        username = cfg.get("imap_username", inbox.email)
        password = inbox.optional_secret("IMAP_PASSWORD") or inbox.secret("SMTP_PASSWORD")
        try:
            client = (
                imaplib.IMAP4_SSL(host, port, timeout=s.imap_timeout_seconds)
                if use_ssl
                else imaplib.IMAP4(host, port, timeout=s.imap_timeout_seconds)
            )
        except OSError as exc:
            raise MailError(f"IMAP connect failed for {inbox.email}: {exc}") from exc
        try:
            try:
                client.login(username, password)
            except imaplib.IMAP4.error as exc:
                raise MailError(
                    f"IMAP auth failed for {inbox.email}: {exc}", permanent=True
                ) from exc
            status, data = client.status(folder, "(UIDNEXT)")
            match = re.search(rb"UIDNEXT (\d+)", data[0] or b"") if status == "OK" else None
            if not match:
                raise MailError(f"cannot read UIDNEXT of IMAP folder {folder!r}")
            return int(match.group(1)) - 1
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    async def current_cursor(self, inbox: Inbox) -> int:
        return await asyncio.to_thread(self._current_cursor_blocking, inbox)


def _to_incoming(uid: int, raw: bytes) -> IncomingMessage:
    msg = email.message_from_bytes(raw)
    body = parsing.extract_text(msg)
    subject = parsing.decode_mime_header(msg.get("Subject"))

    bounce = parsing.is_bounce(msg)
    bounced_recipient = None
    permanent = False
    if bounce:
        bounced_recipient, permanent = parsing.classify_bounce(msg, body)

    return IncomingMessage(
        uid=uid,
        message_id=(msg.get("Message-ID") or "").strip() or None,
        in_reply_to=(msg.get("In-Reply-To") or "").strip() or None,
        references=parsing.parse_references(msg.get("References")),
        from_email=parsing.first_address(msg.get("From")),
        to_email=parsing.first_address(msg.get("To")),
        subject=subject,
        body_snippet=body[:2000],
        received_at=parsing.parse_date(msg.get("Date")),
        is_bounce_report=bounce,
        bounced_recipient=bounced_recipient,
        bounce_is_permanent=permanent,
        is_auto_reply=parsing.is_auto_reply(msg),
    )
