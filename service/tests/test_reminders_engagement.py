"""Day-before meeting reminders and the Brevo engagement sync."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import repository as repo
from app import settings_store
from app.mail.base import Inbox, SendResult
from app.workers import engagement, reminders

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
INBOX = Inbox("inbox-1", "sales@aspiretss.com", "Aspire Sales", "smtp", "INBOX_B", 50, 0)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Sender:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send(self, inbox, message):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append(message)
        return SendResult(message_id="<m@x>")


@pytest.fixture
def world(monkeypatch):
    state = {"marks": [], "due": [], "sender": Sender()}

    async def triage_config():
        return {"meeting": {"reminder_hours": 24}}

    async def email_style():
        return {"sign_off": "Best,", "accent": "#2563eb", "html": True, "layout": "plain"}

    async def due(until):
        state["until"] = until
        return state["due"]

    async def mark(mid, sent):
        state["marks"].append((mid, sent))
        return True

    async def inboxes():
        return [INBOX]

    async def lead_for_step(lead_id):
        return {"first_name": "Dana", "timezone": "Asia/Dhaka"}

    monkeypatch.setattr(settings_store, "triage_config", triage_config)
    monkeypatch.setattr(settings_store, "email_style", email_style)
    monkeypatch.setattr(repo, "meetings_due_reminder", due)
    monkeypatch.setattr(repo, "mark_meeting_reminded", mark)
    monkeypatch.setattr(repo, "active_inboxes", inboxes)
    monkeypatch.setattr(repo, "lead_for_step", lead_for_step)
    monkeypatch.setattr(reminders, "get_sender", lambda: state["sender"])
    return state


def meeting(start, created, mid="m-1"):
    return {"id": mid, "lead_id": "lead-1", "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=30)).isoformat(),
            "attendee_email": "dana@northwind.com", "attendee_name": "Dana Okafor",
            "meeting_url": "https://teams.microsoft.com/l/meetup-join/x", "created_at": created.isoformat()}


@pytest.mark.anyio
async def test_reminder_goes_out_the_day_before_in_the_leads_time(world):
    world["due"] = [meeting(NOW + timedelta(hours=20), NOW - timedelta(days=3))]
    assert await reminders.run_once(now=NOW) == {"sent": 1}
    assert world["until"] == NOW + timedelta(hours=24)
    [m] = world["sender"].sent
    assert m.to_email == "dana@northwind.com" and m.subject.startswith("Reminder: our call Tuesday")
    assert "Hi Dana," in m.body_text and "(Asia/Dhaka)" in m.body_text      # 06:00 UTC -> 12:00 Dhaka
    assert "12:00-12:30" in m.body_text and "teams.microsoft.com" in m.body_text
    assert "reply with one that does" in m.body_text and m.body_html
    assert world["marks"] == [("m-1", True)]


@pytest.mark.anyio
async def test_no_reminder_for_a_meeting_booked_inside_the_window(world):
    world["due"] = [meeting(NOW + timedelta(hours=20), NOW - timedelta(hours=2))]
    assert await reminders.run_once(now=NOW) == {"booked_recently": 1}
    assert not world["sender"].sent and world["marks"] == [("m-1", True)]   # never again


@pytest.mark.anyio
async def test_a_failed_reminder_is_released_for_the_next_run(world):
    world["sender"] = Sender(fail=True)
    world["due"] = [meeting(NOW + timedelta(hours=20), NOW - timedelta(days=3))]
    assert await reminders.run_once(now=NOW) == {"failed": 1}
    assert world["marks"] == [("m-1", True), ("m-1", False)]


@pytest.mark.anyio
async def test_reminders_off(world, monkeypatch):
    async def off():
        return {"meeting": {"reminder_hours": 0}}
    monkeypatch.setattr(settings_store, "triage_config", off)
    world["due"] = [meeting(NOW + timedelta(hours=20), NOW - timedelta(days=3))]
    assert await reminders.run_once(now=NOW) == {}


# --- Brevo engagement -------------------------------------------------------------

@pytest.mark.anyio
async def test_engagement_stores_events_and_stops_bounces_and_spam(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "mail_sender", "brevo")
    monkeypatch.setattr(get_settings(), "brevo_api_key", "xkeysib-test")
    events = [
        {"email": "dana@northwind.com", "date": "2026-10-05T09:00:00.000+02:00", "messageId": "<a@relay>", "event": "delivered"},
        {"email": "dana@northwind.com", "date": "2026-10-05T09:05:00.000+02:00", "messageId": "<a@relay>", "event": "opened"},
        {"email": "dana@northwind.com", "date": "2026-10-05T09:06:00.000+02:00", "messageId": "<a@relay>", "event": "clicks", "link": "https://aspiretss.com/x"},
        {"email": "gone@acme.com", "date": "2026-10-05T09:00:00Z", "messageId": "<b@relay>", "event": "hardBounces", "reason": "550 no such user"},
        {"email": "angry@acme.com", "date": "2026-10-05T09:00:00Z", "messageId": "<c@relay>", "event": "spam"},
        {"email": "x@y.com", "date": "2026-10-05T09:00:00Z", "messageId": "<d@relay>", "event": "requests"},  # skipped
        {"email": "someone@else.com", "date": "2026-10-05T09:00:00Z", "messageId": "<not-ours>", "event": "opened"},
    ]
    seen_requests = []

    def brevo(request):
        seen_requests.append(request)
        assert request.headers["api-key"] == "xkeysib-test" and request.url.path == "/v3/smtp/statistics/events"
        return httpx.Response(200, json={"events": events})

    stored, actions = [], []
    leads = {"<a@relay>": "lead-a", "<b@relay>": "lead-b", "<c@relay>": "lead-c"}

    async def record(message_id, email, event, link, reason, at):
        if message_id not in leads:
            return {"outcome": "unknown"}
        key = (message_id, event, at)
        if key in stored:
            return {"outcome": "seen", "lead_id": leads[message_id]}
        stored.append(key)
        return {"outcome": "new", "lead_id": leads[message_id], "source": "cold"}

    async def suppress(email, reason):
        actions.append(("suppress", email, reason))

    async def cancel_sequence(lead_id, status, reason):
        actions.append(("cancel", lead_id, status))
        return 1

    async def mark_dnc(lead_id):
        actions.append(("dnc", lead_id))
        return True

    for name, fn in (("record_email_event", record), ("suppress", suppress),
                     ("cancel_sequence", cancel_sequence), ("mark_dnc", mark_dnc)):
        monkeypatch.setattr(repo, name, fn)

    stats = await engagement.run_once(transport=httpx.MockTransport(brevo))
    assert stats == {"delivered": 1, "opened": 1, "clicked": 1, "hard_bounce": 1, "stopped_bounce": 1,
                     "spam": 1, "stopped_spam": 1}
    assert ("suppress", "gone@acme.com", "hard_bounce") in actions and ("cancel", "lead-b", "bounced") in actions
    assert ("suppress", "angry@acme.com", "complaint") in actions and ("dnc", "lead-c") in actions
    assert not any(a[1] == "lead-a" for a in actions)                  # opens never stop anyone

    # The next run sees the same events: nothing is stored or done twice.
    actions.clear()
    assert await engagement.run_once(transport=httpx.MockTransport(brevo)) == {}
    assert actions == []
