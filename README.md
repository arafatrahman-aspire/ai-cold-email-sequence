# OUT-01 — Cold Email Automation

Implementation of the [OUT-01 plan](./OUT-01%20—%20Cold%20Email%20Automation%20Implementation%20%26%20Architecture%20Plan.md).

When an existing lead is enrolled into cold outreach, the system routes it to one of three
personas, drafts a 4-email sequence, schedules each email inside the lead's own
business hours, sends within per-inbox daily caps, and cancels everything
outstanding on a reply, hard bounce or unsubscribe.

---

## Architecture as built

```
Supabase (shared with other projects)
  public.*      leads · lead_profiles · company_profiles · prospects
                email_nurture_state                       ← READ ONLY
  cold_email.*  enrollments · emails · inboxes · inbox_events
                suppression_list · settings               ← owned by this service
        ▲
        │ Supabase REST API (PostgREST) — cold_email.* functions, secret key
        ▼
FastAPI service (Docker)  ── APScheduler ──┬── intake worker  (every 5 min)
                                           │     auto-enroll (if on), claim enrollments
                                           │     LangGraph: persona → draft → validate
                                           │     write emails
                                           │
                                           ├── sender worker  (every 3 min)
                                           │     kill switch → lead status → suppression → hours → cap
                                           │     send via SMTP or Graph, record on the email
                                           │
                                           └── poller worker  (at start, then every 10 min)
                                                 IMAP or Graph poll
                                                 classify → cancel sequence
```

### One deviation from the plan

The plan puts the sending and reply-detection cron in Supabase Edge Functions.
Edge Functions run Deno, which has no workable SMTP or IMAP client, so those two
workers run in the Python service instead, on an APScheduler loop. Supabase
remains the system of record and the intake trigger is unchanged. Everything
else follows the plan.

### Everything swappable is swappable by environment variable

| Concern | Variable | Options |
|---|---|---|
| Drafting model | `LLM_PROVIDER` | `gemini`, `omniroute` |
| Cross-provider fallback | `LLM_FALLBACK_PROVIDER` | `gemini`, `omniroute`, `none` |
| Sending | `MAIL_SENDER` | `smtp`, `graph` |
| Reading | `MAIL_READER` | `imap`, `graph` |

Sender and reader are independent, so SMTP-out with Graph-in (or the reverse)
is a valid configuration. Each inbox row can override host, port and TLS mode in
its `config` JSON, which is what lets one deployment span providers.

### The shared database is never modified

Other projects use the same Supabase database, so this service writes only to
its own `cold_email` schema. The `public` tables are read, never written,
altered, indexed or triggered. There are no foreign keys or views pointing into
`public`, so other projects can delete leads or change columns freely.

A lead enters outreach only when it is **enrolled** (a row in
`cold_email.enrollments`, keyed by `public.leads.id`):

```bash
# by public.leads.lead_id or public.leads.id; job_title/company are optional overrides
curl -X POST localhost:8080/enroll -H 'Content-Type: application/json' \
  -d '{"leads": [{"lead_id": "L-1042", "job_title": "Head of IT"}]}'
```

Enrollment refuses leads with no email, with a `public.leads.status` outside
`contactable_lead_statuses`, that already have an `email_nurture_state` journey,
or that are suppressed, and it says which reason applied. Before every send the
shared status is checked again, so if another project marks a lead `DNC`,
`Paid` or `Not Interested`, its sequence stops (enrollment status `stopped`).

To enroll automatically, list the `public.leads.source` values to take from:

```bash
curl -X PUT localhost:8080/settings/auto_enroll -H 'Content-Type: application/json' \
  -d '{"value": {"enabled": true, "sources": ["apollo"], "batch_size": 50}}'
```

Lead details are assembled at read time: email, name, timezone and status come
from `leads`; company from the enrollment override, then `lead_profiles`, then
`company_profiles`, then `prospects`; job title from the enrollment override,
then `prospects.title`. Job title drives persona routing, so set the override
if the shared data lacks it.

### Every lead is emailed in its own local business hours

Each lead's timezone is worked out from the shared data, in this order:
the enrollment's `timezone` override → `leads.timezone` (unless it is just the
column default, `America/New_York`) → state/province (US, Canada, Australia)
from `prospects`, `company_profiles.location` or `leads.state` → country →
`leads.timezone` → UTC. So a prospect in Bangladesh whose `leads.timezone`
was never filled in is still emailed on Dhaka time. The intake log shows
which timezone each lead got and why.

