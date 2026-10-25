"""Email Nurture calendar dates and hand-off reasons (app.nurture.schedule)."""

from datetime import datetime, timedelta, timezone

from app.nurture import options, schedule

MON_10 = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)  # a Monday


def _cfg(**over):
    return options.merge({"default_timezone": "UTC", **over})


def test_planned_dates_follow_the_cadence_and_skip_written_steps():
    cfg = _cfg()
    live = {"enrollment_id": "e1", "lead_id": "l1", "persona": "ciso", "temperature": "warm", "step": 1,
            "written": 2, "started_at": MON_10.isoformat(), "next_send_at": None, "test_mode": False,
            "timezone": "UTC", "email": "a@x"}
    written = {"message_id": "m2", "enrollment_id": "e1", "step": 2, "status": "ready",
               "at": (MON_10 + timedelta(days=3)).isoformat(), "subject": "Two"}
    items = schedule.calendar({"messages": [written], "live": [live]}, cfg, MON_10, MON_10 + timedelta(days=40))

    assert [(i["step"], i["planned"]) for i in items] == [(2, False), (3, True), (4, True), (5, True), (6, True)]
    warm = cfg["cadence"]["warm"]  # [0, 3, 7, 12, 19, 28]
    third = datetime.fromisoformat(items[1]["at"])
    assert third.date() == (MON_10 + timedelta(days=warm[2])).date()  # Monday + 7 = a Monday, in window
    # Only what falls inside the range.
    short = schedule.calendar({"messages": [], "live": [live]}, cfg, MON_10, MON_10 + timedelta(days=8))
    assert [i["step"] for i in short] == [3]


def test_the_next_email_uses_its_stored_date():
    cfg = _cfg()
    nxt = MON_10 + timedelta(days=4, hours=2)
    live = {"enrollment_id": "e1", "persona": "it", "temperature": "cold", "step": 2, "written": 2,
            "started_at": MON_10.isoformat(), "next_send_at": nxt.isoformat(), "test_mode": False, "timezone": None}
    items = schedule.calendar({"live": [live]}, cfg, MON_10, MON_10 + timedelta(days=6))
    assert [(i["step"], i["at"]) for i in items] == [(3, nxt.isoformat())]


def test_signals_say_why_in_plain_words():
    row = {"tier": "Warm",
           "clicks": [{"step": "2", "link": "pricing"}, {"step": "4", "link": "pricing"},
                      {"step": "3", "link": "resource", "resource_id": "r1"}],
           "replies": [{"category": "interested", "summary": "Asks for a quote"}]}
    out = schedule.signals(row, "pricing_click", [{"id": "r1", "title": "Phishing ROI guide"}])
    assert out == [
        "They clicked the pricing link",
        "Clicked the pricing link twice (email 2, 4)",
        "Opened “Phishing ROI guide”",
        "Replied (interested): Asks for a quote",
        "Lead score now: Warm",
    ]
    assert schedule.signals({}, "manual", []) == ["Handed off by a person"]


def test_campaign_banned_claims_fail_validation(monkeypatch):
    import asyncio

    from app.graph import validate

    async def setting(key):
        return {"llm_min_body_chars": 10, "llm_max_body_chars": 2000}[key]

    monkeypatch.setattr(validate, "get_setting", setting)
    body = ("Hi Dana,\n\nWe make audits painless for every team you run.\n\n"
            "We offer guaranteed compliance, too.")
    state = {"step_count": 1, "draft_attempts": 2,
             "lead": {"playbook": {"banned_claims": ["Guaranteed compliance"]}},
             "emails": [{"step_number": 1, "subject": "audit season", "body": body}]}
    out = asyncio.run(validate.validate_node(state))
    assert out["status"] == "manual_review" and "the campaign bans: guaranteed compliance" in out["error"]
    general = asyncio.run(validate.validate_node({**state, "lead": {}}))
    assert general["status"] == "ok"
