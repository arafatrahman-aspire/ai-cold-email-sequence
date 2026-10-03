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


def _good_email(n):
    return {
        "step_number": n,
        "subject": f"phishing tests at northwind {n}",
        "body": "x" * 400,
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
        "Most teams we speak to run one phishing simulation a year and chase "
        "completions by hand afterwards. Worth comparing notes? " + "x" * 300
    )
    result = _run(_state(emails))
    assert result["status"] == "ok"
