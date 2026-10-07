"""Integration test: every repository call through a real PostgREST server.

This is the same path production uses (Supabase's REST API is PostgREST), so
it exercises the migration's functions, their privileges and the HTTP client
together. It writes and deletes data (including in public.leads) — never
point it at the shared Supabase database.

Skipped unless INTEGRATION_DATABASE_URL and INTEGRATION_REST_URL are set:

    docker run -d --rm --name out01-pgtest -e POSTGRES_PASSWORD=test \\
        -p 55432:5432 postgres:16-alpine
    docker exec out01-pgtest psql -U postgres -c "create role anon nologin; \\
        create role authenticated nologin; create role service_role nologin; \\
        create role authenticator login noinherit password 'test'; \\
        grant anon, authenticated, service_role to authenticator;"
    docker run -d --rm --name out01-rest --network host \\
        -e PGRST_DB_URI=postgres://authenticator:test@localhost:55432/postgres \\
        -e PGRST_DB_SCHEMAS=public,cold_email -e PGRST_DB_ANON_ROLE=anon \\
        -e PGRST_JWT_SECRET=integration-test-secret-at-least-32-chars \\
        -e PGRST_SERVER_PORT=3917 postgrest/postgrest
    INTEGRATION_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres \\
    INTEGRATION_REST_URL=http://localhost:3917 \\
        python -m pytest tests/integration -q

The roles must exist before PostgREST starts (it exits if it cannot log in).
The fixture then creates a stand-in for the shared public tables and applies
the migration before each test.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DB_URL = os.environ.get("INTEGRATION_DATABASE_URL")
REST_URL = os.environ.get("INTEGRATION_REST_URL")
JWT_SECRET = os.environ.get(
    "INTEGRATION_JWT_SECRET", "integration-test-secret-at-least-32-chars"
)
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
# Every schema migration in order; the seed (0002) is left out on purpose.
MIGRATIONS = [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if "seed" not in p.name]

pytestmark = pytest.mark.skipif(
    not (DB_URL and REST_URL),
    reason="set INTEGRATION_DATABASE_URL and INTEGRATION_REST_URL to run integration tests",
)


def _jwt(role: str) -> str:
    """An HS256 token like Supabase's legacy anon / service_role keys."""
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"role": role}).encode())
    signature = hmac.new(
        JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256
    ).digest()
    return f"{header}.{payload}.{b64(signature)}"


if DB_URL and REST_URL:
    os.environ["SUPABASE_URL"] = REST_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = _jwt("service_role")


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


SUPABASE_ROLES = """
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'service_role') then
        create role service_role nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticator') then
        create role authenticator login noinherit password 'test';
    end if;
end $$;
grant anon, authenticated, service_role to authenticator;
"""

# The subset of the shared public schema (supabase/existing_schema.sql) that
# the service reads. Stands in for the real tables on a throwaway database.
SHARED_SCHEMA_STUB = """
create table if not exists public.leads (
    id uuid primary key default gen_random_uuid(),
    lead_id text unique,
    first_name text, last_name text, email text,
    timezone text default 'America/New_York',
    state text,
    source text,
    status text default 'New',
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);
create table if not exists public.company_profiles (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    location jsonb not null default '{}'::jsonb
);
create table if not exists public.lead_profiles (
    id uuid primary key default gen_random_uuid(),
    lead_id text not null unique references public.leads(lead_id),
    first_name text, last_name text, email text, company text,
    company_profile_id uuid references public.company_profiles(id)
);
create table if not exists public.prospects (
    id uuid primary key default gen_random_uuid(),
    title text, company_name text, country text, state text,
    lead_id text references public.leads(lead_id),
    updated_at timestamptz default now()
);
create table if not exists public.email_nurture_state (
    id uuid primary key default gen_random_uuid(),
    lead_id text not null unique references public.leads(lead_id),
    email_journey_started_at timestamptz
);
-- Read by Email Nurture (0010).
alter table public.leads add column if not exists lead_score text default 'Warm';
alter table public.leads add column if not exists payment_status text default 'none';
create table if not exists public.lead_scores (
    id uuid primary key default gen_random_uuid(),
    lead_id text not null unique references public.leads(lead_id),
    tier text not null,
    scored_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
"""


@pytest.fixture
async def database():
    import asyncpg

    from app import db

    pg = await asyncpg.connect(DB_URL)
    await pg.execute(SUPABASE_ROLES)
    await pg.execute(SHARED_SCHEMA_STUB)
    for migration in MIGRATIONS:
        await pg.execute(migration.read_text())
    # Start from a clean slate so the test is repeatable.
    await pg.execute(
        "truncate cold_email.meetings, cold_email.reply_drafts, cold_email.inbox_events,"
        " cold_email.emails, cold_email.enrollments, cold_email.contacts,"
        " cold_email.suppression_list, cold_email.inboxes,"
        " cold_email.nurture_enrollments,"
        " public.lead_scores,"
        " public.email_nurture_state, public.prospects, public.lead_profiles,"
        " public.company_profiles, public.leads restart identity cascade"
    )

    client = await db.connect()
    # Standalone PostgREST serves at the root; Supabase serves it at /rest/v1.
    client.base_url = REST_URL
    # The migration notifies PostgREST to reload; wait until it has.
    for _ in range(50):
        try:
            await db.rpc("ping")
            break
        except Exception:
            await asyncio.sleep(0.2)

    yield pg
    await db.disconnect()
    await pg.close()


