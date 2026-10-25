# Outreach Platform — Documentation

What each service is, how it works, and where each piece lives.
For step-by-step setup and day-to-day operation, see [runbook.md](./runbook.md).

The platform is one backend (FastAPI, `service/`), one console (React,
`frontend/`) and one database schema (`cold_email` in the shared Supabase
project). It runs three services, each with its own workspace in the console:

| Part | Service | Console workspace | Code |
|---|---|---|---|
| A | Shared platform (database, login, configuration) | — | `service/app/*.py`, `supabase/` |
| B | Cold Email Sequence (OUT-01) | **Cold sequence** | `service/app/graph/`, `workers/intake.py`, `sender.py`, `poller.py` |
| C | Reply Triage (OUT-05) | **Reply triage** | `service/app/triage/`, `calendar/`, `workers/triage.py`, `calendar_sync.py` |
| D | Email Nurture | **Email Nurture** | `service/app/nurture/` |

Each part starts with a `<!-- PART X: … -->` marker so the file can be split by service.

<!-- ======================================================================= -->
<!-- PART A: SHARED PLATFORM                                                 -->
<!-- ======================================================================= -->

# Part A — Shared platform

## A1. The big picture

```
                        ┌──────────────────────────────────────────┐
  Browser               │  Outreach Console (React)                │  frontend/  — Vite, port 5173
  (signed in via CMS) ──▶  Cold sequence · Reply triage · Nurture  │
                        └──────────────────┬───────────────────────┘
                                           │ /api/*  (proxied)
                        ┌──────────────────▼───────────────────────┐
                        │  FastAPI service (Python)                │  service/  — Docker, port 8080
                        │                                          │
                        │  Cold:    intake 5m · sender 3m · poller 10m
                        │  Triage:  triage 1m · calendar sync 5m   │──▶ LLM (Gemini / OmniRoute)
                        │  Nurture: score 5m · write 1m · send 1m  │──▶ SMTP / Graph,  IMAP / Graph
                        │           mailbox 10m · reconcile 1h     │──▶ Cal.com
                        └──────────────────┬───────────────────────┘
                                           │ Supabase REST API (secret key)
                        ┌──────────────────▼───────────────────────┐
                        │  Supabase Postgres                       │
                        │  public.*      READ ONLY (one exception) │  shared with other projects
                        │  cold_email.*  owned here                │
                        └──────────────────────────────────────────┘
```

| Part | Tech | Folder |
|---|---|---|
| Backend + background workers | Python 3.12, FastAPI, APScheduler, LangGraph | `service/` |
| Console | React 19 + Vite | `frontend/` |
| Database schema and functions | SQL migrations for Supabase | `supabase/` |
| Container | Docker Compose (backend only) | `docker-compose.yml` |

All background jobs run in one APScheduler loop inside the backend. Each job is
single-instance: a slow run delays the next one instead of overlapping it.

## A2. The shared database rule

The Supabase database is **shared with other projects**, so the platform is
careful not to touch anything it doesn't own:

- It **reads** `public.leads`, `lead_profiles`, `company_profiles`,
  `prospects`, `lead_scores` and `email_nurture_state`.
- It **writes** only to its own `cold_email` schema. The **single exception**:
  when someone unsubscribes (through Reply Triage or Email Nurture),
  `public.leads.status` is set to `DNC` so other projects stop contacting them too.
- There are **no foreign keys, views or triggers** pointing into `public`, so other
  projects can delete leads or change columns without breaking anything here.
- The service has **no database password**. It calls SQL functions
  (`cold_email.*`) through the Supabase REST API with the secret key. The tables
  themselves are never exposed.

### Migrations

Run in the Supabase SQL Editor, in number order; each is safe to re-run.

| File | Service | What it does |
|---|---|---|
| `0001_init.sql` | Cold | Tables (enrollments, emails, inboxes, inbox events, suppression list, settings) and their functions |
| `0002_seed.sql` | Cold | Your sending inbox (edit the address first) |
| `0003_review.sql` | Cold | "Needs review" list and retry |
| `0004_send_now.sql` | Cold | "Send next email now" |
| `0005_replies.sql` | Cold | Replies list and reply alerts |
| `0006_triage.sql` | Triage | Categories, AI drafts, meetings, referrals, snooze |
| `0007_conversation.sql` | Triage | The last few emails of a thread, for the AI |
| `0008_meeting_sync.sql` | Triage | Meetings booked through the Cal.com link |
| `0009_lead_browser.sql` | Cold | Enroll page: list every lead, enroll or remove with one click |
| `0010_nurture.sql` | Nurture | 3 tables (enrollments, emails, timeline) and their functions |
| `0011_nurture_and_cold.sql` | Nurture + Cold | One sequence at a time: three cold functions get one extra line each |
| `0012_nurture_lead_list.sql` | Nurture | The Leads tab, and "Send now" for testing |

Running an older file on its own puts back that file's older versions of
functions a later file replaced, so always re-run from that file to the end.
`supabase/revert.sql` removes the whole `cold_email` schema.

## A3. Login and access

- The console signs in **through CMS**, with no separate accounts. Only CMS users
  with the **`admin`** or **`marketing`** role get in.
- The browser holds only an HttpOnly session cookie: at most 8 hours, and it
  ends after 30 minutes idle. Every change also needs a CSRF token.
- Signing out of the console signs you out of CMS too. Signing out of CMS ends
  the console session within 60 seconds.