The send window defaults to `business_hours` (09:00–17:00, Mon–Fri, local
time). `business_hours_by_region` changes it per country code or timezone,
and only needs the fields that differ. It ships with Sun–Thu work weeks for
Bangladesh, Saudi Arabia, Israel, Egypt, Qatar, Kuwait, Oman, Bahrain, Jordan,
Iraq and Algeria:

```bash
# Start at 10:00 for UK leads; add Nepal's Sun–Fri week
curl -X PUT localhost:8080/settings/business_hours_by_region \
  -H 'Content-Type: application/json' \
  -d '{"value": {"BD": {"weekdays": [6,0,1,2,3]}, "GB": {"start_hour": 10},
                 "NP": {"weekdays": [6,0,1,2,3,4]}}}'
```

That replaces the whole map, so include the entries you want to keep. To
fix a single lead, pass `"timezone": "Asia/Dhaka"` to `POST /enroll`.

### Everything operational is table-driven

`cold_email.settings` holds business hours, step spacing, batch sizes, daily caps and a
global kill switch. Changing any of them takes effect within a minute — no
redeploy, no restart.

```bash
curl -X PUT localhost:8080/settings/business_hours \
  -H 'Content-Type: application/json' \
  -d '{"value": {"start_hour": 8, "end_hour": 18, "weekdays": [0,1,2,3,4]}}'

# Stop all sending immediately:
curl -X PUT localhost:8080/settings/sequence_enabled \
  -H 'Content-Type: application/json' -d '{"value": false}'
```

---

## Setup

### 1. Database

The service has no direct Postgres connection. It calls functions in the
`cold_email` schema through the Supabase REST API with the secret key, so no
database password is needed anywhere.

1. Supabase dashboard → **SQL Editor** → paste and run
   `supabase/migrations/0001_init.sql`. It creates the `cold_email` schema
   (tables plus the API functions) and touches nothing in `public`.
2. Edit the inbox address in `supabase/migrations/0002_seed.sql`, then run it
   the same way. Then run `supabase/migrations/0003_review.sql` (lets you
   see why a lead needs review and retry it). Later migration files are run
   the same way, in number order; each is safe to re-run.
3. **Project Settings → Data API → Exposed schemas** → add `cold_email` → Save.
   Only the secret (service-role) key can use anything in it; the publishable
   key and logged-in users get nothing, and no table is reachable directly.
4. Put `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (the `sb_secret_...`
   key from **Project Settings → API Keys**) in `.env`.

If step 3 is missed, calls fail with a message telling you to do it.

**To undo everything** (e.g. to start the migration over): first remove
`cold_email` from **Exposed schemas**, then run `supabase/revert.sql` in the
SQL Editor. It deletes the `cold_email` schema and all its data, returning the
database to its pre-migration state; `public` is not touched. If anything
outside `cold_email` depends on it, it stops and deletes nothing.

> Supabase free projects pause after 7 days of inactivity. The workers poll
> continuously, so an always-running deployment keeps the project alive by
> itself.

### 2. Configure

```bash
cp .env.example .env
$EDITOR .env
```

At minimum: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, one LLM key, and the SMTP settings plus one
`INBOX_*_SMTP_PASSWORD`.

Plus the three login settings, `APP_PUBLIC_URL`, `CMS_PUBLIC_URL` and
`SSO_CLIENT_SECRET` (see [Login](#login)). The service refuses to start without them.

**Keep `DRY_RUN=true` until the end-to-end test passes.** Everything runs
normally — routing, drafting, scheduling, cap accounting, database writes — but
no mail leaves the machine.

### 3. Register your inboxes

Each mailbox needs a row. The `credential_ref` names an environment-variable
prefix; the password itself is never stored in the database.

```sql
insert into cold_email.inboxes (email, display_name, provider, credential_ref, daily_cap, config)
values ('alex@outreach-domain.com', 'Alex Rivera', 'smtp', 'INBOX_A', 15, '{}'::jsonb);
```

Then set `INBOX_A_SMTP_PASSWORD` in `.env`. For a mailbox on a different
provider, override the host in `config`:

```sql
update cold_email.inboxes
   set config = '{"smtp_host":"smtp.zoho.com","smtp_port":465,"smtp_ssl":true,
                  "smtp_starttls":false,"imap_host":"imap.zoho.com"}'::jsonb
 where email = 'sam@outreach-domain.com';
