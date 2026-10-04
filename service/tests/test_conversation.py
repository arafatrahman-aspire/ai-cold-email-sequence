"""Conversation context for the AI: quote stripping and thread formatting."""

from datetime import date

import pytest

from app.triage.classify import build_prompt
from app.triage.conversation import PER_MESSAGE_CHARS, format_history, strip_quoted
from app.triage.drafting import DraftInput, _prompt


@pytest.mark.parametrize("text,expected", [
    ("Tuesday works.\n\nOn Mon, 5 Oct 2026 at 10:00, Alex Rivera <alex@gmail.com> wrote:\n> Would Tuesday or Wednesday work?",
     "Tuesday works."),
    ("Yes please.\n\n-----Original Message-----\nFrom: Alex\nSent: Monday\nWould a call help?", "Yes please."),
    ("Sounds good\n\nFrom: Alex Rivera <alex@gmail.com>\nSent: Monday, October 5, 2026 10:00\nSubject: hi",
     "Sounds good"),
    ("Thanks\n> quoted line\n> another", "Thanks"),
    ("Merci.\n\nLe lun. 5 oct. 2026 à 10:00, Alex a écrit :\n> hello", "Merci."),
    ("No quotes at all.", "No quotes at all."),
    ("", ""),
])
def test_strip_quoted(text, expected):
    assert strip_quoted(text) == expected


def test_format_history_marks_direction_and_strips_quotes():
    history = [
        {"direction": "out", "subject": "audit evidence", "body": "Would a call help?", "at": "2026-10-01T09:00:00+00:00"},
        {"direction": "in", "subject": "Re: audit evidence", "at": "2026-10-02T14:30:00+00:00",
         "body": "Maybe, what does it cost?\n\nOn Thu, Alex wrote:\n> Would a call help?"},
        {"direction": "out", "subject": "Re: audit evidence", "body": "x" * 2000, "at": "2026-10-03T09:00:00+00:00"},
    ]
    text = format_history(history, lead_name="Dana Okafor", lead_timezone="Europe/London")
    assert text.index("[1] Us -> Dana Okafor, Thu 1 Oct 2026, 10:00") < text.index("[2] Dana Okafor -> us, Fri 2 Oct 2026, 15:30")
    assert "Maybe, what does it cost?" in text and "> Would a call help?" not in text
    assert "x" * PER_MESSAGE_CHARS + " ..." in text and "x" * (PER_MESSAGE_CHARS + 1) not in text


def test_prompts_carry_the_history():
    history = "[1] Us -> Dana\nSubject: hi\nWould Tuesday or Wednesday work?"
    p = build_prompt("Re: hi", "Tuesday works", lead_timezone="UTC", today=date(2026, 10, 5), history=history,
                     last_sent={"subject": "ignored", "body": "ignored when history is given"})
    assert "conversation so far" in p and "Would Tuesday or Wednesday work?" in p
    assert "ignored when history is given" not in p
    assert p.index("conversation so far") < p.index("Their NEW reply")

    d = DraftInput(kind="objection_reply", first_name="Dana", sign_off="Alex", reply_subject="Re: hi",
                   reply_body="Too expensive", slot_lines=[], history=history)
    dp = _prompt(d)
    assert dp.index("Would Tuesday or Wednesday work?") < dp.index("Their new reply")
    assert "conversation so far" not in _prompt(DraftInput(**{**d.__dict__, "history": None}))
