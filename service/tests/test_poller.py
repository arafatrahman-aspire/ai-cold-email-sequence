from datetime import datetime, timezone

import pytest

from app.mail.base import Inbox, IncomingMessage, MailError
from app.workers import poller


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _inbox(cursor: int) -> Inbox:
    return Inbox(
        id="inbox-1", email="me@example.com", display_name=None, provider="smtp",
        credential_ref="INBOX_A", daily_cap=15, poll_cursor=cursor,
    )


def _message(uid: int, sender: str) -> IncomingMessage:
    return IncomingMessage(
        uid=uid, message_id=f"<m{uid}@x>", in_reply_to=None, references=[],
        from_email=sender, to_email="me@example.com", subject="Hello",
        body_snippet="hi", received_at=datetime.now(timezone.utc),
        is_bounce_report=False, bounced_recipient=None,
        bounce_is_permanent=False, is_auto_reply=False,
    )


class FakeReader:
    def __init__(self, messages=None, cursor=118, fail=None):
        self.messages = messages or []
        self.cursor = cursor
        self.fail = fail
        self.fetched = False

    async def fetch_new(self, inbox, limit=50):
        if self.fail:
            raise self.fail
        self.fetched = True
        return self.messages

    async def current_cursor(self, inbox):
        return self.cursor


@pytest.fixture
def fake_repo(monkeypatch):
    calls = {"cursor": [], "events": [], "cancel": [], "alerts": []}

    async def update_inbox_cursor(inbox_id, cursor):
        calls["cursor"].append(cursor)

    async def record_inbox_event(**kwargs):
        calls["events"].append(kwargs)
        return True

    async def match_by_ids(ids):
        return None

    async def match_by_email(address):
        return "lead-1" if address == "lead@example.com" else None

    async def cancel_sequence(lead_id, status, reason):
        calls["cancel"].append((lead_id, status))
        return 3

    monkeypatch.setattr(poller.repo, "update_inbox_cursor", update_inbox_cursor)
    monkeypatch.setattr(poller.repo, "record_inbox_event", record_inbox_event)
    monkeypatch.setattr(poller.repo, "match_lead_by_message_ids", match_by_ids)
    monkeypatch.setattr(poller.repo, "match_lead_by_email", match_by_email)
    async def send_reply_alert(inbox, lead_id, message):
        calls["alerts"].append((lead_id, message.from_email))
        return True

    monkeypatch.setattr(poller.repo, "cancel_sequence", cancel_sequence)
    monkeypatch.setattr(poller.alerts, "send_reply_alert", send_reply_alert)
    return calls


@pytest.mark.anyio
async def test_first_poll_skips_existing_mail(monkeypatch, fake_repo):
    reader = FakeReader(messages=[_message(5, "old@example.com")], cursor=118)
    monkeypatch.setattr(poller, "get_reader", lambda: reader)

    stats, error = await poller.poll_inbox(_inbox(0))

    assert (stats, error) == ({"tracking_started": 1}, None)
    assert fake_repo["cursor"] == [118]
    assert reader.fetched is False


@pytest.mark.anyio
async def test_empty_folder_polls_normally(monkeypatch, fake_repo):
    reader = FakeReader(cursor=0)
    monkeypatch.setattr(poller, "get_reader", lambda: reader)

    assert await poller.poll_inbox(_inbox(0)) == ({}, None)
    assert reader.fetched is True


@pytest.mark.anyio
async def test_unrelated_mail_is_neither_stored_nor_acted_on(monkeypatch, fake_repo):
    reader = FakeReader(messages=[
        _message(119, "newsletter@example.com"),
        _message(120, "lead@example.com"),
    ])
    monkeypatch.setattr(poller, "get_reader", lambda: reader)

    stats, error = await poller.poll_inbox(_inbox(118))

    assert error is None
    assert stats == {"unmatched": 1, "reply": 1}
    assert [e["from_email"] for e in fake_repo["events"]] == ["lead@example.com"]
    assert fake_repo["cancel"] == [("lead-1", "replied")]
    assert fake_repo["alerts"] == [("lead-1", "lead@example.com")]  # replies only
    assert fake_repo["cursor"] == [120]


@pytest.mark.anyio
async def test_failure_reason_is_reported(monkeypatch, fake_repo):
    reader = FakeReader(fail=MailError("IMAP auth failed for me@example.com: bad password"))
    monkeypatch.setattr(poller, "get_reader", lambda: reader)

    async def inboxes():
        return [_inbox(118)]

    monkeypatch.setattr(poller.repo, "active_inboxes", inboxes)
    result = await poller.run_once()

    assert result == {
        "error": 1,
        "errors": ["me@example.com: IMAP auth failed for me@example.com: bad password"],
    }