```

### 4. Run

```bash
docker compose up --build
```

---

## Login

The console signs in through CMS, the same way as the landing page: no separate
accounts or passwords. Only CMS users with the `admin` or `marketing` role get
in. The browser holds only an HttpOnly session cookie (8 hours at most, 30
minutes idle); every change also needs a per-session CSRF token.

- **Sign out** in the console also signs you out of CMS (and every app signed
  in through it). **Signing out of CMS** ends the console session within 60
  seconds, or at once when you return to the console tab.
- CMS `.env`: `COLD_EMAIL_URL` = this console's origin, `COLD_EMAIL_SECRET` =
  this project's `SSO_CLIENT_SECRET`. The console then also appears in the CMS
  sidebar.
- Open the console at exactly `APP_PUBLIC_URL` (`http://localhost:5173` via
  `npm run dev`): the login callback is registered for that origin only.
- Sessions live in the service's memory: run one worker; a restart signs
  everyone out.
- Everything except `/health` needs a session. For scripts and the curl
  examples below, set `API_KEY` and add `-H "Authorization: Bearer $API_KEY"`.
- Inside Docker, the CMS API is reached through `host.docker.internal`
  automatically; set `CMS_API_BASE_URL` only if it lives elsewhere.

Implementation: `service/app/auth.py`, tests in `service/tests/test_auth.py`.

## Verifying it works

