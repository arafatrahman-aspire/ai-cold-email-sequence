"""Transport selection. Sender and reader are chosen independently."""

from __future__ import annotations

from typing import Optional

from app.config import get_settings
from app.mail.base import Inbox, MailError, MailReader, MailSender
from app.mail.brevo_sender import BrevoSender
from app.mail.graph_reader import GraphReader
from app.mail.graph_sender import GraphSender
from app.mail.imap_reader import IMAPReader
from app.mail.smtp_sender import SMTPSender

_sender: Optional[MailSender] = None
_readers: dict[str, MailReader] = {}


def get_sender() -> MailSender:
    global _sender
    if _sender is None:
        name = get_settings().mail_sender
        if name == "smtp":
            _sender = SMTPSender()
        elif name == "graph":
            _sender = GraphSender()
        elif name == "brevo":
            _sender = BrevoSender()
        else:
            raise MailError(f"unknown MAIL_SENDER: {name}", permanent=True)
    return _sender


def get_reader(inbox: Optional[Inbox] = None) -> MailReader:
    """The reader for ``inbox``: its ``config.reader`` ("imap" | "graph") if
    set, else MAIL_READER. So a Microsoft 365 inbox can be read through Graph
    while a Gmail inbox is still read over IMAP."""
    name = str(((inbox.config or {}) if inbox else {}).get("reader") or get_settings().mail_reader)
    if name not in _readers:
        if name == "imap":
            _readers[name] = IMAPReader()
        elif name == "graph":
            _readers[name] = GraphReader()
        else:
            raise MailError(f"unknown mail reader: {name}", permanent=True)
    return _readers[name]


async def close_transports() -> None:
    global _sender
    if _sender is not None:
        await _sender.aclose()
        _sender = None
    for reader in _readers.values():
        await reader.aclose()
    _readers.clear()
