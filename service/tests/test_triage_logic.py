"""Reply triage: classifier parsing, decision rules, drafting guarantees, timing."""

from datetime import date, datetime, timezone

import pytest

from app.settings_store import BusinessHours
from app.triage import drafting
from app.triage.classify import build_prompt, parse_result
from app.triage.drafting import DraftInput, reply_subject, template, write_draft
from app.triage.plan import decide
from app.workers.triage import auto_send_time, return_time

HOURS = BusinessHours(start_hour=9, end_hour=17, weekdays=(0, 1, 2, 3, 4))


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- parsing the classifier ------------------------------------------------

def test_parse_valid_result_and_details():
    body = "Not me, try Priya at priya.shah@northwind.com"
    r = parse_result(
        '{"category": "wrong_person", "confidence": 0.93, "summary": "Points to Priya",'
        ' "referral": {"name": "Priya Shah", "email": "priya.shah@northwind.com", "job_title": null},'
        ' "return_date": null, "chosen_slot": null, "proposed_time": null, "objection": null}',
        body,
    )
    assert (r.category, r.confidence, r.summary) == ("wrong_person", 0.93, "Points to Priya")
    assert r.extracted == {"referral": {"name": "Priya Shah", "email": "priya.shah@northwind.com", "job_title": None}}


def test_invented_referral_email_is_dropped():
    r = parse_result('{"category": "wrong_person", "confidence": 0.9, '
                     '"referral": {"name": "Jane Ortiz", "email": "jane.ortiz@company.com"}}',
                     "Jane Ortiz took over, speak to her.")
    assert r.extracted["referral"] == {"name": "Jane Ortiz", "email": None, "job_title": None}


def test_details_are_validated():
    r = parse_result('```json\n{"category": "interested", "confidence": 7, "chosen_slot": 2,'
                     ' "proposed_time": "2026-10-08T15:00:00+00:00", "return_date": "not a date"}\n```')
    assert r.confidence == 1.0
    assert r.extracted == {"chosen_slot": 2, "proposed_time": "2026-10-08T15:00:00+00:00"}
    # A time without a UTC offset is ambiguous, so it is dropped.
    assert "proposed_time" not in parse_result('{"category": "interested", "proposed_time": "2026-10-08T15:00"}').extracted
    assert "chosen_slot" not in parse_result('{"category": "interested", "chosen_slot": 0}').extracted


@pytest.mark.parametrize("text", ["not json", "[1, 2]", '{"category": "maybe"}'])
def test_unusable_output_becomes_other(text):
    assert parse_result(text).category == "other"


def test_prompt_carries_context():
    p = build_prompt("Re: hi", "Option 2", lead_timezone="Asia/Dhaka", today=date(2026, 10, 5),
                     last_sent={"subject": "hi", "body": "Shall we talk?"},
                     offered=["Tuesday 6 October, 10:00", "Wednesday 7 October, 14:00"],
                     forced_category="interested")
    assert "Asia/Dhaka" in p and "2026-10-05 (Monday)" in p
    assert "1. Tuesday 6 October, 10:00" in p and "2. Wednesday 7 October, 14:00" in p
    assert 'category is "interested"' in p and "Shall we talk?" in p


# --- decision rules ----------------------------------------------------------

def plan(category, confidence=0.95, extracted=None, **kw):
    return decide(category, confidence, extracted or {}, min_confidence=0.7, **kw)


def test_unsubscribe_everywhere_only_when_sure():
    p = plan("unsubscribe")
    assert p.actions == ["suppress", "cancel_drafts", "mark_dnc"] and p.draft_kind is None
    unsure = plan("unsubscribe", 0.4)
    assert unsure.actions == [] and unsure.needs_human
    # The opt-out keyword detector is decisive whatever the model says.
    assert plan("interested", 0.3, event_type="unsubscribe").actions == ["suppress", "cancel_drafts", "mark_dnc"]


def test_auto_reply_is_out_of_office():
    p = plan("objection", 0.2, event_type="auto_reply")
    assert p.category == "out_of_office" and p.actions == ["pause_ooo"] and not p.needs_human


def test_not_now_snoozes_and_acknowledges():
    assert (plan("not_now").actions, plan("not_now").draft_kind) == (["snooze"], "not_now_ack")
    unsure = plan("not_now", 0.5)
    assert unsure.actions == [] and unsure.needs_human


def test_wrong_person():
    with_email = plan("wrong_person", extracted={"referral": {"name": "Priya", "email": "p@x.com"}})
    assert (with_email.actions, with_email.draft_kind) == (["add_referral"], "referral_ack")
    name_only = plan("wrong_person", extracted={"referral": {"name": "Jane", "email": None}})
    assert (name_only.actions, name_only.draft_kind, name_only.needs_human) == ([], "referral_ask", False)


