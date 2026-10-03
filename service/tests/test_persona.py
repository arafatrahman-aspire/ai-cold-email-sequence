import pytest

from app.graph.persona import keyword_persona


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Chief Information Security Officer", "ciso"),
        ("CISO", "ciso"),
        ("Head of Risk & Compliance", "ciso"),
        ("Data Protection Officer", "ciso"),
        ("VP, Information Security", "ciso"),
        ("IT Infrastructure Manager", "it"),
        ("Systems Administrator", "it"),
        ("Head of IT", "it"),
        ("CIO", "it"),
        ("Service Desk Lead", "it"),
        ("Head of People Operations", "hr"),
        ("HR Director", "hr"),
        ("Talent Acquisition Manager", "hr"),
        ("Learning and Development Lead", "hr"),
        ("CHRO", "hr"),
    ],
)
def test_keyword_routing(title, expected):
    assert keyword_persona(title) == expected


def test_security_beats_training():
    # "Security Awareness Training Manager" contains both "security" and
    # "training"; the security rule must win.
    assert keyword_persona("Security Awareness Training Manager") == "ciso"


def test_unmatched_returns_none():
    assert keyword_persona("Regional Sales Director") is None
    assert keyword_persona("") is None
    assert keyword_persona(None) is None