CONTACTABLE = ["New", "No Answer"]


async def _seed(pg):
    inbox_id = await pg.fetchval(
        """
        insert into cold_email.inboxes (email, display_name, provider, credential_ref, daily_cap)
        values ('outreach1@example.com', 'Alex', 'smtp', 'INBOX_A', 15)
        returning id
        """
    )
    await pg.execute(
        """
        insert into public.leads (lead_id, email, first_name, timezone, source, status)
        values ('L-1', 'CISO@example.com', 'Dana', 'Europe/London', 'apollo', 'New'),
               ('L-2', 'dnc@example.com',  'Sam',  'UTC',           'apollo', 'DNC'),
               ('L-3', 'nurture@example.com', 'Riley', 'UTC',       'apollo', 'New'),
               ('L-4', null,               'Ash',  'UTC',           'apollo', 'New'),
               ('L-5', 'auto@example.com', 'Kim',  'UTC',           'apollo', 'New'),
               ('L-6', 'other@example.com','Lee',  'UTC',           'webform','New'),
               ('L-7', 'dhaka@example.com','Nadia','America/New_York','apollo','New');
        insert into public.company_profiles (name) values ('Northwind Holdings');
        insert into public.lead_profiles (lead_id, company, company_profile_id)
        select 'L-1', null, id from public.company_profiles;
        insert into public.prospects (lead_id, title, company_name, country, state)
        values ('L-1', 'CISO', 'Northwind', null, null),
               ('L-7', 'IT Manager', 'Padma Tech', 'Bangladesh', 'Dhaka Division');
        insert into public.email_nurture_state (lead_id, email_journey_started_at)
        values ('L-3', now());
        """
    )
    return str(inbox_id)


