"""The background scheduler: replies are checked at startup, then every 10 min."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.workers import scheduler


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_reply_check_runs_at_start_then_every_ten_minutes(monkeypatch):
    calls = {"poller": 0}

    async def poll():
        calls["poller"] += 1

    async def idle():
        return None

    monkeypatch.setattr(scheduler.poller, "run_once", poll)
    for name in ("intake", "sender", "triage"):
        monkeypatch.setattr(getattr(scheduler, name), "run_once", idle)
    for module, fn in ((scheduler.nurture_enroll, "score_tick"), (scheduler.nurture_enroll, "reconcile_tick"),
                       (scheduler.nurture_jobs, "generate_tick"), (scheduler.nurture_jobs, "send_tick"),
                       (scheduler.nurture_replies, "poll_tick")):
        monkeypatch.setattr(module, fn, idle)

    scheduler.start()
    try:
        await asyncio.sleep(0.3)
        assert calls["poller"] == 1, "the reply check should run right away"

        status = scheduler.status()
        assert status["poller"]["interval_seconds"] == 600
        next_run = datetime.fromisoformat(status["poller"]["next_run"])
        wait = (next_run - datetime.now(timezone.utc)).total_seconds()
        assert 590 <= wait <= 600, f"next check should be ~10 min away, got {wait:.0f}s"
        assert set(status) == {"intake", "sender", "poller", "triage", "calendar", "nurture_score",
                               "nurture_reconcile", "nurture_write", "nurture_send", "nurture_mailbox"}
    finally:
        scheduler.shutdown()
    assert scheduler.status() == {}
