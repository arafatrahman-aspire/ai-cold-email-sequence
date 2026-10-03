"""SMTP submission driver.

Works against any SMTP relay that accepts a username/password: a VPS Postfix
relay, Outlook/M365 with an app password, Zoho, Fastmail, and so on. Host,
port and TLS mode default from the environment and may be overridden per inbox
via the ``inboxes.config`` JSON.

smtplib is blocking, so each send runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.config import get_settings
from app.mail.base import Inbox, MailError, MailSender, OutgoingMessage, SendResult

log = logging.getLogger(__name__)

# SMTP codes that will never succeed on retry.
_PERMANENT_PREFIXES = ("5",)


def build_mime(inbox: Inbox, message: OutgoingMessage, message_id: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["From"] = formataddr((inbox.display_name or "", inbox.email))
    msg["To"] = message.to_email
    msg["Subject"] = message.subject

    if message.reply_to:
        msg["Reply-To"] = message.reply_to

    if message.in_reply_to:
        msg["In-Reply-To"] = message.in_reply_to
    if message.references:
        msg["References"] = " ".join(message.references)

    if message.unsubscribe_mailto:
        # RFC 8058: the mailto form works without a hosted landing page, and
        # mailbox providers weight its presence positively.
        msg["List-Unsubscribe"] = f"<mailto:{message.unsubscribe_mailto}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    msg.set_content(message.body_text)
    return msg


class SMTPSender(MailSender):
    name = "smtp"

    def _send_blocking(self, inbox: Inbox, msg: EmailMessage) -> None:
        s = get_settings()
        cfg = inbox.config or {}
        host = cfg.get("smtp_host", s.smtp_host)
        port = int(cfg.get("smtp_port", s.smtp_port))
        use_ssl = bool(cfg.get("smtp_ssl", s.smtp_ssl))
        use_starttls = bool(cfg.get("smtp_starttls", s.smtp_starttls))
        username = cfg.get("smtp_username", inbox.email)
        password = inbox.secret("SMTP_PASSWORD")
        timeout = s.smtp_timeout_seconds

        context = ssl.create_default_context()
        try:
            if use_ssl:
                client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
            else:
                client = smtplib.SMTP(host, port, timeout=timeout)
            with client:
                client.ehlo()
                if use_starttls and not use_ssl:
                    client.starttls(context=context)
                    client.ehlo()
                client.login(username, password)
                client.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailError(
                f"SMTP auth failed for {inbox.email}: {exc}", permanent=True
            ) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            # The recipient was rejected at submission time — a synchronous
            # hard bounce. Never retry this.
            raise MailError(
                f"recipient refused: {exc.recipients}", permanent=True
            ) from exc
        except smtplib.SMTPResponseException as exc:
            permanent = str(exc.smtp_code).startswith(_PERMANENT_PREFIXES)
            raise MailError(
                f"SMTP {exc.smtp_code}: {exc.smtp_error!r}", permanent=permanent
            ) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError(f"SMTP transport error: {exc}") from exc

    async def send(self, inbox: Inbox, message: OutgoingMessage) -> SendResult:
        domain = inbox.email.split("@", 1)[-1] or None
        message_id = make_msgid(domain=domain)
        msg = build_mime(inbox, message, message_id)

        if get_settings().dry_run:
            log.info(
                "[dry-run] would send to %s from %s: %r",
                message.to_email, inbox.email, message.subject,
            )
            return SendResult(message_id=message_id)

        await asyncio.to_thread(self._send_blocking, inbox, msg)
        log.info("sent to %s from %s (%s)", message.to_email, inbox.email, message_id)
        return SendResult(message_id=message_id)
