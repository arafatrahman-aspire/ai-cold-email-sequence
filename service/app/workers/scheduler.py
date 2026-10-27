"""APScheduler wiring for the workers (intake, sender, poller, triage, calendar,
and Email Nurture's own jobs).

This is the cron layer. It lives here rather than in Supabase Edge Functions
because the send and poll paths need SMTP and IMAP, which Deno cannot provide.
Each job is single-instance: a slow tick delays the next one rather than
overlapping with it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.nurture import alerts as nurture_alerts
from app.nurture import enroll as nurture_enroll
from app.nurture import jobs as nurture_jobs
from app.nurture import options as nurture_options
from app.nurture import replies as nurture_replies
from app.workers import calendar_sync, engagement, intake, poller, reminders, sender, triage

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _guard(name: str, coro_fn):
    async def _run() -> None:
        try:
            await coro_fn()
        except Exception:
            # A worker crash must never kill the scheduler thread.
            log.exception("%s worker tick failed", name)

    _run.__name__ = f"{name}_tick"
    return _run


# Poll results that are not new mail.
_NOT_MAIL = {"error", "errors", "tracking_started", "skipped"}


def _then_triage(coro_fn):
    """Run a mailbox poll, then triage at once if it brought in mail, so a
    reply does not wait for triage's own (long) interval."""
    async def _run() -> dict:
        stats = await coro_fn() or {}
        if _scheduler is not None and set(stats) - _NOT_MAIL:
            _scheduler.modify_job("triage", next_run_time=datetime.now(timezone.utc))
        return stats

    return _run


# A job must fail this many runs in a row before anyone is emailed: one slow
# database answer is retried a minute later and needs no human.
ALERT_AFTER_FAILURES = 3
_failures: dict[str, int] = {}


def _guard_nurture(name: str, coro_fn):
    """Like _guard; repeated failures also email the nurture alert address."""
    async def _run() -> None:
        try:
            await coro_fn()
            _failures[name] = 0
        except Exception as exc:
            _failures[name] = _failures.get(name, 0) + 1
            log.exception("%s worker tick failed (%d in a row)", name, _failures[name])
            if _failures[name] < ALERT_AFTER_FAILURES:
                return
            try:
                await nurture_alerts.job_failed(await nurture_options.load(), name, exc, _failures[name])
            except Exception:
                log.exception("could not send the %s failure alert", name)

    _run.__name__ = f"{name}_tick"
    return _run


def _add_nurture_jobs(s) -> None:
    now = datetime.now(timezone.utc)
    for job_id, fn, seconds, first in (
        ("nurture_score", nurture_enroll.score_tick, s.nurture_enroll_interval_seconds, now),
        ("nurture_reconcile", nurture_enroll.reconcile_tick, s.nurture_reconcile_interval_seconds, None),
        ("nurture_write", nurture_jobs.generate_tick, s.nurture_generate_interval_seconds, None),
        ("nurture_send", nurture_jobs.send_tick, s.nurture_send_interval_seconds, None),
        ("nurture_mailbox", _then_triage(nurture_replies.poll_tick), s.nurture_poll_interval_seconds, None),
    ):
        _scheduler.add_job(
            _guard_nurture(job_id, fn),
            trigger=IntervalTrigger(seconds=seconds),
            id=job_id,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            **({"next_run_time": first} if first else {}),
        )


def start() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    s = get_settings()
    _scheduler = AsyncIOScheduler(timezone="UTC")

    _scheduler.add_job(
        _guard("intake", intake.run_once),
        trigger=IntervalTrigger(seconds=s.intake_interval_seconds),
        id="intake",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        _guard("sender", sender.run_once),
        trigger=IntervalTrigger(seconds=s.send_interval_seconds),
        id="sender",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    # Replies: checked as soon as the service starts, then every interval
    # (POLL_INTERVAL_SECONDS, default 10 minutes). New mail starts triage.
    _scheduler.add_job(
        _guard("poller", _then_triage(poller.run_once)),
        trigger=IntervalTrigger(seconds=s.poll_interval_seconds),
        id="poller",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        next_run_time=datetime.now(timezone.utc),
    )

    _scheduler.add_job(
        _guard("triage", triage.run_once),
        trigger=IntervalTrigger(seconds=s.triage_interval_seconds),
        id="triage",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    # Meetings booked through the booking link (no-op without a calendar).
    _scheduler.add_job(
        _guard("calendar", calendar_sync.run_once),
        trigger=IntervalTrigger(seconds=s.calendar_sync_interval_seconds),
        id="calendar",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        next_run_time=datetime.now(timezone.utc),
    )
    # The day-before meeting reminder, and what Brevo saw happen to our emails.
    for job_id, fn, seconds in (
        ("reminders", reminders.run_once, s.reminder_interval_seconds),
        ("engagement", engagement.run_once, s.engagement_interval_seconds),
    ):
        _scheduler.add_job(
            _guard(job_id, fn),
            trigger=IntervalTrigger(seconds=seconds),
            id=job_id,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )

    if s.nurture_workers:
        _add_nurture_jobs(s)

    _scheduler.start()
    log.info(
        "scheduler started (intake %ds, send %ds, poll %ds, triage %ds, calendar %ds)",
        s.intake_interval_seconds, s.send_interval_seconds, s.poll_interval_seconds,
        s.triage_interval_seconds, s.calendar_sync_interval_seconds,
    )
    return _scheduler


def status() -> dict[str, Any]:
    """Each background job's interval and next run, for the dashboard."""
    if _scheduler is None:
        return {}
    return {
        job.id: {
            "interval_seconds": int(job.trigger.interval.total_seconds()),
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in _scheduler.get_jobs()
    }


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("scheduler stopped")