def test_interested_paths():
    assert plan("interested").actions == ["offer_slots"]
    pick = plan("interested", extracted={"chosen_slot": 2}, offered_count=2)
    assert (pick.actions, pick.draft_kind) == (["book_offered"], "booking_confirmation")
    assert plan("interested", extracted={"chosen_slot": 3}, offered_count=2).actions == ["offer_slots"]
    assert plan("interested", extracted={"proposed_time": "2026-10-08T15:00:00+00:00"}).actions == ["book_proposed"]
    # Never book on a low-confidence read; offer times and wait for a person.
    low = plan("interested", 0.5, extracted={"chosen_slot": 1}, offered_count=2)
    assert (low.actions, low.needs_human) == (["offer_slots"], True)
    assert plan("interested", has_meeting=True).draft_kind is None


def test_other_and_objection():
    assert plan("other").needs_human
    assert plan("objection").draft_kind == "objection_reply"


# --- drafting guarantees -------------------------------------------------------

def _input(**kw):
    base = dict(kind="meeting_offer", first_name="Dana", sign_off="Alex", reply_subject="Re: Re: hi",
                reply_body="Sounds good", slot_lines=["Tuesday 6 October, 10:00-10:30 (UTC)",
                                                      "Wednesday 7 October, 14:00-14:30 (UTC)"],
                booking_link="https://cal.com/alex/30min")
    base.update(kw)
    return DraftInput(**base)


def test_reply_subject():
    assert reply_subject("Re: RE: phishing") == "Re: phishing"
    assert reply_subject("") == "Re: our conversation"


@pytest.mark.parametrize("kind", drafting.KINDS)
def test_every_kind_has_a_template(kind):
    text = template(_input(kind=kind, booked="Tuesday 6 October, 10:00-10:30 (UTC)", referral="Priya"))
    assert text.startswith("Hi Dana,") and text.endswith("Alex")
    assert not drafting._PLACEHOLDER.search(text)


def test_ensure_adds_link_and_rejects_missing_slots():
    d = _input()
    good = "Hi Dana,\n\n- Tuesday 6 October, 10:00-10:30 (UTC)\n- Wednesday 7 October, 14:00-14:30 (UTC)\n\nAlex"
    assert d.booking_link in drafting._ensure(good, d)
    assert drafting._ensure("Hi Dana, how about Tuesday?\n\nAlex", d) is None
    assert drafting._ensure("Hi [Name], - Tuesday 6 October, 10:00-10:30 (UTC)", d) is None


class _Gateway:
    def __init__(self, text=None, fail=False):
        self.text, self.fail = text, fail

    async def complete_json(self, system, user, *, temperature):
        if self.fail:
            raise RuntimeError("provider down")
        from app.llm.base import Completion
        return Completion(self.text, "m", "p")


@pytest.mark.anyio
async def test_write_draft_uses_ai_text_or_falls_back(monkeypatch):
    d = _input()
    ok = '{"body": "Hi Dana,\\n\\nGlad to hear it.\\n- Tuesday 6 October, 10:00-10:30 (UTC)\\n- Wednesday 7 October, 14:00-14:30 (UTC)\\n\\nAlex"}'
    monkeypatch.setattr(drafting, "get_gateway", lambda: _Gateway(ok))
    subject, body, source = await write_draft(d)
    assert (subject, source) == ("Re: hi", "ai") and "https://cal.com/alex/30min" in body

    monkeypatch.setattr(drafting, "get_gateway", lambda: _Gateway('{"body": "How about Tuesday?"}'))
    assert (await write_draft(d))[2] == "template"
    monkeypatch.setattr(drafting, "get_gateway", lambda: _Gateway(fail=True))
    _, body, source = await write_draft(d)
    assert source == "template" and "Wednesday 7 October, 14:00-14:30 (UTC)" in body


# --- timing --------------------------------------------------------------------

def test_auto_send_lands_in_business_hours():
    fri_evening = datetime(2026, 10, 2, 16, 30, tzinfo=timezone.utc)  # Friday
    assert auto_send_time(fri_evening, 120, "UTC", HOURS) == datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)
    mon_morning = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)
    assert auto_send_time(mon_morning, 120, "UTC", HOURS) == datetime(2026, 10, 5, 11, 0, tzinfo=timezone.utc)


def test_out_of_office_resume_time():
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    # Back Friday 9 Oct -> resume the next business day, Monday 12 Oct 09:00.
    assert return_time("2026-10-09", now, "UTC", HOURS, 7) == datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    # No date: a week later, on a business day.
    assert return_time(None, now, "UTC", HOURS, 7) == datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


def test_referral_reaches_the_sequence_prompt():
    from app.graph.prompts import build_user_prompt
    lead = {"first_name": "Priya", "company": "Northwind", "job_title": "IT Security Lead"}
    assert "Referred by" not in build_user_prompt("it", lead, 4)
    assert "- Referred by: Dana Okafor, who said" in build_user_prompt("it", {**lead, "referred_by": "Dana Okafor"}, 4)


def test_no_slots_prompt_asks_for_times():
    prompt = drafting._prompt(_input(slot_lines=[], booking_link=None))
    assert "which days and times suit" in prompt and "Time options" not in prompt
