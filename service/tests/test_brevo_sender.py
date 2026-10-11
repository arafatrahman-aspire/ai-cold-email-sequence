"""MAIL_SENDER=brevo: the request sent to Brevo, and how its answers are read."""

import json

import httpx
import pytest

from app.config import get_settings
from app.mail import factory
from app.mail.base import Inbox, MailError, OutgoingMessage
from app.mail.brevo_sender import BrevoSender
from app.nurture import sending

INBOX = Inbox(id="i1", email="sales@example.com", display_name="Arafat Rahman", provider="smtp",
              credential_ref="INBOX_A", daily_cap=None, poll_cursor=0)
MESSAGE = OutgoingMessage(to_email="lead@example.org", subject="Hello", body_text="Hi there",
                          in_reply_to="<first@x>", references=["<first@x>"],
                          unsubscribe_mailto="unsub@example.com", reply_to="replies@example.com")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def brevo(monkeypatch):
    monkeypatch.setenv("MAIL_SENDER", "brevo")
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sender(handler) -> tuple[BrevoSender, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    s = BrevoSender()
    s._client = httpx.AsyncClient(base_url="https://api.brevo.com", transport=httpx.MockTransport(record))
    return s, seen


@pytest.mark.anyio
async def test_sends_through_the_api(brevo):
    s, seen = _sender(lambda r: httpx.Response(201, json={"messageId": "<abc@smtp-relay.mailin.fr>"}))
    result = await s.send(INBOX, MESSAGE)

    assert result.message_id == "<abc@smtp-relay.mailin.fr>"  # stored, so replies thread on it
    [req] = seen
    assert req.url.path == "/v3/smtp/email" and req.headers["api-key"] == "xkeysib-test"
    body = json.loads(req.content)
    assert body["sender"] == {"email": "sales@example.com", "name": "Arafat Rahman"}
    assert body["to"] == [{"email": "lead@example.org"}]
    assert (body["subject"], body["textContent"]) == ("Hello", "Hi there")
    assert body["replyTo"] == {"email": "replies@example.com"}
    assert body["headers"]["In-Reply-To"] == "<first@x>"
    assert body["headers"]["List-Unsubscribe"] == "<mailto:unsub@example.com>"
    assert "htmlContent" not in body


@pytest.mark.anyio
@pytest.mark.parametrize("status,permanent", [(400, True), (401, True), (429, False), (502, False)])
async def test_errors(brevo, status, permanent):
    s, _ = _sender(lambda r: httpx.Response(status, json={"message": "nope"}))
    with pytest.raises(MailError) as err:
        await s.send(INBOX, MESSAGE)
    assert err.value.permanent is permanent


@pytest.mark.anyio
async def test_missing_key_and_dry_run(brevo, monkeypatch):
    s, seen = _sender(lambda r: httpx.Response(201, json={"messageId": "<x@y>"}))
    monkeypatch.setenv("BREVO_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(MailError) as err:
        await s.send(INBOX, MESSAGE)
    assert err.value.permanent

    monkeypatch.setenv("DRY_RUN", "true")
    get_settings.cache_clear()
    assert (await s.send(INBOX, MESSAGE)).message_id
    assert seen == []


def test_selected_by_mail_sender(brevo, monkeypatch):
    monkeypatch.setattr(factory, "_sender", None)
    monkeypatch.setattr(sending, "_transport", None)
    assert factory.get_sender().name == "brevo"
    assert sending.transport().name == "brevo"

    monkeypatch.setenv("MAIL_SENDER", "smtp")
    get_settings.cache_clear()
    monkeypatch.setattr(factory, "_sender", None)
    monkeypatch.setattr(sending, "_transport", None)
    assert factory.get_sender().name == "smtp"
    assert sending.transport().name == "smtp"