Except `/health`, these need `-H "Authorization: Bearer $API_KEY"` (see [Login](#login)).

```bash
# Health and current configuration
curl localhost:8080/health

# Persona routing, without spending a token on drafting
curl 'localhost:8080/route?job_title=Head+of+People+Operations'

# Draft a full sequence for a made-up lead; nothing is persisted
curl -X POST localhost:8080/preview -H 'Content-Type: application/json' -d '{
  "lead": {"email":"test@example.com","first_name":"Dana",
           "company":"Northwind Logistics",
           "job_title":"Chief Information Security Officer",
           "timezone":"Europe/London"}
}'

# Force a worker tick instead of waiting for the interval
curl -X POST localhost:8080/run/intake
curl -X POST localhost:8080/run/sender
curl -X POST localhost:8080/run/poller

# Pipeline state at a glance
curl localhost:8080/stats
```

### Checking the done-when criteria

| Criterion | How to verify |
|---|---|
| Sequences for all 3 personas | Enroll three leads with CISO / IT / HR job-title overrides, run intake, confirm three rows in `cold_email.enrollments` with distinct personas (or use `POST /preview`, which writes nothing) |
| Stop on reply | Reply to a sent email from the prospect address; after one poll cycle the enrollment is `replied` and its remaining steps are `cancelled` |
| Stop on unsubscribe | Reply with "please remove me"; the enrollment goes `unsubscribed` and the address lands in `cold_email.suppression_list` |
| Stop on bounce | Send to a known-dead address; the hard bounce suppresses it and cancels the sequence |
| Spam test | Point one inbox at a Mail-Tester address and run a sequence with `DRY_RUN=false` |

### Tests

```bash
cd service
pip install -r requirements-dev.txt
python -m pytest tests -q          # unit tests, no network, no database
```

These cover the logic that is expensive to get wrong and cheap to test:
business-hours and timezone scheduling, persona keyword precedence, bounce
classification (including the hard/soft distinction), opt-out detection, and
draft validation.

There are also integration tests that call every database function through a
real PostgREST server (the engine behind Supabase's REST API) — claim
locking, atomic cap reservation, suppression matching, cascade cancellation —
and check that the publishable key is locked out. They are skipped unless you
point them at throwaway containers:

```bash
docker run -d --rm --name out01-pgtest -e POSTGRES_PASSWORD=test \
  -p 55432:5432 postgres:16-alpine
# the Supabase roles; PostgREST exits at start-up if it cannot log in
docker exec out01-pgtest psql -U postgres -c "create role anon nologin; \
  create role authenticated nologin; create role service_role nologin; \
  create role authenticator login noinherit password 'test'; \
  grant anon, authenticated, service_role to authenticator;"
docker run -d --rm --name out01-rest --network host \
  -e PGRST_DB_URI=postgres://authenticator:test@localhost:55432/postgres \
  -e PGRST_DB_SCHEMAS=public,cold_email -e PGRST_DB_ANON_ROLE=anon \
  -e PGRST_JWT_SECRET=integration-test-secret-at-least-32-chars \
  -e PGRST_SERVER_PORT=3917 postgrest/postgrest
INTEGRATION_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres \
INTEGRATION_REST_URL=http://localhost:3917 \
  python -m pytest tests -q
```

It creates stand-in `public` lead tables and truncates everything on setup, so
never point it at the shared Supabase database.

---

## How the safety guards are layered

Every send passes five checks, in order:

1. **Kill switch** — `cold_email.settings.sequence_enabled`
2. **Shared lead status** — the lead must still exist in `public.leads` with a
   status in `contactable_lead_statuses`; otherwise the sequence is stopped
3. **Suppression** — checked in the claim query *and again* immediately before
   the send, independently of sequence state, as the plan requires
4. **Business hours** — evaluated in the lead's own timezone; a step outside the
   window is pushed to the next business morning rather than dropped
5. **Daily cap** — reserved atomically per inbox per day via
   `cold_email.reserve_inbox_slot()`, which counts the inbox's emails reserved
   or sent that day; a failed or deferred send gives its slot back

Reply detection matches inbound mail to a lead by `In-Reply-To`/`References`
first, falling back to the sender address, so a reply from a colleague's address
still stops the sequence.

Soft bounces (mailbox full, greylisted) deliberately do **not** suppress the
address — only `5.x.x` status codes and unambiguous hard-bounce language do.
An unclassifiable NDR is treated as transient rather than burning a good lead.

---

## Operational notes

- **Warm-up is manual**, per the plan. Raise `daily_cap` on each inbox row as
  warm-up progresses; start low (5–10) and build toward 15–20 over 2–3 weeks.
  Nothing in the code needs to change.
- **Threading**: steps 2–4 are sent with `In-Reply-To` pointing at the opener,
  so they arrive as a follow-up thread rather than four unrelated cold emails.
- **List-Unsubscribe** is set on every SMTP send (RFC 8058 mailto form), which
  needs no hosted landing page and is weighted positively by mailbox providers.
- **Crash recovery**: steps stuck in `sending` for over 30 minutes are returned
  to `pending` at the start of each sender tick.
- **Concurrency**: every claim uses `FOR UPDATE SKIP LOCKED`, so multiple
  service instances can run against one database without double-sending.


---

## Reply triage (OUT-05)

The console's **Reply triage** workspace handles what happens after a prospect
answers. Every reply (found by the inbox poller) is classified by the AI into
one of six categories and acted on:

| Category | What happens |
|---|---|
| Interested | 2 free slots from the calendar (inside the lead's business hours, on different days) plus the booking link go into a reply draft. When they pick one, it is booked and a confirmation is drafted. |
| Not now | Paused for 60 days, then a fresh sequence starts automatically. A short acknowledgement is drafted. |
| Wrong person | If they name someone with an email address, that person is enrolled ("referred by …" in their first email). Otherwise a draft asks who the right contact is. |
| Objection | A reply addressing it is drafted (a flat "no" gets a graceful close). |
| Out of office | The remaining emails move to the business day after their return date. |
| Unsubscribe | Blocked, drafts cancelled, and `public.leads.status` set to `DNC` so other projects stop too. Never replied to. |

**Drafts** wait in the Drafts tab. Approve, edit or reject them; if nobody
does, they auto-send after `auto_send_delay_minutes` (default 2 hours), moved
into the lead's business hours. If the AI is less than 70% sure, nothing
auto-sends and nothing irreversible happens (no booking, unsubscribe or
snooze) until a person confirms or corrects the category.

**Conversation context**: before classifying a reply or writing an answer,
the AI reads the last 4 emails of the thread (ours and theirs, oldest first,
quoted text removed), so short replies like "yes, Tuesday works" make sense.
The number is "Earlier emails the AI reads" in Reply triage → Settings.

**Setup**
1. Run `supabase/migrations/0006_triage.sql`, then `0007_conversation.sql`
   and `0008_meeting_sync.sql`, in the Supabase SQL Editor. (`0009_lead_browser.sql`
   adds the lead list on the Enroll page.)
2. In `.env`, set `CALENDAR_PROVIDER=calcom` with `CALCOM_API_KEY`,
   `CALCOM_EVENT_TYPE_ID` and `CALCOM_BOOKING_URL` (or `fake` to try it
   without Cal.com), then restart the backend.
3. Check it under **Reply triage → Settings → Calendar → Check free times**.

**Meetings booked through the link**: every 5 minutes the calendar sync
reads Cal.com's bookings. A booking by one of your leads (or anyone, for the
event type in `CALCOM_EVENT_TYPE_ID`) appears on the Meetings page, stops
that lead's sequence and cancels unsent replies; a cancellation puts the lead
back to "replied". **Check calendar now** on the Meetings page runs it at once.

**Drafts never invent a meeting**: an AI reply that names a time we did not
offer, or promises a link or invite with nothing booked, is replaced by the
plain template.

**Accuracy**: Reply triage → Accuracy runs the 50 labelled replies in
`service/app/triage/testset.jsonl` through the classifier (target 95%). From
the command line: `cd service && python -m app.triage.evaluate`.

**Calendar providers** are swappable: `app/calendar/base.py` defines the
interface (free slots, book, booking link). Cal.com and a fake calendar are
implemented; adding Google or Microsoft is one new class plus one line in
`app/calendar/factory.py`, and `tests/test_calendar.py` runs the same
contract tests against it.

**Re-running migrations**: run every file in `supabase/migrations` in number
order. Running an older file on its own puts back that file's older versions
of functions that a later file replaced.

## Email Nurture

Keeps in touch with leads who are interested but not ready: six AI-written
emails over about 30 days, and a hand-off to sales the moment they are ready.
It is the third tab in the console (**Email Nurture**: Dashboard, Leads,
Review, Hand-offs, Content, Settings), code in `service/app/nurture/`, tables
`cold_email.nurture_*` (migrations 0010 and 0011, see supabase/migrations/README.md). Briefs, fallback emails and resources are stored in `cold_email.settings`; the starting text is in `service/app/nurture/content.py`.

**Who joins**: a lead whose score (`public.lead_scores.tier`, else
`leads.lead_score`) changes to Warm or Cold, checked every 5 minutes with an
hourly reconciliation (there are no triggers on the shared tables). Not
joined: unsubscribed, bounced, customers, blocked lead statuses, anyone in the
cold sequence (one sequence at a time, enforced both ways), anyone already in
nurture, or who left nurture in the last 90 days.

**Tracks**: persona (CISO, IT Manager, HR & Compliance: keyword rules, then
the AI; below 70% confidence IT Manager plus a review flag) × Warm/Cold.
Cold: days 0, 5, 10, 16, 23, 30, educational, no pitch before email 4. Warm:
days 0, 3, 7, 12, 19, 28, proof and stronger calls to action. A score change
between Warm and Cold switches track from the next email and recalculates the
dates.

**Writing**: each email is written about a day ahead from its step brief (36,
editable), the lead (as cleaned, delimited data: form fields are never
instructions), approved resources not yet sent, and what the lead clicked.
The AI never writes links: it uses `{{CTA_DEMO}}`, `{{CTA_PRICING}}` and
`{{RESOURCE_LINK}}`, which become tracked links. Every draft passes checks
(word limit, placeholders, no URLs, allowed resource, no prices, discounts,
guarantees or compliance claims), then an AI judge; one retry, then the
pre-approved fallback (18, editable) so the email still goes out on time.

**Sending**: from nurture's own account (`NURTURE_*` in `.env`), never a
cold inbox, in the lead's sending window, with a daily limit, HTML layout,
one-click `List-Unsubscribe` and a Reply-To that Reply Triage reads. Right
before every send the lead is checked again (Hot, unsubscribed, customer,
handed off, in the cold sequence).

**Hand-off** (once, atomically): score becomes Hot, a person clicks pricing or
demo (mail-scanner clicks within 2 minutes of sending are ignored), a reply
Reply Triage reads as interested, or "Hand off now". Unsent emails are
cancelled and sales is emailed an AI summary. Other replies: not now →
continue; out of office → next email at least 5 days later; objection →
stop nurture and flag; unsubscribe → suppressed everywhere and the shared
lead marked DNC; wrong person or unclear → held for a person.

**Safety**: pause-all switch (next cycle), test mode (a day becomes minutes,
only allow-listed addresses), pilot mode (every AI draft waits in Review), and
email alerts for failed jobs and a high fallback rate.

**What it changes elsewhere** (all additive): cold enrollment refuses leads in
active nurture; the poller hands mail about nurture emails to nurture first;
Reply Triage classifies nurture replies with the same classifier but takes
nurture's actions (no reply drafts); the nurture mailbox is an inactive inbox
row the cold sender and poller never use. `OutgoingMessage` gained optional
`body_html` and `unsubscribe_url`, which cold email does not set.

**Before going live**: links and the unsubscribe page use `NURTURE_PUBLIC_URL`.
In test mode the console address works; for real leads point an HTTPS
subdomain at the console and set it, since plain-HTTP links on an IP address
hurt delivery.

**Tests**: `tests/test_nurture_unit.py`, `tests/test_nurture_writing.py` and
`tests/integration/test_nurture.py` cover the 14 acceptance scenarios (2, 3 and
11 as unit tests; the rest end to end in test mode against a real database).