@pytest.mark.anyio
async def test_full_lifecycle(database):
    from app import repository as repo

    pg = database
    inbox_id = await _seed(pg)
    now = datetime.now(timezone.utc)
    leads_before = await pg.fetch("select * from public.leads order by lead_id")

    # Enrollment guards: every refusal names its reason.
    assert (await repo.enroll_lead("L-1", CONTACTABLE, True))[0] == "enrolled"
    assert (await repo.enroll_lead("L-1", CONTACTABLE, True))[0] == "already_enrolled"
    assert (await repo.enroll_lead("L-2", CONTACTABLE, True))[0] == "not_contactable (DNC)"
    assert (await repo.enroll_lead("L-3", CONTACTABLE, True))[0] == "in_email_nurture"
    assert (await repo.enroll_lead("L-4", CONTACTABLE, True))[0] == "no_email"
    assert (await repo.enroll_lead("nope", CONTACTABLE, True)) == ("not_found", None)

    # Auto-enroll only takes the configured sources and skips what is enrolled.
    assert await repo.auto_enroll([], CONTACTABLE, True, 50) == 0
    assert await repo.auto_enroll(["apollo"], CONTACTABLE, True, 50) == 2
    assert await pg.fetchval(
        "select count(*) from cold_email.enrollments where source = 'auto'"
    ) == 2
    await pg.execute("delete from cold_email.enrollments where source = 'auto'")

    # Intake claims exactly once, and details are assembled from shared tables.
    leads = await repo.claim_leads_for_intake(10)
    assert len(leads) == 1
    assert await repo.claim_leads_for_intake(10) == []

    lead = leads[0]
    lead_id = lead["id"]
    assert lead["email"] == "CISO@example.com"
    assert lead["job_title"] == "CISO"
    assert lead["company"] == "Northwind Holdings"
    assert lead["timezone"] == "Europe/London"
    assert lead["lead_exists"] is True

    # Region data beats the shared America/New_York column default, and an
    # enrollment override beats everything. Checked on throwaway enrollments.
    assert (await repo.enroll_lead("L-7", CONTACTABLE, True))[0] == "enrolled"
    dhaka_id = await pg.fetchval("select id::text from public.leads where lead_id='L-7'")
    dhaka = await repo.lead_for_step(dhaka_id)
    assert (dhaka["timezone"], dhaka["timezone_source"]) == ("Asia/Dhaka", "prospects country")
    await pg.execute("delete from cold_email.enrollments where lead_id=$1::uuid", dhaka_id)
    assert (await repo.enroll_lead("L-7", CONTACTABLE, True, timezone="Asia/Tokyo"))[0] == "enrolled"
    assert (await repo.lead_for_step(dhaka_id))["timezone"] == "Asia/Tokyo"
    await pg.execute("delete from cold_email.enrollments where lead_id=$1::uuid", dhaka_id)

    # Regenerating a sequence bumps the version and cancels the old emails.
    emails = [{"step_number": i, "subject": f"s{i}", "body": f"b{i}"} for i in range(1, 5)]
    dues = [
        now - timedelta(minutes=5),
        now + timedelta(days=3),
        now + timedelta(days=7),
        now + timedelta(days=12),
    ]
    first = await repo.store_sequence(lead_id, "ciso", "keyword", "gemini", "m", emails, dues)
    second = await repo.store_sequence(lead_id, "it", "llm", "gemini", "m", emails, dues)
    assert (first, second) == ("1", "2")
    assert await pg.fetchval(
        "select count(*) from cold_email.emails where lead_id=$1::uuid and status='cancelled'",
        lead_id,
    ) == 4

    # "Send now": upcoming lists the schedule; claiming takes only the next
    # email of each lead, and never a second one while the first is sending.
    assert [e["step_number"] for e in await repo.upcoming_emails(10)] == [1, 2, 3, 4]
    [nxt] = await repo.claim_next_emails(None, 25)
    assert nxt["step_number"] == 1
    assert await repo.claim_next_emails(None, 25) == []
    assert await repo.claim_next_emails(lead_id, 25) == []
    await repo.defer_step(nxt["id"], now - timedelta(minutes=5))
    # ...and a lead emailed in the last 24 hours is left alone.
    await pg.execute(
        "update cold_email.emails set sent_at = now() - interval '1 hour'"
        " where lead_id = $1::uuid and version = 1 and step_number = 1", lead_id)
    assert await repo.claim_next_emails(None, 25) == []
    assert len(await repo.claim_next_emails(None, 25, min_gap_hours=0)) == 1
    await pg.execute(
        "update cold_email.emails set sent_at = null, status = 'pending', due_at = now() - interval '5 minutes'"
        " where lead_id = $1::uuid and version = 2 and step_number = 1", lead_id)
    await pg.execute(
        "update cold_email.emails set sent_at = null where lead_id = $1::uuid and version = 1", lead_id)

    # Only the due step is claimable.
    steps = await repo.claim_due_steps(25)
    assert len(steps) == 1
    step_id = steps[0]["id"]
    assert steps[0]["step_number"] == 1

    # The daily cap counts the inbox's reserved and sent emails for the day;
    # deferring gives the slot back.
    today = now.date()
    assert await repo.reserve_inbox_slot(step_id, inbox_id, today, 0) is False
    assert await repo.reserve_inbox_slot(step_id, inbox_id, today, 1) is True
    assert (await repo.stats())["inboxes"][0]["sent_today"] == 1
    await repo.defer_step(step_id, now - timedelta(minutes=1))
    assert (await repo.stats())["inboxes"][0]["sent_today"] == 0
    [step] = await repo.claim_due_steps(25)
    assert await repo.reserve_inbox_slot(step["id"], inbox_id, today, 1) is True

    # Sending advances the lead and records a threadable message id.
    await repo.record_send(step_id, lead["email"], "<mid-1@x>", None)
    assert (await repo.stats())["inboxes"][0]["sent_today"] == 1
    assert (await repo.lead_for_step(lead_id))["status"] == "sending"
    assert await repo.thread_anchor(lead_id) == "<mid-1@x>"

    # Suppression is idempotent, case-insensitive, and blocks future claims.
    await repo.suppress(lead["email"], "unsubscribed")
    await repo.suppress(lead["email"], "unsubscribed")
    assert await repo.is_suppressed(lead["email"].upper()) is True
    assert await repo.claim_due_steps(25) == []

    # Domain-level suppression covers every address at that domain.
    await pg.execute(
        "insert into cold_email.suppression_list (domain, reason)"
        " values ('blocked.example','manual')"
    )
    assert await repo.is_suppressed("anyone@blocked.example") is True

    # Inbound events dedupe and thread back to the lead.
    assert await repo.record_inbox_event(
        inbox_id, lead_id, "reply", "a@b.c", "outreach1@example.com",
        "Re: s1", "<in-1@x>", "<mid-1@x>", "hello", now,
    ) is True
    assert await repo.record_inbox_event(
        inbox_id, lead_id, "reply", "a@b.c", "outreach1@example.com",
        "Re: s1", "<in-1@x>", "<mid-1@x>", "hello", now,
    ) is False
    assert await repo.match_lead_by_message_ids(["<mid-1@x>"]) == lead_id
    [event] = await repo.list_inbox_events(["reply"])
    assert (event["lead_id"], event["lead_email"], event["snippet"], event["inbox_email"]) == (
        lead_id, "ciso@example.com", "hello", "outreach1@example.com")
    assert await repo.list_inbox_events(["bounce"]) == []
    assert len(await repo.list_inbox_events(None)) == 1

    # Cancellation stops everything outstanding.
    assert await repo.cancel_sequence(lead_id, "replied", "human reply") == 3
    assert (await repo.lead_for_step(lead_id))["status"] == "replied"
    remaining = await pg.fetchval(
        "select count(*) from cold_email.emails"
        " where lead_id=$1 and status in ('pending','sending')",
        lead_id,
    )
    assert remaining == 0

    # Review & retry: only stuck enrollments can go back to intake.
    assert await repo.retry_enrollment(lead_id) is False  # 'replied' is final
    await repo.mark_lead(lead_id, "manual_review", "email 2: body is too short; llm call failed")
    stuck = await repo.list_enrollments(["manual_review", "failed"])
    assert [(e["lead_id"], e["last_error"]) for e in stuck] == [
        (lead_id, "email 2: body is too short; llm call failed")
    ]
    assert await repo.list_enrollments(["sent"]) == []
    assert len(await repo.list_enrollments()) == 1
    assert await repo.retry_enrollment(lead_id, job_title="Head of IT") is True
    [again] = await repo.list_enrollments()
    assert (again["status"], again["job_title"], again["last_error"]) == (
        "ready_for_outreach", "Head of IT", None)
    assert (await repo.claim_leads_for_intake(10))[0]["job_title"] == "Head of IT"
    await repo.cancel_sequence(lead_id, "replied", "human reply")

    # The IMAP cursor never rewinds.
    await repo.update_inbox_cursor(inbox_id, 42)
    await repo.update_inbox_cursor(inbox_id, 7)
    assert (await repo.active_inboxes())[0].poll_cursor == 42

    # Inbound mail matches enrolled leads only.
    assert await repo.match_lead_by_email("ciso@EXAMPLE.com") == lead_id
    assert await repo.match_lead_by_email("other@example.com") is None

    # Settings and reporting round-trip.
    await repo.set_setting("auto_enroll", {"enabled": True, "sources": ["x"]})
    settings = {s["key"]: s["value"] for s in await repo.get_settings()}
    assert settings["auto_enroll"] == {"enabled": True, "sources": ["x"]}
    stats = await repo.stats()
    assert stats["enrollments"] == {"replied": 1}
    by_region = {s["key"]: s["value"] for s in await repo.get_settings()}["business_hours_by_region"]
    assert by_region["BD"] == {"weekdays": [6, 0, 1, 2, 3]}
    assert {s["key"]: s["value"] for s in await repo.get_settings()}["reply_alert_email"] == ""
    assert stats["inboxes"][0]["sent_today"] == 1
    assert stats["emails"] == {"sent": 1, "cancelled": 7}
    assert await repo.reclaim_stuck_steps() == 0
    assert await repo.ping() is True

    # Nothing in the shared leads table was touched.
    assert await pg.fetch("select * from public.leads order by lead_id") == leads_before


