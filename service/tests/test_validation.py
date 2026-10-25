import asyncio

import pytest

from app.graph.draft import _parse_emails
from app.graph.validate import validate_node


def _state(emails, attempts=1):
    return {
        "lead": {"id": "test"},
        "step_count": 4,
        "emails": emails,
        "draft_attempts": attempts,
        "validation_errors": [],
    }


PARAS = ("Most IT teams I talk to run phishing simulations by hand and chase the results into a "
         "spreadsheet afterwards, which eats most of a week.\n\n"
         "We automate that end to end, so a campaign takes minutes and the report writes itself. "
         "Would a short look next week be useful?")


def _good_email(n):
    # Laid out like a real email: a greeting line in email 1, short paragraphs.
    return {
        "step_number": n,
        "subject": f"phishing tests at northwind {n}",
        "body": ("Hi Dana,\n\n" if n == 1 else "") + PARAS,
    }


def _run(state):
    return asyncio.run(validate_node(state))


def test_valid_sequence_passes():
    result = _run(_state([_good_email(i) for i in range(1, 5)]))
    assert result["status"] == "ok"
    assert result["validation_errors"] == []


def test_wrong_count_fails():
    result = _run(_state([_good_email(i) for i in range(1, 4)]))
    assert "status" not in result  # routes back to draft for a retry
    assert any("expected 4 emails" in e for e in result["validation_errors"])


def test_empty_body_fails():
    emails = [_good_email(i) for i in range(1, 5)]
    emails[2]["body"] = ""
    result = _run(_state(emails))
    assert any("body is empty" in e for e in result["validation_errors"])


def test_overlong_body_fails():
    emails = [_good_email(i) for i in range(1, 5)]
    emails[0]["body"] = "x" * 5000
    result = _run(_state(emails))
    assert any("too long" in e for e in result["validation_errors"])


def test_placeholder_is_rejected():
    emails = [_good_email(i) for i in range(1, 5)]
    emails[1]["body"] = "Hi [First Name], " + "x" * 400
    result = _run(_state(emails))
    assert any("placeholder" in e for e in result["validation_errors"])


def test_duplicate_subjects_rejected():
    emails = [_good_email(i) for i in range(1, 5)]
    emails[3]["subject"] = emails[0]["subject"]
    result = _run(_state(emails))
    assert any("duplicates" in e for e in result["validation_errors"])


def test_spam_words_in_subject_rejected():
    emails = [_good_email(i) for i in range(1, 5)]
    emails[0]["subject"] = "free security audit guaranteed"
    result = _run(_state(emails))
    assert any("spam-trigger" in e for e in result["validation_errors"])


def test_second_failure_goes_to_manual_review():
    result = _run(_state([], attempts=2))
    assert result["status"] == "manual_review"


def test_parse_emails_tolerates_markdown_fence():
    raw = '```json\n{"emails": [{"step_number": 1, "subject": "a", "body": "b"}]}\n```'
    emails = _parse_emails(raw, 4)
    assert emails == [{"step_number": 1, "subject": "a", "body": "b"}]


def test_parse_emails_renumbers():
    raw = '{"emails": [{"step_number": 3, "subject": "a", "body": "b"},' \
          ' {"step_number": 3, "subject": "c", "body": "d"}]}'
    emails = _parse_emails(raw, 4)
    assert [e["step_number"] for e in emails] == [1, 2]


def test_parse_emails_rejects_non_list():
    with pytest.raises(ValueError):
        _parse_emails('{"emails": "not a list"}', 4)


@pytest.mark.parametrize(
    "snippet",
    [
        "Hi [First Name], ",
        "at {{company}} we ",
        "regarding [Company Name] and ",
        "your <role> at ",
        "Lorem ipsum dolor ",
        "the TBD programme ",
    ],
)
def test_placeholder_variants_rejected(snippet):
    emails = [_good_email(i) for i in range(1, 5)]
    emails[0]["body"] = snippet + "x" * 400
    result = _run(_state(emails))
    assert any("placeholder" in e for e in result["validation_errors"]), snippet


def test_clean_body_has_no_false_placeholder():
    emails = [_good_email(i) for i in range(1, 5)]
    emails[0]["body"] = (
        "Hi Dana,\n\nMost teams we speak to run one phishing simulation a year and chase "
        "completions by hand afterwards, which leaves little time for anything else.\n\n"
        "Worth comparing notes on how others handle it? A short call would do."
    )
    result = _run(_state(emails))
    assert result["status"] == "ok"


def test_one_block_of_text_or_a_missing_greeting_is_rejected():
    no_greeting = [_good_email(i) for i in range(1, 5)]
    no_greeting[0]["body"] = "Dana, " + PARAS
    errors = _run(_state(no_greeting))["validation_errors"]
    assert errors and "greeting" in errors[0]

    wall = [_good_email(i) for i in range(1, 5)]
    wall[1]["body"] = PARAS.replace("\n\n", " ")
    errors = _run(_state(wall))["validation_errors"]
    assert errors == ["email 2: write the body as 2-3 short paragraphs separated by a blank line, "
                      "each under 70 words, not one block of text"]
