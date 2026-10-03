from datetime import datetime, timezone

from app.mail.base import Inbox, IncomingMessage
from app.workers.alerts import build_alert, gmail_link


def _inbox(email="outreach@gmail.com"):
    return Inbox(id="i", email=email, display_name=None, provider="smtp",
                 credential_ref="INBOX_A", daily_cap=15, poll_cursor=0)


def _reply(snippet="Sounds interesting, can we talk Tuesday?"):
    return IncomingMessage(
        uid=1, message_id="<abc123@mail.example.com>", in_reply_to="<mid-1@x>", references=[],
        from_email="dana@northwind.com", to_email="outreach@gmail.com",
        subject="Re: phishing simulations", body_snippet=snippet,
        received_at=datetime(2026, 9, 28, 14, 5, tzinfo=timezone.utc),
        is_bounce_report=False, bounced_recipient=None, bounce_is_permanent=False,
        is_auto_reply=False,
    )


def test_gmail_link_only_for_gmail_inboxes():
    link = gmail_link("outreach@gmail.com", "<abc123@mail.example.com>")
    assert link.startswith("https://mail.google.com/mail/u/?authuser=outreach%40gmail.com#search/")
    assert "rfc822msgid%3Aabc123%40mail.example.com" in link
    assert gmail_link("me@company.com", "<abc@x>") is None
    assert gmail_link("outreach@gmail.com", None) is None


def test_alert_names_the_lead_and_quotes_the_reply():
    lead = {"first_name": "Dana", "last_name": "Okafor", "job_title": "CISO", "company": "Northwind"}
    subject, body = build_alert(_inbox(), lead, _reply())
    assert subject == "New reply from Dana Okafor: Re: phishing simulations"
    assert "New reply from Dana Okafor <dana@northwind.com>" in body
    assert "Lead:     Dana Okafor · CISO · Northwind" in body
    assert "Received: 2026-09-28 14:05 UTC" in body
    assert "Sounds interesting, can we talk Tuesday?" in body
    assert "Reply to them from outreach@gmail.com." in body
    assert "Open in Gmail: https://mail.google.com/" in body


def test_alert_without_lead_details_or_gmail():
    subject, body = build_alert(_inbox("me@company.com"), None, _reply("x" * 5000))
    assert subject.startswith("New reply from dana@northwind.com")
    assert "Lead:     dana@northwind.com" in body
    assert "Open in Gmail" not in body
    assert len(body) < 2500  # long replies are trimmed
