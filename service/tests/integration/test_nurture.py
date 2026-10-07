"""Email Nurture end to end against a real database (PostgREST + Postgres).

The workers run for real; only the AI and the mail transport are fakes, and
time is passed in, so a 30-day sequence runs in a few seconds. Scenario
numbers refer to the acceptance list in the nurture spec.

Same setup as test_repository.py (see its docstring); skipped without it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.test_repository import (  # noqa: F401  (fixtures)
    CONTACTABLE, DB_URL, REST_URL, anyio_backend, database,
)

pytestmark = pytest.mark.skipif(
    not (DB_URL and REST_URL),
    reason="set INTEGRATION_DATABASE_URL and INTEGRATION_REST_URL to run integration tests",
)

MON = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)  # Monday, inside 9-17 UTC


class NoAI:
    """Persona routing must not reach a real model in these tests."""

    async def complete_json(self, system, user, *, temperature):
        raise RuntimeError("no AI in this test")


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    from app.nurture import persona
    monkeypatch.setattr(persona, "get_gateway", lambda: NoAI())


async def add_lead(pg, ref, email, title, tier, tz="UTC", status="New"):
    await pg.execute(
        "insert into public.leads (lead_id, email, first_name, timezone, source, status, lead_score)"
        " values ($1, $2, $3, $4, 'fb', $5, $6)", ref, email, ref.replace("-", " "), tz, status, tier)
    if title:
        await pg.execute("insert into public.prospects (lead_id, title, company_name) values ($1, $2, 'Northwind')",
                         ref, title)
    await pg.execute("insert into public.lead_scores (lead_id, tier) values ($1, $2)", ref, tier)
    return str(await pg.fetchval("select id from public.leads where lead_id=$1", ref))


async def set_tier(pg, ref, tier):
    """What the scorer does: a new tier and a new timestamp."""
    await pg.execute("update public.lead_scores set tier=$2, updated_at=now(), scored_at=now()"
                     " where lead_id=$1", ref, tier)
    await pg.execute("update public.leads set lead_score=$2 where lead_id=$1", ref, tier)


async def configure(**changes):
    """Fresh nurture settings: defaults plus ``changes`` (nothing left over
    from an earlier test or run on the same database)."""
    from app import settings_store
    from app.nurture import options
    await settings_store.set_value("nurture", {})
    await settings_store.set_value("nurture_scores_seen_at", "")
    await settings_store.set_value("nurture_resources", [])
    base = {"default_timezone": "UTC", "test_mode": {"enabled": False, "allow_list": []}}
    return await options.save({**base, **changes})


async def enrollment(pg, lead_id):
    row = await pg.fetchrow("select * from cold_email.nurture_enrollments where lead_id=$1::uuid"
                            " order by enrolled_at desc limit 1", lead_id)
    return dict(row) if row else None


@pytest.mark.anyio
async def test_scores_enroll_switch_tracks_and_hand_off(database):
    """Scenarios 1 (enrolment part), 4 (Hot), 6 and 10."""
    from app import repository as repo
    from app.nurture import enroll, exits

    pg = database
    await configure()
    ciso = await add_lead(pg, "N-1", "ciso@northwind.com", "Chief Information Security Officer", "Cold")
    outbound = await add_lead(pg, "N-2", "outbound@northwind.com", "IT Manager", "Cold")
    blocked = await add_lead(pg, "N-3", "gone@northwind.com", "IT Manager", "Cold")
    assert (await repo.enroll_lead("N-2", CONTACTABLE, True))[0] == "enrolled"
    await repo.suppress("gone@northwind.com", "unsubscribed")

    # The first look only records today's scores; nobody is enrolled.
    assert await enroll.score_tick(now=MON) == {}
    assert await pg.fetchval("select count(*) from cold_email.nurture_enrollments") == 0

    for ref in ("N-1", "N-2", "N-3"):
        await set_tier(pg, ref, "Warm")
    stats = await enroll.score_tick(now=MON)
    assert stats == {"enrolled": 1}           # N-2 is in the cold sequence (scenario 10), N-3 unsubscribed

    e = await enrollment(pg, ciso)
    assert (e["persona"], e["temperature"], e["status"], e["step"]) == ("ciso", "warm", "active", 0)
    assert e["next_send_at"] == e["started_at"] == MON
    assert await enrollment(pg, outbound) is None and await enrollment(pg, blocked) is None
    # One sequence at a time, the other way round too.
    assert (await repo.enroll_lead("N-1", CONTACTABLE, True))[0] == "in_nurture"

    # Warm -> Cold: same enrollment, other track.
    await set_tier(pg, "N-1", "Cold")
    assert await enroll.score_tick(now=MON + timedelta(hours=1)) == {"switched": 1}
    assert (await enrollment(pg, ciso))["temperature"] == "cold"

    # Hot: handed to sales, once.
    await set_tier(pg, "N-1", "Hot")
    assert await enroll.score_tick(now=MON + timedelta(hours=2)) == {"handoff": 1}
    e = await enrollment(pg, ciso)
    assert (e["status"], e["exit_reason"]) == ("handed_off", "hot_score")
    assert await pg.fetchval("select count(*) from cold_email.nurture_enrollments where status='handed_off'") == 1

    # Scenario 6: two hand-off signals at the same moment -> one hand-off.
    it = await add_lead(pg, "N-4", "it@northwind.com", "IT Manager", "Cold")
    assert (await enroll.enroll_eligible([it], now=MON))["enrolled"] == 1
    eid = str((await enrollment(pg, it))["id"])
    results = await asyncio.gather(*(exits.handoff(eid, t) for t in ("hot_score", "pricing_click") * 3))
    assert sum(results) == 1                     # exactly one call handed off
    assert await pg.fetchval("select count(*) from cold_email.nurture_events where kind='handoff' and enrollment_id=$1::uuid", eid) == 1


@pytest.mark.anyio
async def test_cooldown_reconcile_and_test_mode(database):
    """Scenario 9 (cooldown), the hourly reconciliation and the allow-list."""
    from app.nurture import enroll
    from app.nurture import repo as nrepo

    pg = database
    cfg = await configure()
    lead = await add_lead(pg, "C-1", "done@northwind.com", "HR Manager", "Cold")
    assert await enroll.enroll_lead(lead, "Cold", "manual", cfg, MON) == "enrolled"
    e = await enrollment(pg, lead)
    assert e["persona"] == "hr"

    # Completed today: not again within the cooldown.
    await pg.execute("update cold_email.nurture_enrollments set status='completed', exit_reason='completed',"
                     " exited_at=now() where id=$1", e["id"])
    assert await enroll.enroll_lead(lead, "Cold", "manual", cfg, MON) == "cooldown"
    assert await nrepo.candidates(90, cfg["blocked_lead_statuses"], True) == []
    await pg.execute("update cold_email.nurture_enrollments set exited_at=now() - interval '91 days'"
                     " where id=$1", e["id"])
    assert [r["lead_id"] for r in await nrepo.candidates(90, cfg["blocked_lead_statuses"], True)] == [lead]

    # A Warm change that never enrolled (e.g. the worker crashed) is caught
    # by the reconciliation.
    await enroll.score_tick(now=MON)                      # baseline
    missed = await add_lead(pg, "C-2", "missed@northwind.com", "Network Engineer", "Warm")
    # Rescored in the last 48 h: the missed lead, and C-1 whose cooldown is now over.
    assert (await enroll.reconcile_tick(now=MON))["enrolled"] == 2
    assert (await enrollment(pg, lead))["status"] == "active"
    missed_e = await enrollment(pg, missed)
    assert json.loads(await pg.fetchval("select detail from cold_email.nurture_events where enrollment_id=$1"
                                        " and kind='enrolled'", missed_e["id"]))["source"] == "reconcile"

    # Customers and blocked statuses are never nurtured.
    paid = await add_lead(pg, "C-3", "paid@northwind.com", "IT Manager", "Warm", status="Paid")
    assert await enroll.enroll_lead(paid, "Warm", "manual", cfg, MON) == "lead_status (Paid)"
    await pg.execute("update public.leads set status='New', payment_status='paid' where lead_id='C-3'")
    assert await enroll.enroll_lead(paid, "Warm", "manual", cfg, MON) == "customer"

    # Test mode: only allow-listed addresses are enrolled.
    cfg = await configure(test_mode={"enabled": True, "allow_list": ["tester@northwind.com"]})
    other = await add_lead(pg, "C-4", "real@northwind.com", "IT Manager", "Warm")
    tester = await add_lead(pg, "C-5", "Tester@Northwind.com", "IT Manager", "Warm")
    assert await enroll.enroll_lead(other, "Warm", "manual", cfg, MON) == "test_mode_not_allowed"
    assert await enroll.enroll_lead(tester, "Warm", "manual", cfg, MON) == "enrolled"
    assert (await enrollment(pg, tester))["test_mode"] is True


# =============================================================================
# The whole flow in test mode: a "day" is one minute, mail and AI are fakes.
# =============================================================================

class CaptureSender:
    """Stands in for SMTP: records every message."""

    def __init__(self):
        self.sent = []

    async def send(self, inbox, message):
        from app.mail.base import SendResult
        self.sent.append((inbox, message))
        return SendResult(message_id=f"<n{len(self.sent)}@aspire.test>")

    def to(self, address):
        return [m for _, m in self.sent if m.to_email == address]


class WorldAI:
    """Writer, judge, reply classifier and sales summary, scripted."""

    def __init__(self):
        self.prompts = []
        self.bad_urls = False
        self.verdict = {"category": "interested", "confidence": 0.95, "summary": "Wants a demo"}

    async def complete_json(self, system, user, *, temperature):
        from app.llm.base import Completion
        self.prompts.append((system, user))
        if system.startswith("You review"):
            out = {"grounding": 0.9, "persona_fit": 0.9, "tone": 0.9, "non_repetition": 0.9, "overall": 0.9}
        elif system.startswith("You triage replies"):
            out = self.verdict
        elif system.startswith("You brief a salesperson"):
            out = {"summary": "Engaged CISO; clicked pricing. Offer a short demo."}
        elif "nurture sequence" in system:
            step = user.split("Email ", 1)[1].split(" of 6", 1)[0]
            temp = "warm" if "A Warm lead" in user else "cold"
            body = ("Short, regular training keeps people alert to phishing far better than a long "
                    "yearly course, and it is easy to measure how habits change over a few months. "
                    f"Email {step} on the {temp} track.\n\nIf useful, you can {{{{CTA_DEMO}}}} or {{{{CTA_PRICING}}}}.")
            if self.bad_urls:
                body += " Details at http://example.com/offer"
            out = {"subject": f"Step {step} ({temp})", "preheader": "p", "body": body,
                   "resource_id": None, "cta_type": "demo"}
        else:
            raise RuntimeError("unexpected prompt")
        return Completion(json.dumps(out), "test-model", "test", 100, 50)

    def writer_prompts(self):
        return [u for s, u in self.prompts if "nurture sequence" in s]


@pytest.fixture
async def world(database, monkeypatch):
    from app import repository as repo
    from app.config import get_settings
    from app.nurture import exits, persona, sending, writer
    from app.triage import classify
    from tests.integration.test_repository import _seed

    s = get_settings()
    monkeypatch.setattr(s, "nurture_from_email", "nurture@aspire.test")
    monkeypatch.setattr(s, "nurture_public_url", "https://go.aspire.test")
    monkeypatch.setenv("NURTURE_SMTP_PASSWORD", "app-password")
    mail, ai = CaptureSender(), WorldAI()
    monkeypatch.setattr(sending, "_transport", mail)
    for module in (writer, persona, exits, classify):
        monkeypatch.setattr(module, "get_gateway", lambda: ai)
    await _seed(database)   # an active cold inbox: Reply Triage reads it, nurture uses it as Reply-To
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    testers = ["ciso@w.test", "hot@w.test", "click@w.test", "both@w.test", "unsub@w.test",
               "switch@w.test", "url@w.test", "reply@w.test", "pause@w.test",
               "r1@w.test", "r2@w.test", "r3@w.test"]
    await configure(
        test_mode={"enabled": True, "minutes_per_day": 1, "allow_list": testers},
        branding={"demo_url": "https://cal.com/aspire/demo", "pricing_url": "https://aspire.test/pricing",
                  "company_name": "Aspire", "company_address": "1 Main St"},
        sales_email="sales@aspire.test", bot_click_seconds=0,
    )
    return {"pg": database, "mail": mail, "ai": ai, "start": start, "repo": repo,
            "inbox": (await repo.active_inboxes())[0]}


async def run(minutes_from, minutes_to, start):
    """Run the write and send jobs once a (simulated) minute."""
    from app.nurture import jobs
    for minute in range(minutes_from, minutes_to + 1):
        now = start + timedelta(minutes=minute)
        await jobs.generate_tick(now)
        await jobs.send_tick(now)


async def enrolled(pg, ref, email, title, tier, start):
    from app.nurture import enroll, options
    lead = await add_lead(pg, ref, email, title, tier)
    assert await enroll.enroll_lead(lead, tier, "score", await options.load(), start) == "enrolled"
    return lead, str((await enrollment(pg, lead))["id"])


async def sent_message(pg, eid, step):
    """The id of email ``step``: tracked links are /n/c/<this id>/<demo|pricing|resource>."""
    return str(await pg.fetchval("select id from cold_email.nurture_messages where enrollment_id=$1::uuid"
                                 " and step=$2 and status='sent'", eid, step))


@pytest.mark.anyio
async def test_warm_ciso_gets_email_one(world):
    """Scenario 1: CISO scored Warm -> CISO Warm track, email 1 sent."""
    pg, mail, ai, start = world["pg"], world["mail"], world["ai"], world["start"]
    lead, eid = await enrolled(pg, "W-1", "ciso@w.test", "CISO", "Warm", start)
    e = await enrollment(pg, lead)
    assert (e["persona"], e["temperature"]) == ("ciso", "warm")

    await run(0, 0, start)
    [msg] = mail.to("ciso@w.test")
    assert msg.subject == "Step 1 (warm)"
    assert "A Warm lead" in ai.writer_prompts()[0] and "Acknowledge their interest" in ai.writer_prompts()[0]
    assert msg.reply_to == world["inbox"].email                     # Reply Triage reads it
    assert msg.unsubscribe_url.startswith("https://go.aspire.test/n/u/")
    assert "https://go.aspire.test/n/c/" in msg.body_text and "aspire.test/pricing" not in msg.body_text
    assert "Unsubscribe" in msg.body_html and "1 Main St" in msg.body_text
    e = await enrollment(pg, lead)
    assert e["step"] == 1 and e["next_send_at"] == start + timedelta(minutes=3)
    m = await pg.fetchrow("select * from cold_email.nurture_messages where enrollment_id=$1::uuid", eid)
    log = json.loads(m["ai_log"])
    assert (m["source"], m["status"], log["model"], log["prompt_version"]) == ("ai", "sent", "test:test-model", "nurture-writer-v1")
    assert log["judge_score"] == pytest.approx(0.9) and log["tokens_in"] == 200 and log["brief_version"] == 1
    assert f"https://go.aspire.test/n/c/{m['id']}/pricing" in msg.body_text


@pytest.mark.anyio
async def test_hot_after_email_two_stops_and_notifies_sales_once(world):
    """Scenario 4."""
    pg, mail, start = world["pg"], world["mail"], world["start"]
    lead, eid = await enrolled(pg, "W-2", "hot@w.test", "IT Manager", "Warm", start)
    await run(0, 3, start)                                          # warm: emails at minute 0 and 3
    assert len(mail.to("hot@w.test")) == 2
    await set_tier(pg, "W-2", "Hot")
    await run(4, 12, start)                                         # email 3 would be at minute 7
    assert len(mail.to("hot@w.test")) == 2
    e = await enrollment(pg, lead)
    assert (e["status"], e["exit_reason"]) == ("handed_off", "hot_score")
    assert await pg.fetchval("select count(*) from cold_email.nurture_events where kind='handoff' and enrollment_id=$1::uuid", eid) == 1
    [note] = mail.to("sales@aspire.test")
    assert "Lead score became Hot" in note.subject and "Offer a short demo" in note.body_text
    assert await pg.fetchval("select count(*) from cold_email.nurture_messages where enrollment_id=$1::uuid"
                             " and status in ('ready','approved','pending_approval','generating')", eid) == 0


@pytest.mark.anyio
async def test_pricing_click_hands_off_exactly_once(world):
    """Scenarios 5 and 6."""
    from app.nurture import exits, options
    from app.nurture.api import handle_click

    pg, mail, start = world["pg"], world["mail"], world["start"]
    lead, eid = await enrolled(pg, "W-3", "click@w.test", "HR Manager", "Cold", start)
    await run(0, 10, start)                                         # cold: emails at 0, 5, 10
    assert len(mail.to("click@w.test")) == 3
    mid = await sent_message(pg, eid, 3)
    urls = await asyncio.gather(*(handle_click(mid, "pricing", "Mozilla/5.0") for _ in range(4)))
    assert set(urls) == {"https://aspire.test/pricing"}
    assert (await enrollment(pg, lead))["exit_reason"] == "pricing_click"
    assert await pg.fetchval("select count(*) from cold_email.nurture_events where kind='handoff' and enrollment_id=$1::uuid", eid) == 1
    assert await pg.fetchval("select count(*) from cold_email.nurture_events where kind='click'"
                             " and enrollment_id=$1::uuid", eid) == 4

    # A Hot score and a pricing click at the same moment: one hand-off.
    lead2, eid2 = await enrolled(pg, "W-4", "both@w.test", "HR Manager", "Cold", start)
    await run(0, 0, start)
    mid2 = await sent_message(pg, eid2, 1)
    await asyncio.gather(handle_click(mid2, "pricing", "Mozilla/5.0"), exits.handoff(eid2, "hot_score"),
                         handle_click(mid2, "pricing", "Mozilla/5.0"))
    assert await pg.fetchval("select count(*) from cold_email.nurture_events where kind='handoff' and enrollment_id=$1::uuid", eid2) == 1

    # A mail scanner opening the link (too soon after sending) is not a person.
    await options.save({"bot_click_seconds": 3600})
    lead3, eid3 = await enrolled(pg, "W-5", "ciso@w.test", "CISO", "Cold", start)
    await run(0, 0, start)
    assert await handle_click(await sent_message(pg, eid3, 1), "demo", "Mozilla/5.0") == "https://cal.com/aspire/demo"
    assert (await enrollment(pg, lead3))["status"] == "active"


@pytest.mark.anyio
async def test_unsubscribe_stops_every_module(world):
    """Scenario 7."""
    pg, mail, repo, start = world["pg"], world["mail"], world["repo"], world["start"]
    from app.nurture import repo as nrepo
    lead, eid = await enrolled(pg, "W-6", "unsub@w.test", "IT Manager", "Warm", start)
    await run(0, 0, start)
    # The unsubscribe link is /n/u/<enrollment id>.
    assert f"/n/u/{eid}" in mail.to("unsub@w.test")[0].unsubscribe_url
    assert await nrepo.suppress(eid, "unsubscribed")
    await run(1, 30, start)
    assert len(mail.to("unsub@w.test")) == 1
    assert await repo.is_suppressed("unsub@w.test")                  # checked before every cold / triage send
    assert await pg.fetchval("select status from public.leads where lead_id='W-6'") == "DNC"
    assert (await repo.enroll_lead("W-6", CONTACTABLE, True))[0] != "enrolled"
    assert (await enrollment(pg, lead))["exit_reason"] == "unsubscribed"


@pytest.mark.anyio
async def test_cold_to_warm_switches_track_from_the_next_email(world):
    """Scenario 8."""
    from app.nurture import enroll
    pg, mail, ai, start = world["pg"], world["mail"], world["ai"], world["start"]
    await enroll.score_tick(now=start)                              # baseline
    lead, eid = await enrolled(pg, "W-7", "switch@w.test", "CISO", "Cold", start)
    await run(0, 5, start)                                          # cold: emails 1 and 2 at 0 and 5
    assert [m.subject for m in mail.to("switch@w.test")] == ["Step 1 (cold)", "Step 2 (cold)"]
    await set_tier(pg, "W-7", "Warm")
    assert (await enroll.score_tick(now=start + timedelta(minutes=6)))["switched"] == 1
    e = await enrollment(pg, lead)
    assert e["temperature"] == "warm"
    assert e["next_send_at"] == start + timedelta(minutes=7)        # warm day 7 (cold would be day 10)
    await run(6, 7, start)
    assert mail.to("switch@w.test")[-1].subject == "Step 3 (warm)"
    assert "Value and ROI framing" in ai.writer_prompts()[-1]


@pytest.mark.anyio
async def test_bad_ai_output_falls_back_on_time_and_the_sequence_completes(world):
    """Scenarios 12 and 9: raw URLs twice -> fallback sent on time; after 6
    emails the lead is completed and not re-enrolled within the cooldown."""
    from app.nurture import enroll, options
    pg, mail, ai, start = world["pg"], world["mail"], world["ai"], world["start"]
    ai.bad_urls = True
    lead, eid = await enrolled(pg, "W-8", "url@w.test", "IT Manager", "Warm", start)
    await run(0, 28, start)
    sent = mail.to("url@w.test")
    assert len(sent) == 6 and all("http://example.com" not in m.body_text for m in sent)
    assert sent[0].subject == "The hidden IT cost of phishing"      # the IT step-1 fallback
    rows = await pg.fetch("select step, source, sent_at, send_at from cold_email.nurture_messages"
                          " where enrollment_id=$1::uuid and status='sent' order by step", eid)
    assert [r["source"] for r in rows] == ["fallback"] * 6
    assert [int((r["send_at"] - start).total_seconds() // 60) for r in rows] == [0, 3, 7, 12, 19, 28]
    e = await enrollment(pg, lead)
    assert (e["status"], e["exit_reason"], e["step"]) == ("completed", "completed", 6)
    assert await enroll.enroll_lead(lead, "Warm", "manual", await options.load(), start) == "cooldown"


@pytest.mark.anyio
async def test_interested_reply_hands_off(world):
    """Scenario 13, through the real poller and Reply Triage hooks."""
    from app import settings_store
    from app.mail.base import IncomingMessage
    from app.nurture import replies
    from app.workers import poller

    pg, mail, start = world["pg"], world["mail"], world["start"]
    lead, eid = await enrolled(pg, "W-9", "reply@w.test", "CISO", "Cold", start)
    await run(0, 0, start)
    sent_id = await pg.fetchval("select message_id from cold_email.nurture_messages where enrollment_id=$1::uuid", eid)

    reply = IncomingMessage(uid=1, message_id="<r1@lead>", in_reply_to=sent_id, references=[sent_id],
                            from_email="reply@w.test", to_email=world["inbox"].email,
                            subject="Re: Step 1", body_snippet="Yes, let's talk next week.",
                            received_at=start + timedelta(minutes=1))
    assert await poller._handle(world["inbox"], reply) == "nurture_reply"
    assert (await enrollment(pg, lead))["status"] == "held"
    ev = await pg.fetchrow("select * from cold_email.inbox_events where message_id='<r1@lead>'")
    assert (ev["lead_id"], str(ev["nurture_enrollment_id"])) == (None, eid)
    # Reply Triage's own queue does not take it; the nurture hook does.
    assert await world["repo"].claim_untriaged(10) == []
    stats = await replies.triage_pending(await settings_store.triage_config())
    assert stats == {"interested": 1}
    e = await enrollment(pg, lead)
    assert (e["status"], e["exit_reason"]) == ("handed_off", "reply_interested")
    ev = await pg.fetchrow("select * from cold_email.inbox_events where message_id='<r1@lead>'")
    assert (ev["category"], ev["triage_status"]) == ("interested", "done")
    assert await pg.fetchval("select count(*) from cold_email.reply_drafts") == 0  # no AI reply drafted


@pytest.mark.anyio
async def test_other_reply_outcomes(world):
    """not now continues, out of office delays 5 days, objection stops."""
    from app import settings_store
    from app.mail.base import IncomingMessage
    from app.nurture import replies
    from app.workers import poller

    pg, ai, start = world["pg"], world["ai"], world["start"]
    triage_cfg = await settings_store.triage_config()

    async def reply_with(ref, email, verdict, n):
        lead, eid = await enrolled(pg, ref, email, "CISO", "Cold", start)
        await run(0, 0, start)
        sent_id = await pg.fetchval("select message_id from cold_email.nurture_messages where enrollment_id=$1::uuid", eid)
        await poller._handle(world["inbox"], IncomingMessage(
            uid=n, message_id=f"<x{n}@lead>", in_reply_to=sent_id, references=[], from_email=email,
            to_email=world["inbox"].email, subject="Re", body_snippet="...", received_at=start))
        ai.verdict = verdict
        await replies.triage_pending(triage_cfg, now=start + timedelta(minutes=1))
        return lead

    lead = await reply_with("R-1", "r1@w.test", {"category": "not_now", "confidence": 0.9, "summary": "later"}, 11)
    assert (await enrollment(pg, lead))["status"] == "active"
    lead = await reply_with("R-2", "r2@w.test", {"category": "out_of_office", "confidence": 0.9, "summary": "away"}, 12)
    e = await enrollment(pg, lead)
    assert e["status"] == "active" and e["next_send_at"] >= start + timedelta(days=5)
    lead = await reply_with("R-3", "r3@w.test", {"category": "objection", "confidence": 0.9, "summary": "has a vendor",
                                                 "objection": "already have a vendor"}, 13)
    e = await enrollment(pg, lead)
    assert (e["status"], e["exit_reason"], e["needs_review"]) == ("exited", "not_interested", True)
    assert not await world["repo"].is_suppressed("r3@w.test")       # not a global opt-out


@pytest.mark.anyio
async def test_pause_all_stops_within_one_cycle(world):
    """Scenario 14."""
    from app.nurture import jobs, options
    pg, mail, start = world["pg"], world["mail"], world["start"]
    lead, eid = await enrolled(pg, "W-10", "pause@w.test", "IT Manager", "Warm", start)
    await jobs.generate_tick(start)
    await options.save({"paused": True})
    assert await jobs.send_tick(start) == {"paused": 1}
    assert await jobs.generate_tick(start) == {"paused": 1}
    assert mail.to("pause@w.test") == []
    await options.save({"paused": False})
    await jobs.send_tick(start)
    assert len(mail.to("pause@w.test")) == 1


@pytest.mark.anyio
async def test_the_nurture_mailbox_is_invisible_to_the_cold_sequence(world):
    """Registered as an inactive inbox: cold sending, polling and the Overview ignore it."""
    from app.nurture import repo as nrepo

    repo = world["repo"]
    row = await nrepo.register_mailbox("nurture@aspire.test", "Aspire")
    assert row["active"] is False
    assert [i.email for i in await repo.active_inboxes()] == [world["inbox"].email]
    assert [i["email"] for i in (await repo.stats())["inboxes"]] == [world["inbox"].email]
    # Registering again is harmless, and the cold inbox itself is never taken over.
    assert (await nrepo.register_mailbox("nurture@aspire.test", "Aspire"))["id"] == row["id"]
    assert (await nrepo.register_mailbox(world["inbox"].email, "x"))["active"] is True
