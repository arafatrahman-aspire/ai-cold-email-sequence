"""Shared MIME parsing helpers used by the reader drivers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime

# RFC 3464 status field, e.g. "Status: 5.1.1"
_DSN_STATUS = re.compile(r"^Status:\s*([245])\.(\d+)\.(\d+)", re.MULTILINE)
_DSN_RECIPIENT = re.compile(
    r"^Final-Recipient:\s*rfc822;\s*(.+)$", re.MULTILINE | re.IGNORECASE
)
# Fallback for providers that send a human-readable NDR without a proper DSN part.
_HARD_BOUNCE_PHRASES = (
    "user unknown",
    "no such user",
    "recipient not found",
    "address not found",
    "mailbox unavailable",
    "does not exist",
    "invalid recipient",
    "recipient rejected",
    "550 5.1.1",
)
_SOFT_BOUNCE_PHRASES = (
    "mailbox full",
    "over quota",
    "quota exceeded",
    "temporarily unavailable",
    "try again later",
    "greylisted",
)

_UNSUBSCRIBE_PHRASES = (
    "unsubscribe",
    "opt out",
    "opt-out",
    "remove me",
    "take me off",
    "stop emailing",
    "stop contacting",
    "do not contact",
    "don't contact me",
    "no longer wish to receive",
)


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return value.strip()


def first_address(value: str | None) -> str:
    if not value:
        return ""
    pairs = getaddresses([value])
    for _, addr in pairs:
        if addr:
            return addr.strip().lower()
    return ""


def parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _delivery_status_text(part: Message) -> str:
    """Flatten a message/delivery-status part back into header lines.

    The email package parses this content type into a list of header-only
    sub-messages rather than a decodable payload, so ``get_payload(decode=True)``
    returns None and the DSN fields (Status, Final-Recipient) would otherwise be
    invisible to the bounce classifier.
    """
    blocks: list[str] = []
    payload = part.get_payload()
    if isinstance(payload, list):
        for sub in payload:
            if isinstance(sub, Message):
                blocks.append(
                    "\n".join(f"{k}: {v}" for k, v in sub.items())
                )
    elif isinstance(payload, str):
        blocks.append(payload)
    return "\n\n".join(blocks)


def extract_text(msg: Message, limit: int = 4000) -> str:
    """Best-effort plain-text body extraction.

    Delivery-status and rfc822-header parts are included deliberately: the
    bounce classifier reads its status codes and recipient out of this text.
    """
    parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue

            if ctype == "message/delivery-status":
                text = _delivery_status_text(part)
                if text:
                    parts.append(text)
                continue

            if ctype not in ("text/plain", "text/rfc822-headers", "message/rfc822"):
                continue

            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            charset = msg.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))

    return "\n".join(parts)[:limit].strip()


def parse_references(value: str | None) -> list[str]:
    if not value:
        return []
    return re.findall(r"<[^>]+>", value)


def is_auto_reply(msg: Message) -> bool:
    auto_submitted = (msg.get("Auto-Submitted") or "").lower()
    if auto_submitted and auto_submitted != "no":
        return True
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    if (msg.get("Precedence") or "").lower() in ("auto_reply", "bulk", "junk"):
        return True
    subject = decode_mime_header(msg.get("Subject")).lower()
    return subject.startswith(("out of office", "automatic reply", "auto:", "autoreply"))


def is_bounce(msg: Message) -> bool:
    ctype = (msg.get_content_type() or "").lower()
    report_type = (msg.get_param("report-type", header="Content-Type") or "").lower()
    if ctype == "multipart/report" and report_type == "delivery-status":
        return True

    sender = first_address(msg.get("From"))
    if sender.split("@", 1)[0] in (
        "mailer-daemon", "postmaster", "mail-daemon", "no-reply-bounce"
    ):
        return True

    subject = decode_mime_header(msg.get("Subject")).lower()
    return any(
        s in subject
        for s in (
            "undeliverable", "delivery status notification", "mail delivery failed",
            "returned mail", "delivery has failed", "failure notice",
        )
    )


def classify_bounce(msg: Message, body: str) -> tuple[str | None, bool]:
    """Return ``(bounced_recipient, is_permanent)``.

    A permanent (hard) bounce suppresses the address. A transient one does not
    — a full mailbox today is not a dead address.
    """
    recipient: str | None = None
    m = _DSN_RECIPIENT.search(body)
    if m:
        recipient = first_address(m.group(1)) or m.group(1).strip().lower()

    status = _DSN_STATUS.search(body)
    if status:
        return recipient, status.group(1) == "5"

    lowered = body.lower()
    if any(p in lowered for p in _SOFT_BOUNCE_PHRASES):
        return recipient, False
    if any(p in lowered for p in _HARD_BOUNCE_PHRASES):
        return recipient, True

    # Unclassifiable NDR: treat as transient rather than burning the address.
    return recipient, False


def looks_like_unsubscribe(subject: str, body: str) -> bool:
    """Detect an opt-out in a human reply.

    Only the first part of the body is examined: quoted history below a reply
    routinely contains the word "unsubscribe" from our own footer.
    """
    head = body[:600].lower()
    subj = subject.lower()
    return any(p in head or p in subj for p in _UNSUBSCRIBE_PHRASES)
