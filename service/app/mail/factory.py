"""Transport selection. Sender and reader are chosen independently."""

from __future__ import annotations

from typing import Optional

from app.config import get_settings
from app.mail.base import MailError, MailReader, MailSender
from app.mail.graph_reader import GraphReader
from app.mail.graph_sender import GraphSender
from app.mail.imap_reader import IMAPReader
from app.mail.smtp_sender import SMTPSender

_sender: Optional[MailSender] = None
_reader: Optional[MailReader] = None


def get_sender() -> MailSender:
    global _sender
    if _sender is None:
        name = get_settings().mail_sender
        if name == "smtp":
            _sender = SMTPSender()
        elif name == "graph":
            _sender = GraphSender()
        else:
            raise MailError(f"unknown MAIL_SENDER: {name}", permanent=True)
    return _sender


def get_reader() -> MailReader:
    global _reader
    if _reader is None:
        name = get_settings().mail_reader
        if name == "imap":
            _reader = IMAPReader()
        elif name == "graph":
            _reader = GraphReader()
        else:
            raise MailError(f"unknown MAIL_READER: {name}", permanent=True)
    return _reader


async def close_transports() -> None:
    global _sender, _reader
    if _sender is not None:
        await _sender.aclose()
        _sender = None
    if _reader is not None:
        await _reader.aclose()
        _reader = None
