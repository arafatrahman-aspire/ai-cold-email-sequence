"""How emails look (app/mail/templates.py) and the design endpoints."""

import os
import re

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_PUBLIC_URL", "http://localhost:5173")
os.environ.setdefault("CMS_PUBLIC_URL", "http://localhost:8102")
os.environ.setdefault("SSO_CLIENT_SECRET", "s" * 40)

from app import main, repository as repo, settings_store  # noqa: E402
from app.mail import templates  # noqa: E402
from app.mail.base import Inbox  # noqa: E402

INBOX = Inbox("i", "alex@aspiretss.com", "Alex Rivera", "smtp", "INBOX_A", 15, 0)


def test_personal_email_has_signature_opt_out_and_safe_html():
    sig = templates.Signature(name="Alex Rivera", title="Sales", company="Aspire Tech",
                              website="aspiretss.com", phone="+1 555 0100")
    mail = templates.personal("Hi <Dana> & team,\n\nSee https://aspiretss.com/x.", sig,
                              sign_off="Best,", opt_out="Reply no thanks.")
    assert mail.text.endswith("Best,\nAlex Rivera\nSales | Aspire Tech\n+1 555 0100 | aspiretss.com\n\nReply no thanks.")
    assert "&lt;Dana&gt; &amp; team" in mail.html and "<Dana>" not in mail.html      # escaped
    assert '<a href="https://aspiretss.com/x"' in mail.html                          # linked, no trailing dot
    assert 'href="https://aspiretss.com"' in mail.html and "tel:+15550100" in mail.html
    assert "<img" not in mail.html                                                   # cold: no images


def test_branded_and_internal_layouts():
    doc = templates.branded(greeting="Hi Dana,", body_html="<p>x</p>", preheader="Pre", subject="S",
                            brand=templates.Branding(company="Aspire", address="1 Main St", accent="#ff0000"),
                            cta=("See pricing", "https://go/n/c/m/pricing"), unsubscribe_url="https://go/n/u/e")
    assert "Pre" in doc and "See pricing" in doc and "#ff0000" in doc and "1 Main St" in doc
    assert 'href="https://go/n/u/e"' in doc
    assert templates.accent_color("red; x") == templates.DEFAULT_ACCENT              # no CSS injection
    note = templates.internal(title="Dana replied", facts=[("From", "d@x"), ("Empty", "")], quote="<b>hi</b>")
    assert "Empty" not in note and "&lt;b&gt;hi&lt;/b&gt;" in note


@pytest.fixture
def client(monkeypatch):
    store = {"email_style": {}}

    async def get(key):
        if key in store:
            return store[key]
        return settings_store.DEFAULTS[key]

    async def set_value(key, value, description=None):
        store[key] = value

    async def inboxes():
        return [INBOX]

    monkeypatch.setattr(settings_store, "get", get)
    monkeypatch.setattr(settings_store, "set_value", set_value)
    monkeypatch.setattr(repo, "active_inboxes", inboxes)
    main.app.dependency_overrides[main.require_user] = lambda: {"email": "m@aspire.com", "role": "marketing"}
    yield TestClient(main.app), store
    main.app.dependency_overrides.clear()


def test_marketing_edits_the_design_and_previews_every_kind(client, monkeypatch):
    c, store = client
    r = c.put("/email-design", json={"title": "Account Executive", "company": "Aspire Tech", "accent": "#0f766e",
                                     "website": "aspiretss.com", "sign_off": "Thanks,", "opt_out": "Reply stop.", "html": True})
    assert r.status_code == 200 and store["email_style"]["title"] == "Account Executive"
    assert c.put("/email-design", json={"accent": "blue"}).status_code == 422

    cold = c.post("/email-design/render", json={"kind": "cold"}).json()
    assert "Account Executive" in cold["html"] and "#0f766e" in cold["html"] and "Alex Rivera" in cold["text"]
    assert cold["text"].endswith("Reply stop.")
    reply = c.post("/email-design/render", json={"kind": "reply", "body": "Hi Dana,\n\nSure.\n\nAlex"}).json()
    assert "Reply stop." not in reply["text"] and "Account Executive" in reply["text"]

    async def no_resources():
        return []

    monkeypatch.setattr("app.nurture.content.resources", no_resources)
    nurture = c.post("/email-design/render", json={"kind": "nurture"}).json()
    assert nurture["note"] == "" and "Unsubscribe" in nurture["html"] and "Book a short demo" in nurture["html"]
    for kind in ("handoff", "alert"):
        out = c.post("/email-design/render", json={"kind": kind}).json()
        assert out["html"].startswith("<!doctype html>") and out["subject"]

    store["email_style"] = {**store["email_style"], "html": False}
    assert c.post("/email-design/render", json={"kind": "cold"}).json()["html"] == ""


def _flips_in_dark_mode(html: str) -> bool:
    """Text colour is recoloured for dark mode through class "ink", the page
    through class "page"; a fixed white background without "page" under light
    dark-mode text was unreadable."""
    return 'class="ink"' in html and 'class="page"' in html and ".page {" in html.replace("{{", "{")


def test_plain_layout_is_an_ordinary_email():
    """Plain reads like a person's email but is built properly: a 600px
    table column, one font, a signature set off by a short brand-colour line.
    No page or text colour (the reader's client picks them, so dark mode just
    works), no <style> block, images or buttons."""
    sig = templates.Signature(name="Alex Rivera", title="Sales", company="Aspire Tech", website="aspiretss.com")
    mail = templates.personal("Hi Dana,\n\nHello.", sig, sign_off="Best,", opt_out="No thanks?", accent="#1a5fb4")
    html = mail.html
    assert 'max-width:600px' in html and "<style" not in html and "<img" not in html
    body = html.split("<body", 1)[1].split("<a ", 1)[0]
    assert "<body style=\"margin:0;padding:0\">" in html and not re.search(r"(?<!-)color:#", body)  # no text colour
    assert "background-color:#1a5fb4" in html                        # the signature line, nothing else
    assert "<b>Alex Rivera</b>" in html and "Sales | Aspire Tech" in html and "No thanks?" in html
    assert mail.text.endswith("Best,\nAlex Rivera\nSales | Aspire Tech\naspiretss.com\n\nNo thanks?")


def test_card_layout_follows_dark_mode():
    sig = templates.Signature(name="Alex Rivera", title="Sales", company="Aspire Tech")
    mail = templates.personal("Hi Dana,\n\nHello.", sig, sign_off="Best,", layout="card")
    assert _flips_in_dark_mode(mail.html)
    assert 'class="ink" style="font-weight:600' in mail.html and 'class="soft"' in mail.html


def test_card_layout_wraps_the_same_text_without_images_or_buttons():
    sig = templates.Signature(name="Alex Rivera", company="Aspire Tech")
    plain = templates.personal("Hi Dana,\n\nSee https://x.com/a.", sig, sign_off="Best,", accent="#d97706")
    card = templates.personal("Hi Dana,\n\nSee https://x.com/a.", sig, sign_off="Best,", accent="#d97706", layout="card")
    assert plain.text == card.text  # the plain-text part never changes
    assert 'class="card"' in card.html and "border-top:4px solid #d97706" in card.html
    assert "Aspire Tech" in card.html and 'class="card"' not in plain.html
    assert "<img" not in card.html and "background:#d97706" not in card.html  # no logo, no button
    assert templates.layout_name("card") == "card" and templates.layout_name("fancy") == "plain" == templates.layout_name(None)
