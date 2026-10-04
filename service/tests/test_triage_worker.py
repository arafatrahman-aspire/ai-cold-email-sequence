"""The triage worker end to end: real graph and plan, a scripted LLM, the
fake calendar, and an in-memory stand-in for the database calls."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import repository as repo
from app.calendar.factory import use_calendar
from app.calendar.fake import FakeCalendar
from app.llm.base import Completion
from app.mail.base import Inbox, SendResult
from app.workers import triage as worker

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)  # Monday 10:00 UTC
LEAD = {"id": "lead-1", "email": "dana@northwind.com", "first_name": "Dana", "last_name": "Okafor",
        "company": "Northwind", "job_title": "CISO", "timezone": "UTC", "lead_status": "New",
        "lead_exists": True, "status": "replied"}
INBOX = Inbox(id="inbox-1", email="alex@gmail.com", display_name="Alex Rivera", provider="smtp",
              credential_ref="INBOX_A", daily_cap=15, poll_cursor=0)
CFG = {"enabled": True, "auto_send_delay_minutes": 120, "auto_send_kinds": [
    "meeting_offer", "booking_confirmation", "slot_unavailable", "objection_reply",
    "not_now_ack", "referral_ack", "referral_ask"], "min_confidence": 0.7, "not_now_days": 60,
    "ooo_default_days": 7, "meeting": {"slots_to_offer": 2, "days_ahead": 7, "min_notice_hours": 12}}


@pytest.fixture
def anyio_backend():
    return "asyncio"


class ScriptedLLM:
    """Answers the classifier with ``verdict`` and drafts with a body that
    copies whatever time options it was given."""

    def __init__(self, verdict: dict):
        self.verdict = verdict
        self.prompts: list[str] = []

    async def complete_json(self, system, user, *, temperature):
        self.prompts.append(user)
        if "triage replies" in system:
            return Completion(json.dumps(self.verdict), "m", "test")
        options = []
        if "Time options" in user:
            block = user.split("Time options (copy exactly, one per line):\n", 1)[1].split("\n\n", 1)[0]
            options = block.splitlines()
        booked = ""
        if "The meeting is booked for " in user:
            booked = user.split("The meeting is booked for ", 1)[1].split(". Confirm", 1)[0]
        body = "Hi Dana,\n\nThanks.\n" + "\n".join(f"- {o}" for o in options) + (f"\nBooked: {booked}" if booked else "") + "\n\nAlex"
        return Completion(json.dumps({"body": body}), "m", "test")


@pytest.fixture
def db(monkeypatch):
    """Records every database call the worker makes."""
    state = {"calls": [], "drafts": [], "context": {"last_sent": None, "offered_slots": [], "has_meeting": False},
             "saved": None, "lead": dict(LEAD), "newer": False, "suppressed": set(), "finished": []}

    def record(name):
        async def fn(*args, **kwargs):
            state["calls"].append((name, args, kwargs))
            return {"cancel_lead_drafts": 0, "mark_dnc": True, "pause_sequence_until": 3,
                    "record_meeting": "meeting-1", "cancel_sequence": 2}.get(name)
        return fn

    for name in ("suppress", "cancel_sequence", "cancel_lead_drafts", "mark_dnc", "pause_sequence_until",
                 "snooze_lead", "record_meeting"):
        monkeypatch.setattr(repo, name, record(name))

    async def lead_for_step(lead_id):
        return dict(state["lead"])

    async def triage_context(lead_id):
        return state["context"]

    async def save_triage(event_id, category, confidence, summary, extracted, status, error=None):
        state["saved"] = {"category": category, "confidence": confidence, "extracted": extracted,
                          "status": status, "error": error}

    async def add_referral(*args):
        state["calls"].append(("add_referral", args, {}))
        return "enrolled", "contact-1"

    async def create_draft(event_id, lead_id, inbox_id, kind, to_email, subject, body, offered,
                           in_reply_to, references, auto_send_at):
        state["drafts"].append(dict(kind=kind, to=to_email, subject=subject, body=body, offered=offered,
                                    in_reply_to=in_reply_to, references=references, auto_send_at=auto_send_at))
        return f"draft-{len(state['drafts'])}"

    async def get_settings():
        return []

    for name, fn in (("lead_for_step", lead_for_step), ("triage_context", triage_context),
                     ("save_triage", save_triage), ("add_referral", add_referral),
                     ("create_draft", create_draft), ("get_settings", get_settings)):
        monkeypatch.setattr(repo, name, fn)
    return state


@pytest.fixture
def calendar():
    cal = FakeCalendar("UTC", "https://cal.example.com/demo/30min")
    use_calendar(cal)
    yield cal
    use_calendar(None)


def llm(monkeypatch, **verdict):
    verdict = {"confidence": 0.95, "summary": "s", **verdict}
    gateway = ScriptedLLM(verdict)
    monkeypatch.setattr("app.triage.classify.get_gateway", lambda: gateway)
    monkeypatch.setattr("app.triage.drafting.get_gateway", lambda: gateway)
    return gateway


def event(body="Sounds good", event_type="reply", human_category=None):
    return {"id": "ev-1", "inbox_id": "inbox-1", "lead_id": "lead-1", "event_type": event_type,
            "from_email": "dana@northwind.com", "subject": "Re: phishing simulations", "snippet": body,
            "message_id": "<their-msg@northwind.com>", "in_reply_to": "<our-msg@gmail.com>",
            "received_at": NOW.isoformat(), "human_category": human_category}


async def run(e=None):
    return await worker.process_event(e or event(), CFG, {"inbox-1": INBOX}, now=NOW)


def calls(db, name):
    return [c for c in db["calls"] if c[0] == name]


# --- interested -------------------------------------------------------------------

@pytest.mark.anyio
async def test_interested_gets_two_slots_and_the_link(monkeypatch, db, calendar):
    llm(monkeypatch, category="interested")
    assert await run() == "interested"
    [d] = db["drafts"]
    assert d["kind"] == "meeting_offer" and len(d["offered"]) == 2
    # 12h notice from Monday 10:00 -> first offer Tuesday; second on another day.
    starts = [datetime.fromisoformat(s["start"]) for s in d["offered"]]
    assert starts[0] >= NOW + timedelta(hours=12) and starts[0].date() != starts[1].date()
    assert "https://cal.example.com/demo/30min" in d["body"]
    assert "Tuesday 6 October, 10:00-10:30 (UTC)" in d["body"]
    # Threaded under their message, auto-sends in 2h within business hours.
    assert d["in_reply_to"] == "<their-msg@northwind.com>"
    assert d["references"] == ["<our-msg@gmail.com>", "<their-msg@northwind.com>"]
    assert d["auto_send_at"] == NOW + timedelta(hours=2)
    assert db["saved"]["status"] == "done"


@pytest.mark.anyio
async def test_picking_an_offered_slot_books_it(monkeypatch, db, calendar):
    offered = [s.to_json() for s in (await calendar.free_slots(NOW + timedelta(days=1), NOW + timedelta(days=3)))[:2]]
    db["context"]["offered_slots"] = offered
    llm(monkeypatch, category="interested", chosen_slot=2)
    await run(event("The second one works"))
    assert len(calendar.bookings) == 1
    booked_start = next(iter(calendar.bookings))
    assert booked_start.isoformat() == offered[1]["start"]
    [(_, args, _)] = calls(db, "record_meeting")
    assert args[0] == "lead-1" and args[2] == "fake"
    [d] = db["drafts"]
    assert d["kind"] == "booking_confirmation" and "Booked: Tuesday 6 October, 11:00-11:30 (UTC)" in d["body"]


@pytest.mark.anyio
async def test_taken_slot_offers_new_ones(monkeypatch, db, calendar):
    slots = await calendar.free_slots(NOW + timedelta(days=1), NOW + timedelta(days=3))
    db["context"]["offered_slots"] = [slots[0].to_json()]
    await calendar.book(slots[0].start, worker._attendee(LEAD, "someone@else.com"))
    llm(monkeypatch, category="interested", chosen_slot=1)
    await run()
    [d] = db["drafts"]
    assert d["kind"] == "slot_unavailable" and len(d["offered"]) == 2
    assert slots[0].start.isoformat() not in [s["start"] for s in d["offered"]]
    assert not calls(db, "record_meeting")


@pytest.mark.anyio
async def test_low_confidence_never_books_or_auto_sends(monkeypatch, db, calendar):
    db["context"]["offered_slots"] = [s.to_json() for s in (await calendar.free_slots(NOW + timedelta(days=1), NOW + timedelta(days=2)))[:2]]
    llm(monkeypatch, category="interested", chosen_slot=1, confidence=0.4)
    await run()
    assert not calendar.bookings
    [d] = db["drafts"]
    assert d["kind"] == "meeting_offer" and d["auto_send_at"] is None
    assert db["saved"]["status"] == "needs_human"


@pytest.mark.anyio
async def test_no_calendar_asks_for_times(monkeypatch, db):
    use_calendar(None)
    monkeypatch.setattr(worker, "get_calendar", lambda: None)
    llm(monkeypatch, category="interested")
    await run()
    [d] = db["drafts"]
    assert d["kind"] == "meeting_offer" and d["offered"] == []
    assert "offered 0 slot(s)" not in db["saved"]["extracted"]["actions"]
    assert db["saved"]["extracted"]["actions"][-1].startswith("meeting offer draft")


# --- the other categories ------------------------------------------------------

@pytest.mark.anyio
async def test_unsubscribe_everywhere(monkeypatch, db, calendar):
    llm(monkeypatch, category="unsubscribe")
    await run(event("Please remove me"))
    assert [c[0] for c in db["calls"]] == ["suppress", "cancel_sequence", "cancel_lead_drafts", "mark_dnc"]
    assert calls(db, "suppress")[0][1] == ("dana@northwind.com", "unsubscribed")
    assert db["drafts"] == []  # never reply to an unsubscribe
    assert "leads.status set to DNC" in db["saved"]["extracted"]["actions"]


@pytest.mark.anyio
async def test_not_now_snoozes_sixty_days(monkeypatch, db, calendar):
    llm(monkeypatch, category="not_now")
    await run(event("Check back next quarter"))
    [(_, args, _)] = calls(db, "snooze_lead")
    assert args[0] == "lead-1" and args[1] == NOW + timedelta(days=60)
    assert db["drafts"][0]["kind"] == "not_now_ack"


@pytest.mark.anyio
async def test_referral_is_enrolled(monkeypatch, db, calendar):
    body = "Not me. Try Priya Shah, priya.shah@northwind.com"
    llm(monkeypatch, category="wrong_person",
        referral={"name": "Priya Shah", "email": "priya.shah@northwind.com", "job_title": "IT Security Lead"})
    await run(event(body))
    [(_, args, _)] = calls(db, "add_referral")
    assert args[:5] == ("lead-1", "priya.shah@northwind.com", "Priya", "Shah", "IT Security Lead")
    assert args[7] == "Dana Okafor"
    assert db["drafts"][0]["kind"] == "referral_ack"


@pytest.mark.anyio
async def test_out_of_office_pauses_until_after_return(monkeypatch, db, calendar):
    llm(monkeypatch, category="out_of_office", return_date="2026-10-09")
    await run(event("Away until 9 October", event_type="auto_reply"))
    [(_, args, _)] = calls(db, "pause_sequence_until")
    assert args == ("lead-1", datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc))
    assert db["drafts"] == []


@pytest.mark.anyio
async def test_human_category_overrides_the_model(monkeypatch, db, calendar):
    llm(monkeypatch, category="objection")
    await run(event("Maybe later", human_category="not_now"))
    assert db["saved"]["category"] == "not_now" and calls(db, "snooze_lead")


@pytest.mark.anyio
async def test_failure_is_recorded_not_raised(monkeypatch, db, calendar):
    llm(monkeypatch, category="not_now")

    async def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(repo, "snooze_lead", boom)
    assert await run() == "failed"
    assert db["saved"]["status"] == "failed" and "db down" in db["saved"]["error"]


# --- sending drafts -----------------------------------------------------------------

@pytest.mark.anyio
async def test_send_due_drafts_checks_before_sending(monkeypatch, db):
    sent = []

    class Sender:
        async def send(self, inbox, message):
            sent.append(message)
            return SendResult(message_id="<sent-1@gmail.com>")

    drafts = [
        {"id": "d1", "lead_id": "lead-1", "inbox_id": "inbox-1", "to_email": "dana@northwind.com",
         "subject": "Re: hi", "body": "b", "in_reply_to": "<their@x>", "references_ids": ["<a>", "<their@x>"],
         "created_at": NOW.isoformat(), "status_before": "pending"},
        {"id": "d2", "lead_id": "lead-2", "inbox_id": "inbox-1", "to_email": "newer@x.com",
         "subject": "s", "body": "b", "in_reply_to": None, "references_ids": [],
         "created_at": NOW.isoformat(), "status_before": "pending"},
        {"id": "d3", "lead_id": "lead-3", "inbox_id": "inbox-1", "to_email": "blocked@x.com",
         "subject": "s", "body": "b", "in_reply_to": None, "references_ids": [],
         "created_at": NOW.isoformat(), "status_before": "approved"},
    ]
    finished = []

    async def claim(limit):
        return drafts

    async def newer(lead_id, since):
        return lead_id == "lead-2"

    async def suppressed(email):
        return email == "blocked@x.com"

    async def finish(draft_id, status, message_id=None, error=None):
        finished.append((draft_id, status, message_id))

    monkeypatch.setattr(repo, "claim_drafts_to_send", claim)
    monkeypatch.setattr(repo, "newer_reply_exists", newer)
    monkeypatch.setattr(repo, "is_suppressed", suppressed)
    monkeypatch.setattr(repo, "finish_draft", finish)
    monkeypatch.setattr(worker, "get_sender", lambda: Sender())

    stats = await worker.send_due_drafts({"inbox-1": INBOX})
    assert stats == {"sent": 1, "superseded": 1, "blocked": 1}
    assert finished == [("d1", "sent", "<sent-1@gmail.com>"), ("d2", "cancelled", None), ("d3", "cancelled", None)]
    [m] = sent
    assert (m.to_email, m.in_reply_to, m.references) == ("dana@northwind.com", "<their@x>", ["<a>", "<their@x>"])


@pytest.mark.anyio
async def test_ai_outage_defers_without_side_effects(monkeypatch, db, calendar):
    class Down:
        async def complete_json(self, *a, **k):
            raise RuntimeError("429 Too Many Requests")
    monkeypatch.setattr("app.triage.classify.get_gateway", lambda: Down())
    assert await run() == "deferred"
    # Nothing done, nothing saved: the claim lapses and it is retried later.
    assert db["calls"] == [] and db["drafts"] == [] and db["saved"] is None



@pytest.mark.anyio
async def test_a_persons_category_works_even_with_the_ai_down(monkeypatch, db, calendar):
    class Down:
        async def complete_json(self, system, user, *, temperature):
            raise RuntimeError("429 Too Many Requests")
    monkeypatch.setattr("app.triage.classify.get_gateway", lambda: Down())
    monkeypatch.setattr("app.triage.drafting.get_gateway", lambda: Down())
    assert await run(event("Maybe next year", human_category="not_now")) == "not_now"
    assert calls(db, "snooze_lead")
    # The reply falls back to the plain template.
    assert db["drafts"][0]["kind"] == "not_now_ack" and "check back" in db["drafts"][0]["body"]



@pytest.mark.anyio
async def test_both_ai_steps_see_the_conversation(monkeypatch, db, calendar):
    asked = {}

    async def history(lead_id, before, exclude_event=None, limit=4):
        asked.update(lead_id=lead_id, exclude_event=exclude_event, limit=limit)
        return [
            {"direction": "out", "subject": "phishing simulations", "body": "Would a 20-minute call help?",
             "at": "2026-10-01T09:00:00+00:00"},
            {"direction": "in", "subject": "Re: phishing simulations", "body": "What would it cost for 400 people?",
             "at": "2026-10-02T09:00:00+00:00"},
            {"direction": "out", "subject": "Re: phishing simulations", "body": "Around EUR 8 per seat per year.",
             "at": "2026-10-03T09:00:00+00:00"},
        ]
    monkeypatch.setattr(repo, "conversation_history", history)
    gateway = llm(monkeypatch, category="objection", objection="price")
    await run(event("Still too much for us.\n\nOn Sat, Alex wrote:\n> Around EUR 8 per seat"))

    assert asked == {"lead_id": "lead-1", "exclude_event": "ev-1", "limit": 4}
    classify_prompt, draft_prompt = gateway.prompts
    for p in (classify_prompt, draft_prompt):
        assert "What would it cost for 400 people?" in p and "Around EUR 8 per seat per year." in p
        assert "Dana Okafor -> us" in p and "Us -> Dana Okafor" in p
    # The new reply is passed without its quoted tail.
    assert "Still too much for us." in classify_prompt and "> Around EUR 8" not in classify_prompt


@pytest.mark.anyio
async def test_missing_history_function_falls_back(monkeypatch, db, calendar):
    async def missing(*a, **k):
        raise RuntimeError("conversation_history: HTTP 404 PGRST202")
    monkeypatch.setattr(repo, "conversation_history", missing)
    llm(monkeypatch, category="not_now")
    assert await run() == "not_now"
