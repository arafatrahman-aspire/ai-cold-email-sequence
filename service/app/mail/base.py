"""Mail transport interfaces.

Sending and reading are separate interfaces because they are genuinely
independent choices: you can send over SMTP and read over Microsoft Graph, or
any other combination, by setting MAIL_SENDER and MAIL_READER.
"""

from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


class MailError(RuntimeError):
    """Transport failure. Treated as retryable unless ``permanent`` is set."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass
class Inbox:
    """A sending mailbox, as loaded from the ``inboxes`` table."""

    id: str
    email: str
    display_name: Optional[str]
    provider: str
    credential_ref: str
    daily_cap: Optional[int]
    poll_cursor: int  # last IMAP UID, or last Graph receive time (epoch secs)
    config: dict[str, Any] = field(default_factory=dict)

    def secret(self, suffix: str) -> str:
        """Resolve a secret from the environment.

        Secrets are never stored in the database; the row only carries a
        reference. ``credential_ref='INBOX_A'`` plus ``suffix='SMTP_PASSWORD'``
        reads ``INBOX_A_SMTP_PASSWORD``.
        """
        key = f"{self.credential_ref}_{suffix}".upper()
        value = os.environ.get(key, "")
        if not value:
            raise MailError(
                f"missing credential {key} for inbox {self.email}", permanent=True
            )
        return value

    def optional_secret(self, suffix: str, default: str = "") -> str:
        key = f"{self.credential_ref}_{suffix}".upper()
        return os.environ.get(key, default)


@dataclass
class OutgoingMessage:
    to_email: str
    subject: str
    body_text: str
    # Threading: follow-ups reference the opener so they land in the same thread.
    in_reply_to: Optional[str] = None
    references: list[str] = field(default_factory=list)
    unsubscribe_mailto: Optional[str] = None
    reply_to: Optional[str] = None
    # Optional (Email Nurture): an HTML alternative to body_text, and an
    # HTTPS one-click unsubscribe endpoint (RFC 8058). Cold email sets neither.
    body_html: Optional[str] = None
    unsubscribe_url: Optional[str] = None


@dataclass
class SendResult:
    message_id: str
    thread_id: Optional[str] = None


@dataclass
class IncomingMessage:
    uid: int
    message_id: Optional[str]
    in_reply_to: Optional[str]
    references: list[str]
    from_email: str
    to_email: str
    subject: str
    body_snippet: str
    received_at: datetime
    # Populated when the transport itself recognises a delivery-status report.
    is_bounce_report: bool = False
    bounced_recipient: Optional[str] = None
    bounce_is_permanent: bool = False
    is_auto_reply: bool = False


class MailSender(abc.ABC):
    name: str

    @abc.abstractmethod
    async def send(self, inbox: Inbox, message: OutgoingMessage) -> SendResult: ...

    async def aclose(self) -> None:  # pragma: no cover
        return None


class MailReader(abc.ABC):
    name: str

    @abc.abstractmethod
    async def fetch_new(self, inbox: Inbox, limit: int = 50) -> list[IncomingMessage]:
        """Return messages newer than the inbox's stored cursor."""

    @abc.abstractmethod
    async def current_cursor(self, inbox: Inbox) -> int:
        """The cursor value meaning "everything up to now has been seen".

        Used on an inbox's first poll so mail that was already there (none of
        it replies to cold email) is skipped rather than read.
        """

    async def aclose(self) -> None:  # pragma: no cover
        return None