@pytest.mark.anyio
async def test_publishable_key_is_locked_out(database):
    """anon (the publishable key) and authenticated users get nothing."""
    import httpx

    for token in (None, _jwt("anon"), _jwt("authenticated")):
        headers = {"Content-Profile": "cold_email", "Accept-Profile": "cold_email"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(base_url=REST_URL, headers=headers) as c:
            for fn, body in (("ping", {}), ("stats", {}), ("list_enrollments", {}), ("list_inbox_events", {}),
                             ("suppress", {"p_email": "x@y.z", "p_reason": "x"})):
                r = await c.post(f"/rpc/{fn}", json=body)
                assert r.status_code in (401, 403, 404), (token, fn, r.status_code, r.text)
            r = await c.get("/enrollments")
            assert r.status_code in (401, 403, 404), (token, r.status_code, r.text)


@pytest.mark.anyio
async def test_triage_database_functions(database):
    """Every OUT-05 function: queue, drafts, actions, referrals, meetings."""
    from app import repository as repo

    pg = database
    inbox_id = await _seed(pg)
    now = datetime.now(timezone.utc)
    lead_before = dict(await pg.fetchrow("select * from public.leads where lead_id='L-1'"))
    assert (await repo.enroll_lead("L-1", CONTACTABLE, True))[0] == "enrolled"
    lead_id = str(lead_before["id"])

    # A reply recorded by the poller is queued for triage; a bounce is not.
    assert await repo.record_inbox_event(inbox_id, lead_id, "reply", "ciso@example.com", "o@x",
                                         "Re: hi", "<r1@x>", "<m1@x>", "Sounds good", now)
    assert await repo.record_inbox_event(inbox_id, lead_id, "bounce", "mailer@x", "o@x",
                                         "Undeliverable", "<b1@x>", None, "", now)
    [ev] = await repo.claim_untriaged(10)
    assert (ev["event_type"], ev["snippet"]) == ("reply", "Sounds good")
    assert await repo.claim_untriaged(10) == []
    event_id = str(ev["id"])

    ctx = await repo.triage_context(lead_id)
    assert ctx == {"last_sent": None, "offered_slots": [], "has_meeting": False}

    await repo.save_triage(event_id, "interested", 0.9, "wants a call", {"actions": ["x"]}, "done")
    [listed] = await repo.list_inbox_events(["reply"], 10, ["interested"])
    assert (listed["category"], listed["triage_status"], listed["extracted"]) == ("interested", "done", {"actions": ["x"]})
    assert await repo.list_inbox_events(["reply"], 10, ["objection"]) == []

    # A person's correction re-queues it with their category.
    assert await repo.retriage_event(event_id, "not_now") is True
    [again] = await repo.claim_untriaged(10)
    assert again["human_category"] == "not_now"

    # Drafts: a newer draft supersedes; approve / reject / edit rules.
    slot = {"start": "2026-10-06T10:00:00+00:00", "end": "2026-10-06T10:30:00+00:00"}
    d1 = await repo.create_draft(event_id, lead_id, inbox_id, "meeting_offer", "ciso@example.com",
                                 "Re: hi", "body 1", [slot], "<r1@x>", ["<m1@x>", "<r1@x>"], None)
    d2 = await repo.create_draft(event_id, lead_id, inbox_id, "meeting_offer", "ciso@example.com",
                                 "Re: hi", "body 2", [slot], "<r1@x>", ["<m1@x>", "<r1@x>"],
                                 now - timedelta(minutes=1))
    statuses = {r["id"]: r["status"] for r in await repo.list_drafts(None)}
    assert (statuses[d1], statuses[d2]) == ("cancelled", "pending")
    # Times count as offered only once the reply has gone out.
    assert (await repo.triage_context(lead_id))["offered_slots"] == []
    assert await repo.update_draft(d2, "Re: hi", "edited") is True

    # d2 is past its auto-send time, so it is claimed exactly once.
    [claimed] = await repo.claim_drafts_to_send(10)
    assert (claimed["id"], claimed["status_before"], claimed["body"]) == (d2, "pending", "edited")
    assert claimed["references_ids"] == ["<m1@x>", "<r1@x>"]
    assert await repo.claim_drafts_to_send(10) == []
    await repo.finish_draft(d2, "sent", message_id="<sent-d2@x>")
    assert (await repo.triage_context(lead_id))["offered_slots"] == [slot]
    assert await repo.update_draft(d2, "x", "y") is False
    assert await repo.decide_draft(d2, "approve", "me") is False
    # Replies to our draft thread back to the lead.
    assert await repo.match_lead_by_message_ids(["<sent-d2@x>"]) == lead_id
    assert (await repo.triage_context(lead_id))["last_sent"]["body"] == "edited"

    d3 = await repo.create_draft(event_id, lead_id, inbox_id, "objection_reply", "ciso@example.com",
                                 "Re: hi", "b", [], "<r1@x>", [], None)
    assert await repo.claim_drafts_to_send(10) == []          # waits for a person
    assert await repo.decide_draft(d3, "approve", "arafat@x") is True
    [approved] = await repo.claim_drafts_to_send(10)
    assert approved["status_before"] == "approved"
    await repo.finish_draft(d3, "failed", error="smtp down")
    assert await repo.decide_draft(d3, "approve", "arafat@x") is True  # a failed send can be retried
    d4 = await repo.create_draft(event_id, lead_id, inbox_id, "not_now_ack", "ciso@example.com",
                                 "Re: hi", "b", [], None, [], None)
    assert await repo.decide_draft(d4, "reject", "arafat@x") is True
    assert await repo.cancel_lead_drafts(lead_id, "test") == 0     # d3 already superseded by d4
    assert await repo.newer_reply_exists(lead_id, now - timedelta(minutes=5)) is True
    assert await repo.newer_reply_exists(lead_id, now + timedelta(minutes=5)) is False

    # Meetings: booking marks the lead, and a later reply keeps that state.
    m = await repo.record_meeting(lead_id, event_id, "fake", "bk-1", now + timedelta(days=1),
                                  now + timedelta(days=1, minutes=30), "ciso@example.com", "https://meet/x")
    assert m and (await repo.triage_context(lead_id))["has_meeting"] is True
    assert (await repo.lead_for_step(lead_id))["status"] == "meeting_booked"
    await repo.cancel_sequence(lead_id, "replied", "another reply")
    assert (await repo.lead_for_step(lead_id))["status"] == "meeting_booked"
    [meeting] = await repo.list_meetings()
    assert (meeting["external_id"], meeting["meeting_url"]) == ("bk-1", "https://meet/x")

    # Not now: snooze, then wake into a fresh intake.
    await repo.snooze_lead(lead_id, now - timedelta(minutes=1), "not now")
    [snoozed] = await repo.list_snoozed()
    assert snoozed["lead_id"] == lead_id
    assert await repo.wake_snoozed() == 1
    assert (await repo.lead_for_step(lead_id))["status"] == "ready_for_outreach"

    # Out of office: remaining emails move so the next lands on the return day.
    emails = [{"step_number": i, "subject": f"s{i}", "body": "b"} for i in (1, 2)]
    await repo.store_sequence(lead_id, "ciso", "keyword", "g", "m", emails,
                              [now + timedelta(hours=1), now + timedelta(days=3, hours=1)])
    back = now + timedelta(days=7)
    assert await repo.pause_sequence_until(lead_id, back) == 2
    dues = [r["due_at"] for r in await pg.fetch(
        "select due_at from cold_email.emails where lead_id=$1::uuid and status='pending' order by step_number", lead_id)]
    assert abs((dues[0] - back).total_seconds()) < 1 and abs((dues[1] - dues[0]) - timedelta(days=3)).total_seconds() < 1
    assert await repo.pause_sequence_until(lead_id, now) == 0      # never pulls emails earlier

    # Referrals: a new person becomes a cold_email contact with lead details.
    outcome, contact_id = await repo.add_referral(lead_id, "Priya.Shah@Northwind.com", "Priya", "Shah",
                                                  "IT Security Lead", "Northwind", None, "Dana")
    assert outcome == "enrolled"
    contact = await repo.lead_for_step(contact_id)
    assert (contact["email"], contact["first_name"], contact["job_title"], contact["company"]) == (
        "priya.shah@northwind.com", "Priya", "IT Security Lead", "Northwind")
    assert (contact["lead_exists"], contact["lead_status"]) == (True, "New")
    assert await repo.referral_names([contact_id, lead_id]) == {contact_id: "Dana"}
    assert (await repo.add_referral(lead_id, "priya.shah@northwind.com", None, None, None, None, None, None))[0] == "already_enrolled"
    # Someone already in the shared leads table is enrolled as that lead.
    outcome, existing = await repo.add_referral(lead_id, "auto@example.com", None, None, None, None, None, "Dana")
    assert outcome == "enrolled" and existing == str(await pg.fetchval("select id from public.leads where lead_id='L-5'"))
    assert (await repo.add_referral(lead_id, "not-an-email", None, None, None, None, None, None))[0] == "invalid_email"
    await repo.suppress("blocked@example.com", "manual")
    assert (await repo.add_referral(lead_id, "blocked@example.com", None, None, None, None, None, None))[0] == "suppressed"

    # Unsubscribe everywhere: only public.leads.status (and updated_at) change.
    assert await repo.mark_dnc(lead_id) is True
    assert await repo.mark_dnc(lead_id) is False
    after = dict(await pg.fetchrow("select * from public.leads where lead_id='L-1'"))
    assert after["status"] == "DNC"
    assert {k: v for k, v in after.items() if k not in ("status", "updated_at")} == \
           {k: v for k, v in lead_before.items() if k not in ("status", "updated_at")}
    assert await repo.mark_dnc(contact_id) is False                # contacts have no shared row

    stats = await repo.triage_stats()
    assert stats["meeting_leads"] == 1 and stats["replied_leads"] == 1
    # The person's correction (not_now) wins over the model's "interested".
    assert stats["drafts"]["sent"] == 1 and stats["categories"] == {"not_now": 1}


@pytest.mark.anyio
async def test_conversation_history(database):
    """The thread before a reply: oldest first, both directions, sent only."""
    from app import repository as repo

    pg = database
    inbox_id = await _seed(pg)
    assert (await repo.enroll_lead("L-1", CONTACTABLE, True))[0] == "enrolled"
    lead_id = str(await pg.fetchval("select id from public.leads where lead_id='L-1'"))
    t0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    emails = [{"step_number": i, "subject": f"cold {i}", "body": f"cold body {i}"} for i in (1, 2, 3)]
    await repo.store_sequence(lead_id, "ciso", "keyword", "g", "m", emails,
                              [t0, t0 + timedelta(days=3), t0 + timedelta(days=7)])
    # Emails 1 and 2 were sent; 3 is still scheduled and must not appear.
    await pg.execute(
        "update cold_email.emails set status='sent', sent_at = due_at"
        " where lead_id=$1::uuid and step_number < 3", lead_id)
    await repo.record_inbox_event(inbox_id, lead_id, "reply", "ciso@example.com", "o@x",
                                  "Re: cold 2", "<in-1@x>", None, "What does it cost?", t0 + timedelta(days=4))
    [first_reply] = await repo.list_inbox_events(["reply"], 10)
    draft = await repo.create_draft(first_reply["id"], lead_id, inbox_id, "objection_reply", "ciso@example.com",
                                    "Re: cold 2", "About EUR 8 per seat.", [], "<in-1@x>", [], None)
    await pg.execute("update cold_email.reply_drafts set status='sent', sent_at=$2 where id=$1::uuid",
                     draft, t0 + timedelta(days=5))
    await repo.record_inbox_event(inbox_id, lead_id, "reply", "ciso@example.com", "o@x",
                                  "Re: cold 2", "<in-2@x>", None, "Still too much.", t0 + timedelta(days=6))
    newest = next(e for e in await repo.list_inbox_events(["reply"], 10) if e["snippet"] == "Still too much.")

    history = await repo.conversation_history(lead_id, newest["received_at"], newest["id"], 10)
    assert [(h["direction"], h["body"]) for h in history] == [
        ("out", "cold body 1"), ("out", "cold body 2"), ("in", "What does it cost?"),
        ("out", "About EUR 8 per seat."),
    ]
    # Only the most recent N, still oldest first.
    last2 = await repo.conversation_history(lead_id, newest["received_at"], newest["id"], 2)
    assert [h["body"] for h in last2] == ["What does it cost?", "About EUR 8 per seat."]
    assert await repo.conversation_history(lead_id, newest["received_at"], newest["id"], 0) == []


@pytest.mark.anyio
async def test_meetings_booked_through_the_link(database):
    """Calendar sync: a lead's own booking stops their sequence; cancellations
    and strangers are handled; triage's own bookings are not duplicated."""
    from app import repository as repo

    pg = database
    inbox_id = await _seed(pg)
    now = datetime.now(timezone.utc)
    for lid in ("L-1", "L-5"):
        assert (await repo.enroll_lead(lid, CONTACTABLE, True))[0] == "enrolled"
    lead = str(await pg.fetchval("select id from public.leads where lead_id='L-1'"))
    other = str(await pg.fetchval("select id from public.leads where lead_id='L-5'"))
    emails = [{"step_number": i, "subject": f"s{i}", "body": "b"} for i in (1, 2)]
    await repo.store_sequence(lead, "ciso", "keyword", "g", "m", emails,
                              [now + timedelta(hours=1), now + timedelta(days=3)])

    # The lead was offered times; the offer is open until they act.
    await repo.record_inbox_event(inbox_id, lead, "reply", "ciso@example.com", "o@x",
                                  "Re: hi", "<r1@x>", None, "Interested", now - timedelta(hours=2))
    [ev] = await repo.list_inbox_events(["reply"], 10)
    slot = {"start": (now + timedelta(days=1)).isoformat(), "end": (now + timedelta(days=1, minutes=30)).isoformat()}
    offer = await repo.create_draft(ev["id"], lead, inbox_id, "meeting_offer", "ciso@example.com",
                                    "Re: hi", "times", [slot], None, [], None)
    await pg.execute("update cold_email.reply_drafts set status='sent', sent_at=now() - interval '1 hour'"
                     " where id=$1::uuid", offer)
    pending = await repo.create_draft(ev["id"], lead, inbox_id, "objection_reply", "ciso@example.com",
                                      "Re: hi", "b", [], None, [], None)
    [open_offer] = await repo.open_offers()
    assert (open_offer["lead_id"], open_offer["offered_slots"]) == (lead, [slot])

    start, end = now + timedelta(days=2), now + timedelta(days=2, minutes=15)
    # They book through the link (address in a different case).
    r = await repo.sync_meeting("calcom", "uid-1", start, end, "booked", "CISO@Example.com", "Dana O",
                                "https://app.cal.com/video/uid-1", False)
    assert r == {"outcome": "new", "lead_id": lead}
    assert (await repo.lead_for_step(lead))["status"] == "meeting_booked"
    assert await pg.fetchval("select count(*) from cold_email.emails where lead_id=$1::uuid and status='pending'", lead) == 0
    assert await pg.fetchval("select status from cold_email.reply_drafts where id=$1::uuid", pending) == "cancelled"
    assert await repo.open_offers() == []
    assert (await repo.triage_context(lead))["has_meeting"] is True
    again = await repo.sync_meeting("calcom", "uid-1", start, end, "booked", "ciso@example.com", "Dana O",
                                    "https://app.cal.com/video/uid-1", False)
    assert again["outcome"] == "unchanged"

    # Strangers are kept only for our own event type; unseen cancellations are skipped.
    assert (await repo.sync_meeting("calcom", "uid-2", start, end, "booked", "x@y.com", "X", None, False))["outcome"] == "ignored"
    assert (await repo.sync_meeting("calcom", "uid-3", start, end, "booked", "x@y.com", "X", None, True))["outcome"] == "new"
    assert (await repo.sync_meeting("calcom", "uid-4", start, end, "cancelled", "auto@example.com", "K", None, True))["outcome"] == "ignored"
    assert (await repo.lead_for_step(other))["status"] != "meeting_booked"

    # Triage books, the sync sees it too: one row, credited to the reply.
    await repo.record_meeting(lead, ev["id"], "calcom", "uid-5", start + timedelta(days=1),
                              end + timedelta(days=1), "ciso@example.com", None)
    assert (await repo.sync_meeting("calcom", "uid-5", start + timedelta(days=1), end + timedelta(days=1),
                                    "booked", "ciso@example.com", "Dana O", "https://v/5", True))["outcome"] == "updated"

    rows = {m["external_id"]: m for m in await repo.meetings_overview()}
    assert set(rows) == {"uid-1", "uid-3", "uid-5"}
    assert (rows["uid-1"]["source"], rows["uid-1"]["attendee_name"], rows["uid-1"]["lead_status"]) == ("link", "Dana O", "meeting_booked")
    assert (rows["uid-5"]["source"], str(rows["uid-5"]["event_id"]), rows["uid-5"]["meeting_url"]) == ("reply", str(ev["id"]), "https://v/5")
    assert rows["uid-3"]["lead_id"] is None

    # Cancelling one keeps the lead booked while another is still coming up...
    assert (await repo.sync_meeting("calcom", "uid-1", start, end, "cancelled", "ciso@example.com", None, None, False))["outcome"] == "cancelled"
    assert (await repo.lead_for_step(lead))["status"] == "meeting_booked"
    # ...and the last cancellation puts them back to "replied".
    await repo.sync_meeting("calcom", "uid-5", start + timedelta(days=1), end + timedelta(days=1), "cancelled",
                            "ciso@example.com", None, None, True)
    assert (await repo.lead_for_step(lead))["status"] == "replied"
    assert (await repo.triage_context(lead))["has_meeting"] is False
    stats = await repo.triage_stats()
    assert (stats["meeting_leads"], stats["meetings_upcoming"]) == (0, 1)   # uid-3 (a stranger) remains


@pytest.mark.anyio
async def test_lead_browser_enroll_and_remove(database):
    """The Enroll page: every shared lead with its state, why the blocked ones
    cannot be enrolled, and removing / re-adding a lead."""
    from app import repository as repo

    pg = database
    inbox_id = await _seed(pg)
    browse = lambda view="all", search=None, limit=50, offset=0: repo.browse_leads(  # noqa: E731
        search, view, None, CONTACTABLE, True, limit, offset)

    rows, total = await browse()
    assert total == 7 and len(rows) == 7
    by_ref = {r["lead_ref"]: r for r in rows}
    assert by_ref["L-2"]["blocked"] == "not_contactable (DNC)"
    assert by_ref["L-3"]["blocked"] == "in_email_nurture"
    assert by_ref["L-4"]["blocked"] == "no_email" and by_ref["L-4"]["email"] is None
    assert (by_ref["L-1"]["email"], by_ref["L-1"]["job_title"], by_ref["L-1"]["company"]) == (
        "ciso@example.com", "CISO", "Northwind Holdings")
    assert (await browse("available"))[1] == 4                     # L-1, 5, 6, 7
    assert [r["lead_ref"] for r in (await browse(search="northwind"))[0]] == ["L-1"]
    page, total = await browse(limit=3, offset=6)
    assert (len(page), total) == (1, 7)

    assert (await repo.enroll_lead("L-1", CONTACTABLE, True))[0] == "enrolled"
    lead = str(await pg.fetchval("select id from public.leads where lead_id='L-1'"))
    emails = [{"step_number": i, "subject": f"s{i}", "body": "b"} for i in (1, 2)]
    now = datetime.now(timezone.utc)
    await repo.store_sequence(lead, "ciso", "keyword", "g", "m", emails,
                              [now + timedelta(hours=1), now + timedelta(days=3)])
    [row], _ = await browse("in_sequence")
    assert (row["lead_ref"], row["enrollment_status"], row["blocked"], row["emails_sent"]) == (
        "L-1", "sequence_ready", None, 0)
    assert row["next_email_at"] is not None
    assert (await browse("available"))[1] == 3

    # Removing cancels what is unsent, including a reply waiting to auto-send.
    await repo.record_inbox_event(inbox_id, lead, "reply", "ciso@example.com", "o@x",
                                  "Re: s1", "<r@x>", None, "hm", now)
    [ev] = await repo.list_inbox_events(["reply"], 5)
    draft = await repo.create_draft(ev["id"], lead, inbox_id, "objection_reply", "ciso@example.com",
                                    "Re: s1", "b", [], None, [], now + timedelta(hours=2))
    await pg.execute("update cold_email.enrollments set status='sequence_ready' where lead_id=$1::uuid", lead)
    await pg.execute("update cold_email.emails set status='pending' where lead_id=$1::uuid", lead)

    await pg.execute("update cold_email.enrollments set status='processing' where lead_id=$1::uuid", lead)
    assert await repo.remove_from_sequence(lead, "x") == "busy"
    await pg.execute("update cold_email.enrollments set status='sequence_ready' where lead_id=$1::uuid", lead)

    assert await repo.remove_from_sequence(lead, "removed from the sequence by me@x") == "removed"
    assert await pg.fetchval("select count(*) from cold_email.emails where lead_id=$1::uuid"
                             " and status='pending'", lead) == 0
    assert await pg.fetchval("select status from cold_email.reply_drafts where id=$1::uuid", draft) == "cancelled"
    [row], _ = await browse("finished")
    assert (row["enrollment_status"], row["enrollment_note"]) == ("stopped", "removed from the sequence by me@x")
    assert await repo.remove_from_sequence(lead, "again") == "not_active"
    other = str(await pg.fetchval("select id from public.leads where lead_id='L-5'"))
    assert await repo.remove_from_sequence(other, "x") == "not_enrolled"

    # Enrolling again starts a fresh sequence.
    assert await repo.retry_enrollment(lead) is True
    assert (await repo.lead_for_step(lead))["status"] == "ready_for_outreach"
    assert (await pg.fetchrow("select status from public.leads where lead_id='L-1'"))["status"] == "New"