- Sessions are kept **in memory**: run one backend process, and expect a
  restart to sign everyone out.
- **Scripts / curl:** set `API_KEY` in `.env` and send
  `Authorization: Bearer <API_KEY>`.
- Only `/health` and Email Nurture's public link/unsubscribe routes (`/n/…`)
  work without signing in.

## A4. Configuration

| Kind | Where | Change takes effect |
|---|---|---|
| Wiring and secrets | `.env` | After a restart |
| Business behaviour | `cold_email.settings` table (each workspace's **Settings** tab) | Within 60 seconds, no restart |

### Shared `.env` variables

| Variable | Purpose |
|---|---|
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Database access (the `sb_secret_…` key) |
| `LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER` | `gemini` / `omniroute` (fallback also `none`); used by all three services |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Gemini settings |
| `OMNIROUTE_API_KEY`, `OMNIROUTE_BASE_URL`, `OMNIROUTE_MODEL` | OmniRoute gateway |
| `MAIL_SENDER` / `MAIL_READER` | `smtp` or `graph` / `imap` or `graph`, chosen independently |
| `SMTP_HOST/PORT/STARTTLS/SSL` | Default SMTP server |
| `IMAP_HOST/PORT/SSL/FOLDER` | Default IMAP server |
| `GRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET` | Only if you use Microsoft Graph |
| `RUN_WORKERS` | `false` = API only, no background jobs at all |
| `DRY_RUN` | `true` = everything runs, but no mail actually leaves |
| `API_KEY` | Bearer token for scripts |
| `APP_PUBLIC_URL`, `CMS_PUBLIC_URL`, `SSO_CLIENT_SECRET` | **Required** login settings |
| `HOST_PORT` | Backend port on the host (default 8080) |
| `LOG_LEVEL` | `INFO` by default; `DEBUG` for more detail |

Service-specific variables are listed in each part below.

## A5. Shared API

| Method | Path | Does |
|---|---|---|
| GET | `/health` | Database status, providers, whether dry run is on (no login needed) |
| GET | `/settings` · PUT `/settings/{key}` | Read / change a runtime setting `{"value": …}` |
| — | `/auth/start`, `/callback`, `/me`, `/activity`, `/logout` | CMS login flow |

## A6. Shared code map

```
service/app/
  main.py            HTTP routes (cold + triage); nurture routes are in nurture/api.py
  auth.py            CMS login, sessions, CSRF
  config.py          .env settings
  settings_store.py  runtime settings (60 s cache) + defaults
  repository.py      every database call (Supabase RPC)
  db.py              REST client
  scheduling.py      business-hours maths
  regions.py         country/state → timezone
  llm/               gemini.py, omniroute.py, factory.py (primary + fallback)
  mail/              smtp_sender, graph_sender, imap_reader, graph_reader, parsing (bounce/opt-out)
  workers/scheduler.py  every background job
service/tests/       unit tests + integration tests (need throwaway Postgres + PostgREST)
supabase/migrations/ 0001 → 0012, run in order
supabase/revert.sql  removes the whole cold_email schema
frontend/src/        App.jsx, api.js, components/ (cold), components/triage/, components/nurture/
```

## A7. Platform limitations

- **One backend process only.** Login sessions live in memory. (The workers
  themselves are safe to run concurrently.)
- **The console isn't in Docker.** Compose runs only the backend; the console runs
  with `npm run dev` or as a static build behind a web server.
- **Deviation from the original plan:** the sending and polling cron jobs run in
  the Python service, not in Supabase Edge Functions, because Deno has no
  usable SMTP/IMAP client.

<!-- ======================================================================= -->
<!-- PART B: COLD EMAIL SEQUENCE (OUT-01)                                    -->
<!-- ======================================================================= -->

# Part B — Cold Email Sequence (OUT-01)

## B1. What it does, in one paragraph

You pick existing leads from the shared Supabase `leads` table and **enroll** them.
For each enrolled lead the service works out who they are (security, IT or HR),
has an AI write a **4-email sequence** for that persona, and schedules each email
inside the lead's **own local business hours**. A background sender delivers the
emails while respecting **per-inbox daily limits**. A background poller reads the
inboxes, and the moment a lead **replies, bounces or unsubscribes**, everything
still unsent for that lead is cancelled. Each reply triggers an **alert email**
and is handed to Reply Triage (Part C).

## B2. Tables

| Table | Holds |
|---|---|
| `enrollments` | One row per lead in outreach: status, persona, overrides, last error |
| `emails` | Every drafted email: subject, body, when it's due, and whether it was sent |
| `inboxes` | The sending mailboxes, their daily cap, and the name of their password variable |
| `inbox_events` | Replies, bounces, opt-outs and auto-replies found by the poller |
| `suppression_list` | Addresses and domains that must never be emailed (used by all services) |
| `settings` | Every runtime setting (all services) |

## B3. A lead's journey

```
 enroll ──▶ ready_for_outreach ──▶ processing ──▶ sequence_ready ──▶ sending ──▶ sent
                                        │                                │
                                        ▼                                ▼
                                  manual_review / failed     replied · bounced · unsubscribed · stopped
```

| Status | Console label | Meaning |
|---|---|---|
| `ready_for_outreach` | Waiting to draft | Enrolled; the next intake run will pick it up |
| `processing` | Drafting | The AI is writing the sequence |
| `sequence_ready` | Scheduled | All 4 emails are written and have send times |
| `sending` | Sending | At least one email has gone out |
| `sent` | Sequence finished | All emails sent, no reply |
| `replied` | Replied | A human replied, so the rest is cancelled and Reply Triage takes over |
| `bounced` | Bounced | Hard bounce, so the address is suppressed |
| `unsubscribed` | Unsubscribed | Opted out (or blocked manually), so the address is suppressed |
| `stopped` | Stopped | Removed by hand, or another project changed the lead's status (e.g. `DNC`, `Paid`) |
| `manual_review` | Needs review | The AI draft failed checks twice; see `last_error` |
| `failed` | Failed | Something broke (no email, lead deleted, LLM error…) |

Each email has its own status: `pending` → `sending` → `sent`, or `cancelled` / `failed`.

## B4. How each step works

### B4.1 Enrollment — who gets in

A lead enters outreach only through `POST /enroll` (the **Enroll** page), or
through **auto-enroll** if you switch it on for chosen `leads.source` values.

A lead is refused, with the reason shown, when it:

| Outcome | Why |
|---|---|
| `not_found` | No lead with that `lead_id` / `id` |
| `no_email` | The lead has no email address |
| `not_contactable (<status>)` | `leads.status` isn't in `contactable_lead_statuses` |
| `in_email_nurture` | The lead is in another email journey (`email_nurture_state`, or active in Email Nurture) |
| `suppressed` | The address or domain is on the suppression list |
| `already_enrolled` | The address is already in outreach (one sequence per address) |

When enrolling you can override **job title**, **company** and **timezone** if
the shared data is missing or wrong. **Remove** takes a lead out: unsent emails
and unsent reply drafts are cancelled, and the lead can be enrolled again later.

### B4.2 Lead details — where the data comes from

Nothing is copied. Details are read fresh each time:

| Field | Taken from (first match wins) |
|---|---|
| Email, name, status | `leads` |
| Company | enrollment override → `lead_profiles` → `company_profiles` → `prospects` |
| Job title | enrollment override → `prospects.title` |
| Timezone | override → `leads.timezone` (unless it's just the default `America/New_York`) → state/province → country → UTC |

### B4.3 Persona routing — which angle

| Persona | Who | Angle |
|---|---|---|
| `ciso` | Security, risk, compliance, audit, privacy | Risk and compliance (SOC 2, ISO 27001, NIS2) |
| `it` | IT managers, sysadmins, infrastructure, DevOps | Phishing simulation with low admin overhead |
| `hr` | HR, People Ops, L&D, talent | Training completion without nagging staff |

Routing order:
1. **Keyword match** on the job title. Security keywords are checked first, so
   "Security Awareness Training Manager" goes to `ciso`, not `hr`.
2. **Generic exec** titles (CEO, founder, MD…) go to `ciso`.
3. Otherwise the **LLM classifies** the title.
4. No title at all goes to `ciso`.

Check any title without drafting: **Tools** page, or `GET /route?job_title=...`.

### B4.4 Drafting — the AI pipeline (LangGraph)

```
persona ──▶ draft ──▶ validate ──┬── ok ─────────────▶ scheduled
                        ▲        ├── failed, 1st try ──┐
                        └────────┼─────────────────────┘  (retry with the errors)
                                 └── failed, 2nd try ──▶ manual_review
```

- One LLM call writes all 4 emails as JSON.
- **Validation** rejects a draft that has the wrong number of emails, empty or
  over-long subjects, duplicate subjects, spam words (`free`, `urgent`,
  `guarantee`…), bodies outside 120–2200 characters, or leftover placeholders
  like `[First Name]` or `{{company}}`.
- On the first failure the model gets the list of errors and tries once more.
  If it fails again, the lead goes to **Needs review**.

### B4.5 Scheduling — local business hours

- Default window: **09:00–17:00, Mon–Fri, in the lead's own timezone**.
- `business_hours_by_region` changes it per country or timezone. It ships with
  **Sun–Thu weeks** for Bangladesh, Saudi Arabia, Israel, Egypt, Qatar, Kuwait,
  Oman, Bahrain, Jordan, Iraq and Algeria.
- Step spacing: `step_gaps_business_days` = `[0, 3, 7, 12]`, which means email 1
  goes on day 0, email 2 three business days later, and so on. The **length of
  this list is the sequence length.**
- An email that comes due outside business hours is **moved to the next
  business morning**, never dropped.

### B4.6 Sending — five safety checks

Every single email passes these checks in order. If any one fails, it isn't sent.

| # | Check | If it fails |
|---|---|---|
| 1 | **Kill switch** `sequence_enabled` is on | Nothing is sent |
| 2 | Lead still exists and its **shared status** is contactable | Sequence `stopped` |
| 3 | Address **not suppressed** (checked again right before sending) | Sequence `unsubscribed` |
| 4 | It's **business hours** where the lead is | Moved to the next business morning |
| 5 | An inbox has room under its **daily cap** | Moved to the next business morning |

Other sending details:
- Inboxes are used **round-robin**; the cap is reserved atomically, so two
  workers can never overshoot it.
- Emails 2–4 are sent as **replies in the same thread** as email 1.
- Every email carries a **List-Unsubscribe** header (a mailto link), which
  mailbox providers treat as a good sign.
- A synchronous "recipient refused" counts as a hard bounce.
- Failed sends are retried up to `max_send_attempts` (3).
- An email stuck in `sending` for 30+ minutes (after a crash) goes back to `pending`.
- **Send now** (Overview page) skips the business-hours wait only. Every other
  check still applies, and a lead emailed in the last 24 hours is skipped.

### B4.7 Reply detection — the poller

Each inbox is polled (IMAP or Graph). Each new message is matched to a lead,
first by email threading headers and then by sender address, so a reply from a
colleague's address still counts. Mail about a nurture email is handed to
Email Nurture first (Part D). The rest is classified:

| Type | What happens |
|---|---|
| **Hard bounce** (`5.x.x`) | Address suppressed, sequence cancelled → `bounced` |
| **Soft bounce** (mailbox full, greylisting) | Logged only; the sequence continues |
| **Auto-reply** (out of office) | Logged; Reply Triage may move the remaining emails past the return date |
| **Unsubscribe** ("remove me", "unsubscribe"…) | Address suppressed, sequence cancelled → `unsubscribed` |
| **Human reply** | Sequence cancelled → `replied`, an alert email to `reply_alert_email`, and the reply is queued for Reply Triage |

Mail that doesn't belong to an enrolled lead is ignored and not stored.
The first time an inbox is polled, the mail already in it is skipped.

## B5. Configuration

### `.env`

| Variable | Purpose |
|---|---|
| `INBOX_<REF>_SMTP_PASSWORD` | One per inbox (see B5.3); optional `_IMAP_PASSWORD` |
| `UNSUBSCRIBE_MAILTO`, `REPLY_TO` | Optional sending identity overrides |
| `INTAKE/SEND/POLL_INTERVAL_SECONDS` | Worker timing (300 / 180 / 600); the reply check also runs once at startup |

### B5.2 Runtime settings (`cold_email.settings`)

| Key | Default | Meaning |
|---|---|---|
| `sequence_enabled` | `true` | **Kill switch** for all cold sending |
| `business_hours` | 9–17, Mon–Fri | Default send window (weekdays: 0 = Mon … 6 = Sun) |
| `business_hours_by_region` | Sun–Thu for 11 countries | Per-country / per-timezone changes |
| `step_gaps_business_days` | `[0,3,7,12]` | Spacing and length of the sequence |
| `default_daily_cap` | `15` | Per-inbox cap if the inbox row doesn't set one |
| `intake_batch_size` / `send_batch_size` | `10` / `25` | Leads drafted / emails sent per run |
| `max_send_attempts` | `3` | Retries before an email is marked failed |
| `llm_min_body_chars` / `llm_max_body_chars` | `120` / `2200` | Draft length limits |
| `contactable_lead_statuses` | New, No Answer, Busy, Call Later, In Progress | Which `leads.status` values may be emailed |
| `skip_if_in_email_nurture` | `true` | Refuse leads already in another journey |
| `auto_enroll` | off | `{"enabled", "sources", "batch_size"}` |
| `reply_alert_email` | empty | Who gets an email for each new reply |

### B5.3 Inboxes

Each sending mailbox is a row in `cold_email.inboxes`. Its `credential_ref`
(e.g. `INBOX_A`) points to the password variable in `.env`
(`INBOX_A_SMTP_PASSWORD`). **Passwords never go in the database.** An inbox on a
different provider can override host, port and TLS in its `config` JSON.

## B6. API

| Method | Path | Does |
|---|---|---|
| GET | `/stats` | Pipeline counts and inbox usage |
| POST | `/enroll` | Enroll leads `{"leads":[{"lead_id":"L-1042","job_title":"…"}]}` |
| GET | `/leads?search=&view=all&limit=50&offset=0` | Shared leads with their sequence state; `view`: all, not_enrolled, available, in_sequence, finished |
| GET | `/enrollments?status=manual_review,failed` | List enrollments with `last_error` |
| POST | `/enrollments/remove` | Take leads out `{"lead_ids":["<uuid>"]}`; unsent emails and drafts are cancelled |
| POST | `/enrollments/{id}/retry` | Re-draft a `manual_review` / `failed` / `stopped` lead, optionally correcting data |
| POST | `/preview` | Draft a sequence for a made-up lead; nothing is saved or sent |
| GET | `/route?job_title=…` | Which persona a title gets |
| GET | `/upcoming` | Next scheduled emails (UTC) |
| POST | `/send-now` (`?lead_id=`) | Send the next email now, ignoring business hours only |
| GET | `/replies?type=reply,unsubscribe,bounce,auto_reply` or `all` | Inbound events |
| POST | `/replies/test-alert` | Send a sample reply alert |
| POST | `/suppress?email=…&reason=…` | Block an address and cancel its sequence |
| POST | `/run/intake`, `/run/sender`, `/run/poller` | Run a worker immediately |

## B7. Console — Cold sequence workspace

| Tab | Use it to |
|---|---|
| **Overview** | See the numbers, pipeline, inbox usage vs. cap, leads that need attention (with **Retry**); run workers; **Send now**. Refreshes every 30 s |
| **Enroll** | Browse every shared lead with its sequence state; enroll or remove; enroll by ID with corrections |
| **Preview** | Try the AI on a made-up lead; nothing is saved |
| **Settings** | The on/off switch for sending, the reply alert address, and every runtime setting |
| **Tools** | Block an address; check a job title's persona |

## B8. Code map

```
service/app/
  graph/             LangGraph: persona.py, draft.py, validate.py, prompts.py, build.py
  workers/           intake.py, sender.py, poller.py, alerts.py
frontend/src/components/
  Dashboard.jsx, NeedsAttention.jsx, Enroll.jsx, Preview.jsx, Settings.jsx, Tools.jsx
```

## B9. Known limitations

- **No signature is added to emails.** The drafting prompt tells the AI that a
  signature and footer "are appended by the sending system", but no code
  appends one. Emails go out with just the AI body and the sender's display name.
- **Warm-up is manual.** Raise each inbox's `daily_cap` yourself over 2–3 weeks.

<!-- ======================================================================= -->
<!-- PART C: REPLY TRIAGE (OUT-05)                                           -->
<!-- ======================================================================= -->

# Part C — Reply Triage (OUT-05)

## C1. What it does, in one paragraph

Reply Triage handles what happens after a prospect answers. The cold poller
(Part B) has already done the safe, immediate part: stopped the sequence and
alerted a person. Triage then has the AI **read the reply in the context of the
thread**, put it in one of **six categories**, take that category's action
(offer meeting times, book a slot, snooze, enroll a referral, unsubscribe…) and
**write a reply draft**. Drafts wait for a person to approve, edit or reject, and
otherwise send themselves after a delay inside the lead's business hours.
Meetings booked through the Cal.com link are also picked up.

## C2. Tables

| Table | Holds |
|---|---|
| `inbox_events` (from Part B) | Each reply, plus its triage result: category, confidence, notes |
| `reply_drafts` | AI-written replies: kind, text, offered slots, status, auto-send time |
| `meetings` | Bookings made from a reply or through the booking link |
| `contacts` | People referred to us who are not in the shared `leads` table |

Draft status: `pending` → `approved` → `sending` → `sent`, or `rejected` /
`cancelled` / `failed`. A draft with no auto-send time never sends on its own.

## C3. How a reply is handled

The triage worker runs every minute. Each run:

1. **Wakes snoozed leads** whose "not now" period is over: they get a fresh sequence.
2. **Triages new replies**: reads the conversation → classifies → plans → acts → drafts.
3. **Sends drafts** that were approved, or that reached their auto-send time.

### C3.1 Conversation context

Before classifying a reply or writing an answer, the AI reads the last
`context_messages` (4) emails of the thread, ours and theirs, oldest first,
quoted text removed. So short replies like "yes, Tuesday works" make sense.

### C3.2 Categories and actions

| Category | What happens |
|---|---|
| **Interested** | Free slots from the calendar (inside the lead's business hours, on different days) plus the booking link go into a reply draft. When they pick one, it is booked and a confirmation is drafted. A time we did not offer is booked if free, otherwise new slots are offered |
| **Not now** | Paused for `not_now_days` (60), then a fresh sequence starts automatically. A short acknowledgement is drafted |
| **Wrong person** | If they name someone with an email address, that person is enrolled ("referred by …" in their first email). Otherwise a draft asks who the right contact is |
| **Objection** | A reply addressing it is drafted (a flat "no" gets a graceful close) |
| **Out of office** | The remaining emails move to the business day after their return date (or `ooo_default_days` if none is given) |
| **Unsubscribe** | Suppressed, drafts cancelled, and `public.leads.status` set to `DNC`. Never replied to |
| *other* | Held for a person |

### C3.3 Confidence and safety

- If the AI is less than `min_confidence` (70%) sure, **nothing irreversible
  happens** (no booking, suppression, DNC or snooze), the draft never
  auto-sends, and the reply is marked for a person. They confirm or correct the
  category (**Re-run**) and triage runs again with that category.
- **Drafts never invent a meeting**: an AI reply that names a time we did not
  offer, or promises a link or invite with nothing booked, is replaced by a plain template.
- A draft is cancelled at send time if the address has since been suppressed or
  the lead marked `DNC`.

### C3.4 Drafts and auto-send

Draft kinds: `meeting_offer`, `booking_confirmation`, `slot_unavailable`,
`objection_reply`, `not_now_ack`, `referral_ack`, `referral_ask`. A draft of a
kind listed in `auto_send_kinds` sends after `auto_send_delay_minutes` (120)
unless someone acts first, moved into the lead's business hours. Approving sends
it at the next run (within a minute). Drafts are sent from the inbox the reply
arrived at, in the same thread.

### C3.5 Meetings booked through the link

Every 5 minutes the calendar sync reads the calendar's bookings. A booking by one
of your leads (or anyone, for the Cal.com event type or Bookings service) appears on
the Meetings page, stops that lead's sequence and cancels unsent replies. A
cancellation puts the lead back to "replied".

### C3.6 Calendar providers

`app/calendar/base.py` defines the interface (free slots, book, booking link).
Cal.com, Microsoft Bookings (round robin over the service's staff) and a fake
in-memory calendar are implemented; `CALENDAR_PROVIDER` picks one and nothing
outside `app/calendar/` knows which. Adding Google is one new class plus one line in `app/calendar/factory.py`;
`tests/test_calendar.py` runs the same contract tests against it.

### C3.7 Accuracy

**Accuracy** runs the 50 labelled replies in `service/app/triage/testset.jsonl`
through the classifier (target 95%). From the command line:
`cd service && python -m app.triage.evaluate`.

## C4. Configuration

### `.env`

| Variable | Purpose |
|---|---|
| `CALENDAR_PROVIDER` | `calcom`, `microsoft`, `fake` (for testing) or `none` (drafts ask the lead for times) |
| `CALCOM_API_KEY` | Cal.com API key |
| `CALCOM_EVENT_TYPE_ID` | The event type's number, or its slug (a slug also needs the username) |
| `CALCOM_BOOKING_URL` | Public booking page put in emails, e.g. `https://cal.com/you/30min` |
| `CALCOM_USERNAME` | Only for a slug when `CALCOM_BOOKING_URL` is not set |
| `MSBOOKINGS_BUSINESS_ID` | Microsoft Bookings page id (its mailbox address); signs in with `GRAPH_*` |
| `MSBOOKINGS_SERVICE_ID` | The Bookings service leads book |
| `MSBOOKINGS_STAFF_IDS` | Optional round-robin order; empty = the service's staff |
| `MSBOOKINGS_BOOKING_URL` | Optional link for emails; empty = the service's Bookings page |
| `MSBOOKINGS_TIMEZONE` | The booking page's IANA timezone |
| `TRIAGE_INTERVAL_SECONDS` | Triage worker (60) |
| `CALENDAR_SYNC_INTERVAL_SECONDS` | Calendar sync (300) |

### Runtime setting `triage` (Reply triage → Settings)

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Off: replies still stop sequences and alert you, but are not classified and no drafts send |
| `auto_send_delay_minutes` | `120` | How long a draft waits before sending itself |
| `auto_send_kinds` | all 7 kinds | Which draft kinds may auto-send |
| `min_confidence` | `0.7` | Below this, a person decides |
| `not_now_days` | `60` | Snooze length for "not now" |
| `ooo_default_days` | `7` | Out of office with no return date |
| `context_messages` | `4` | Earlier emails the AI reads |
| `meeting.slots_to_offer` / `days_ahead` / `min_notice_hours` | `2` / `7` / `12` | How meeting times are picked |

## C5. API

| Method | Path | Does |
|---|---|---|
| GET | `/triage/stats` | Counts by category, drafts by status, reply-to-meeting rate |
| GET | `/triage/events` | Replies with their triage result, newest first |
| POST | `/triage/events/{id}/rerun` | Triage a reply again, optionally with the correct category |
| GET | `/triage/drafts` | Drafts |
| PUT | `/triage/drafts/{id}` | Edit a draft |
| POST | `/triage/drafts/{id}/approve` · `reject` | Decide a draft |
| GET | `/triage/meetings` | Booked meetings, plus leads offered times who have not answered |
| GET | `/triage/snoozed` | "Not now" leads and when they restart |
| POST | `/run/triage`, `/run/calendar-sync` | Run a worker immediately |
| POST | `/triage/evaluate` · GET `/triage/evaluation` | Run / read the accuracy test |
| GET | `/calendar/status`, `/calendar/slots` | Calendar configuration check; next free slots (read-only) |

## C6. Console — Reply triage workspace

| Tab | Use it to |
|---|---|
| **Replies** | Every reply with its category and confidence; correct a category and re-run |
| **Drafts** | Approve, edit or reject reply drafts before they auto-send |
| **Meetings** | Booked meetings and leads offered times; **Check calendar now** |
| **Follow-ups** | Snoozed "not now" leads and when they restart |
| **Accuracy** | Run the 50-reply test set |
| **Settings** | The `triage` setting; calendar check (**Check free times**) |

## C7. Code map

```
service/app/
  triage/            classify.py, plan.py (pure rules), drafting.py, conversation.py,
                     graph.py, evaluate.py, testset.jsonl
  calendar/          base.py, calcom.py, msbookings.py, fake.py, factory.py, slots.py
  workers/           triage.py, calendar_sync.py
frontend/src/components/triage/
  TriageReplies.jsx, Drafts.jsx, Meetings.jsx, Accuracy.jsx, TriageSettings.jsx
```

<!-- ======================================================================= -->
<!-- PART D: EMAIL NURTURE                                                   -->
<!-- ======================================================================= -->

# Part D — Email Nurture

## D1. What it does, in one paragraph

Email Nurture keeps in touch with leads who are interested but not ready:
**six AI-written emails over about 30 days**, and a **hand-off to sales** the
moment they are ready. A lead joins when its score becomes Warm or Cold, gets
a track by persona × temperature, and receives emails written a day ahead,
checked, and sent in its own sending window. A click on pricing or demo, an
interested reply or a Hot score hands the lead to sales with an AI summary.

## D2. Tables

| Table | Holds |
|---|---|
| `nurture_enrollments` | One row per lead journey: persona, temperature, status, step (0–6), next send, review flag, exit reason, hand-off summary |
| `nurture_messages` | Each email: step, status, send time, subject, preheader, body, resource, source (ai / fallback / edited), AI log |
| `nurture_events` | The timeline: enrolled, click, track change, persona, paused, handoff, exit… |
| `settings` | Keys `nurture`, `nurture_briefs`, `nurture_fallbacks`, `nurture_resources` |

`inbox_events.nurture_enrollment_id` links a reply to a nurture journey (its
`lead_id` stays empty, so Reply Triage's own queue never picks it up).

Enrollment status: `active`, `paused`, `held` (waiting for a person),
`handed_off`, `completed`, `exited`. A lead is live in nurture at most once.

Email status: `writing` → `needs_approval` (pilot mode) → `ready` → `sending`
→ `sent`, or `rejected` / `cancelled` / `failed`.

## D3. How it works

### D3.1 Who joins

A lead whose score (`public.lead_scores.tier`, else `leads.lead_score`)
**changes** to Warm or Cold, checked every 5 minutes with an hourly
reconciliation (there are no triggers on the shared tables). Leads already Warm
or Cold when nurture is switched on join only through **Enroll eligible leads**.

Not joined: unsubscribed, bounced, customers, `blocked_lead_statuses`, anyone in
the cold sequence (one sequence at a time, enforced both ways), anyone already
in nurture, or anyone who left nurture in the last `cooldown_days` (90).

### D3.2 Tracks

Persona (CISO, IT Manager, HR & Compliance: keyword rules, then the AI; below
70% confidence, IT Manager plus a review flag) × temperature:

| Track | Days | Style |
|---|---|---|
| Cold | 0, 5, 10, 16, 23, 30 | Educational, no pitch before email 4 |
| Warm | 0, 3, 7, 12, 19, 28 | Proof and stronger calls to action |

A score change between Warm and Cold switches track from the next email and
recalculates the dates.

### D3.3 Writing

Each email is written about `generate_hours_ahead` (24) before it is due, from:
its step brief (36, editable), the lead (as cleaned, delimited data: form fields
are never instructions), approved resources not yet sent, and what the lead
clicked. The AI never writes links: it uses `{{CTA_DEMO}}`, `{{CTA_PRICING}}`
and `{{RESOURCE_LINK}}`, which become tracked links.

Every draft passes checks (subject and word limits, no raw URLs or HTML, known
placeholders only, allowed resource, no banned phrases, no prices or discounts),
then an **AI judge** (`judge_min_score` 0.7). One retry, then the
**pre-approved fallback** (18, editable) so the email still goes out on time.

### D3.4 Sending

- Sent over SMTP from `NURTURE_FROM_EMAIL` if set, otherwise from the **first
  active cold inbox** (shared reputation and daily limit; the dashboard warns).
- In the lead's `sending_window` (lead's timezone, else `default_timezone`),
  up to `daily_cap` (50) a day.
- HTML layout, one-click `List-Unsubscribe` (RFC 8058) pointing at the
  unsubscribe page, and a Reply-To that Reply Triage reads.
- Right before every send the lead is checked again (Hot, unsubscribed,
  customer, handed off, in the cold sequence).
- `APP_ENV=dev`: emails are written but only sent with **Send now** (Review tab).

### D3.5 Hand-off and replies

**Hand-off** (once, atomically) happens when: the score becomes Hot; a person
clicks pricing or demo (clicks within `bot_click_seconds` (2 minutes) of sending
are mail scanners and ignored); Reply Triage reads a reply as interested; or
someone presses **Hand off now**. Unsent emails are cancelled and `sales_email`
gets an AI summary.

Other replies (classified by Reply Triage's classifier, but nurture's actions
are taken and no reply drafts are written):

| Reply | Action |
|---|---|
| Not now | Continue |
| Out of office | Next email at least `ooo_delay_days` (5) later |
| Objection | Stop nurture and flag |
| Unsubscribe | Suppressed everywhere, shared lead marked `DNC` |
| Wrong person / unclear | Held for a person |

### D3.6 Safety

- **Pause-all** switch (takes effect next cycle).
- **Test mode**: a day becomes `minutes_per_day` minutes and only allow-listed
  addresses are emailed.
- **Pilot mode** (`approval.mode = all`): every AI draft waits in Review;
  `sample` sends a random share there. A rejected draft is rewritten; after two
  rejections the fallback is used.
- **Alerts** to `alert_email` for failed jobs and a fallback rate above
  `fallback_alert_rate` (30%).

### D3.7 What it changes in the other services (all additive)

- Cold enrollment refuses leads in active nurture.
- The cold poller hands mail about nurture emails to nurture first.
- Reply Triage classifies nurture replies with the same classifier but takes
  nurture's actions.
- `OutgoingMessage` gained optional `body_html` and `unsubscribe_url`, which
  cold email does not set.

## D4. Configuration

### `.env`

| Variable | Purpose |
|---|---|
| `APP_ENV` | `production` = send on schedule; `dev` = only with **Send now** |
| `NURTURE_FROM_EMAIL`, `NURTURE_FROM_NAME`, `NURTURE_SMTP_PASSWORD` | Separate sending account (empty = first active cold inbox) |
| `NURTURE_SMTP_HOST/PORT`, `NURTURE_IMAP_HOST`, `NURTURE_IMAP_PASSWORD` | Only when they differ from the `SMTP_*` / `IMAP_*` defaults |
| `NURTURE_REPLY_TO` | An inbox Reply Triage reads (empty = first active cold inbox) |
| `NURTURE_PUBLIC_URL` | Base for tracked links and the unsubscribe page (empty = `APP_PUBLIC_URL` + `/api`). Use an HTTPS subdomain for real leads |
| `NURTURE_WORKERS` | `false` = no nurture jobs (the tab still works) |
| `NURTURE_ENROLL/GENERATE/SEND/POLL/RECONCILE_INTERVAL_SECONDS` | 300 / 60 / 60 / 600 / 3600 |

### Runtime setting `nurture` (Email Nurture → Settings)

| Field | Default | Meaning |
|---|---|---|
| `paused` | `false` | Pause-all |
| `auto_enroll` | `false` | Enroll on score change (off: only the Enroll button on Leads enrolls) |
| `test_mode` | off, 1 min/day, empty list | Test mode |
| `cadence` | see D3.2 | Days per track |
| `cooldown_days` | `90` | Wait before a lead may rejoin |
| `persona_min_confidence` | `0.7` | Below: IT Manager + review flag |
| `banned_phrases` | discount, guarantee, … | Rejected in drafts |
| `sending_window`, `default_timezone` | 9–17 Mon–Fri, `America/New_York` | When emails go out |
| `daily_cap` / `batch_size` | `50` / `20` | Sending limits |
| `generate_hours_ahead` | `24` | How early emails are written |
| `approval` | `none`, sample 0.2 | Review mode: `none` / `all` / `sample` |
| `judge_min_score`, `max_ai_attempts`, `max_send_attempts` | `0.7`, `2`, `3` | Quality and retries |
| `ooo_delay_days`, `bot_click_seconds` | `5`, `120` | Reply / click handling |
| `blocked_lead_statuses` | DNC, Paid, Payment Pending, Consultation Booked, Escalated to Human | Never nurtured |
| `sales_email`, `alert_email` | empty (= `reply_alert_email`) | Hand-off and error notifications |
| `branding` | company name, address, logo, sender, demo / pricing URL | Email layout and links |
| `token_prices` | per million tokens | Cost estimate on the dashboard |

Briefs, fallbacks and resources start from `service/app/nurture/content.py`;
edits are stored in `cold_email.settings`.

## D5. API

| Method | Path | Does |
|---|---|---|
| GET | `/nurture/status` | What stops nurture from sending, warnings, test mode, pause switch |
| GET | `/nurture/stats?start=&end=` | Dashboard: KPIs, persona × track, per-email performance, AI health |
| GET | `/nurture/leads?search=&view=` | Shared leads with their nurture state; `view`: all, can_join, in_nurture, finished |
| GET | `/nurture/enrollments?search=&status=live&persona=&temperature=&review=` | Nurture journeys |
| GET | `/nurture/enrollments/{id}` | One lead's timeline: emails (full text), clicks, replies, events |
| POST | `/nurture/enrollments/{id}/pause` · `resume` · `remove` · `handoff` · `reviewed` · `persona` | Manual actions |
| GET · POST | `/nurture/eligible` · `/nurture/enroll` | Count / enroll Warm and Cold leads that may join |
| GET | `/nurture/review` | Drafts waiting for approval and emails due in 3 days |
| PUT · POST | `/nurture/messages/{id}` · `/nurture/messages/{id}/approve` · `reject` · `send-now` | Edit / decide / send a draft (`send-now` only with `APP_ENV=dev`) |
| GET | `/nurture/handoffs` | Hand-offs to sales with trigger and AI summary |
| GET · PUT | `/nurture/content/briefs`, `/nurture/content/fallbacks` | The 36 briefs and 18 fallback emails |
| GET · POST · PUT · DELETE | `/nurture/content/resources[/{id}]`, POST `/nurture/content/resources/auto-tag` | Resource library and AI tag suggestions |
| GET · PUT | `/nurture/settings` | Nurture settings |
| POST | `/nurture/run/{score,reconcile,generate,send,poll}` | Run a nurture job now |
| GET | `/n/c/{email id}/{demo,pricing,resource}` (public) | Tracked link: logs the click, redirects |
| GET · POST | `/n/u/{enrollment id}` (public) | Unsubscribe page / one-click unsubscribe |

## D6. Console — Email Nurture workspace

| Tab | Use it to |
|---|---|
| **Dashboard** | KPIs, performance, AI health; a red **Not sending** box lists what is missing |
| **Leads** | Every shared lead with its nurture state; enroll, pause, resume, remove, hand off, change persona; open a timeline |
| **Review** | Approve, edit or reject drafts (pilot mode); emails due soon; **Send now** in dev |
| **Hand-offs** | Leads handed to sales, with trigger and summary |
| **Content** | Briefs, fallback emails, resource library |
| **Settings** | Pause-all, test mode, review mode, branding and links, limits |

## D7. Code map

```
service/app/nurture/
  api.py        console + public routes        enroll.py   score checks, reconcile
  jobs.py       write and send jobs            writer.py   AI writing + judge
  checks.py     draft rules                    render.py   HTML layout, tracked links
  cadence.py    tracks and dates               persona.py  persona routing
  sending.py    sending identity, problems     replies.py  nurture mailbox, reply handling
  exits.py      hand-off, unsubscribe, exits   safety.py   pre-send checks
  options.py    settings + defaults            content.py  starting briefs, fallbacks, resources
  alerts.py     job-failure / fallback alerts  repo.py     database calls
frontend/src/components/nurture/
  NurtureDashboard.jsx, NurtureLeads.jsx, NurtureReview.jsx, NurtureContent.jsx, NurtureSettings.jsx
```

## D8. Tests and limitations

- `tests/test_nurture_unit.py`, `tests/test_nurture_writing.py` and
  `tests/integration/test_nurture.py` cover the 14 acceptance scenarios.
- **Before going live** set `NURTURE_PUBLIC_URL` to an HTTPS subdomain: plain-HTTP
  links on an IP address hurt delivery.
- Sharing the cold inbox is allowed but means shared reputation and daily
  sending limit; set `NURTURE_FROM_EMAIL` to separate them.
