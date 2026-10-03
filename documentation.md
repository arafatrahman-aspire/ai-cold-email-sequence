# Cold Email Automation (OUT-01) — Documentation

What the system is, how it works, and where each piece lives.
For step-by-step setup and day-to-day operation, see [runbook.md](./runbook.md).

---

## 1. What it does, in one paragraph

You pick existing leads from the shared Supabase `leads` table and **enroll** them.
For each enrolled lead the service works out who they are (security, IT or HR),
has an AI write a **4-email sequence** for that persona, and schedules each email
inside the lead's **own local business hours**. A background sender delivers the
emails while respecting **per-inbox daily limits**. A background poller reads the
inboxes, and the moment a lead **replies, bounces or unsubscribes**, everything
still unsent for that lead is cancelled. For each reply, someone gets an
**alert email** and takes over by hand.

---

## 2. The big picture

```
                        ┌─────────────────────────────┐
  Browser               │  Outreach Console (React)   │  frontend/  — Vite, port 5173
  (signed in via CMS) ──▶  Overview · Replies · Enroll │
                        │  Preview · Settings · Tools │
                        └──────────────┬──────────────┘
                                       │ /api/*  (proxied)
                        ┌──────────────▼──────────────┐
                        │  FastAPI service (Python)   │  service/  — Docker, port 8080
                        │                             │
                        │  every 5 min  intake  ──────┼──▶ LLM (Gemini / OmniRoute)
                        │  every 3 min  sender  ──────┼──▶ SMTP or Microsoft Graph
                        │  every 7 min  poller  ◀─────┼─── IMAP or Microsoft Graph
                        └──────────────┬──────────────┘
                                       │ Supabase REST API (secret key)
                        ┌──────────────▼──────────────┐
                        │  Supabase Postgres          │
                        │  public.*      READ ONLY    │  shared with other projects
                        │  cold_email.*  owned here   │
                        └─────────────────────────────┘
```

| Part | Tech | Folder |
|---|---|---|
| Backend + background workers | Python 3.12, FastAPI, APScheduler, LangGraph | `service/` |
| Console | React 19 + Vite | `frontend/` |
| Database schema and functions | SQL migrations for Supabase | `supabase/` |
| Container | Docker Compose (backend only) | `docker-compose.yml` |

---

## 3. The shared database rule

The Supabase database is **shared with other projects**, so this service is
careful not to touch anything it doesn't own:

- It **only reads** `public.leads`, `lead_profiles`, `company_profiles`,
  `prospects` and `email_nurture_state`.
- It **only writes** to its own `cold_email` schema.
- There are **no foreign keys, views or triggers** pointing into `public`, so other
  projects can delete leads or change columns without breaking anything here.
- The service has **no database password**. It calls SQL functions
  (`cold_email.*`) through the Supabase REST API with the secret key. The tables
  themselves are never exposed.

### Tables in `cold_email`

| Table | Holds |
|---|---|
| `enrollments` | One row per lead in outreach: status, persona, overrides, last error |
| `emails` | Every drafted email: subject, body, when it's due, and whether it was sent |
| `inboxes` | The sending mailboxes, their daily cap, and the name of their password variable |
| `inbox_events` | Replies, bounces, opt-outs and auto-replies found by the poller |
| `suppression_list` | Addresses and domains that must never be emailed |
| `settings` | Every setting you can change at runtime (hours, caps, kill switch…) |

---

## 4. A lead's journey

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
| `replied` | Replied | A human replied, so the rest is cancelled and a person takes over |
| `bounced` | Bounced | Hard bounce, so the address is suppressed |
| `unsubscribed` | Unsubscribed | Opted out (or blocked manually), so the address is suppressed |
| `stopped` | Stopped (lead status) | Another project changed the lead's status (e.g. `DNC`, `Paid`) |
| `manual_review` | Needs review | The AI draft failed checks twice; see `last_error` |
| `failed` | Failed | Something broke (no email, lead deleted, LLM error…) |

Each email has its own status: `pending` → `sending` → `sent`, or `cancelled` / `failed`.

---

## 5. How each step works

### 5.1 Enrollment — who gets in

A lead enters outreach only through `POST /enroll` (the **Enroll** page), or
through **auto-enroll** if you switch it on for chosen `leads.source` values.

A lead is refused, with the reason shown, when it:

| Outcome | Why |
|---|---|
| `not_found` | No lead with that `lead_id` / `id` |
| `no_email` | The lead has no email address |
| `not_contactable (<status>)` | `leads.status` isn't in `contactable_lead_statuses` |
| `in_email_nurture` | The lead is already in another email journey (`email_nurture_state`) |
| `suppressed` | The address or domain is on the suppression list |
| `already_enrolled` | The address is already in outreach (one sequence per address) |

When enrolling you can override **job title**, **company** and **timezone** if
the shared data is missing or wrong.

### 5.2 Lead details — where the data comes from

Nothing is copied. Details are read fresh each time:

| Field | Taken from (first match wins) |
|---|---|
| Email, name, status | `leads` |
| Company | enrollment override → `lead_profiles` → `company_profiles` → `prospects` |
| Job title | enrollment override → `prospects.title` |
| Timezone | override → `leads.timezone` (unless it's just the default `America/New_York`) → state/province → country → UTC |

### 5.3 Persona routing — which angle

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

### 5.4 Drafting — the AI pipeline (LangGraph)

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
- LLM provider: `LLM_PROVIDER` (Gemini or OmniRoute), with an optional
  `LLM_FALLBACK_PROVIDER` if the first is down.

### 5.5 Scheduling — local business hours

- Default window: **09:00–17:00, Mon–Fri, in the lead's own timezone**.
- `business_hours_by_region` changes it per country or timezone. It ships with
  **Sun–Thu weeks** for Bangladesh, Saudi Arabia, Israel, Egypt, Qatar, Kuwait,
  Oman, Bahrain, Jordan, Iraq and Algeria.
- Step spacing: `step_gaps_business_days` = `[0, 3, 7, 12]`, which means email 1
  goes on day 0, email 2 three business days later, and so on. The **length of
  this list is the sequence length.**
- An email that comes due outside business hours is **moved to the next
  business morning**, never dropped.

### 5.6 Sending — five safety checks

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
- **`DRY_RUN=true`** runs everything (drafting, scheduling, cap counting,
  database writes) but no mail actually leaves.

### 5.7 Reply detection — the poller

Each inbox is polled (IMAP or Graph). Each new message is matched to a lead,
first by email threading headers and then by sender address, so a reply from a
colleague's address still counts. Then it's classified:

| Type | What happens |
|---|---|
| **Hard bounce** (`5.x.x`) | Address suppressed, sequence cancelled → `bounced` |
| **Soft bounce** (mailbox full, greylisting) | Logged only; the sequence continues |
| **Auto-reply** (out of office) | Logged only; the sequence continues |
| **Unsubscribe** ("remove me", "unsubscribe"…) | Address suppressed, sequence cancelled → `unsubscribed` |
| **Human reply** | Sequence cancelled → `replied`, plus an alert email to `reply_alert_email` |

Mail that doesn't belong to an enrolled lead is ignored and not stored.
The first time an inbox is polled, the mail already in it is skipped.

---

## 6. Login and access

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
- Only `/health` works without signing in.

---

## 7. Configuration

There are two kinds of configuration:

| Kind | Where | Change takes effect |
|---|---|---|
| Wiring and secrets | `.env` | After a restart |
| Business behaviour | `cold_email.settings` table (**Settings** page) | Within 60 seconds, no restart |

### 7.1 `.env` — main variables

| Variable | Purpose |
|---|---|
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Database access (the `sb_secret_…` key) |
| `LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER` | `gemini` / `omniroute` (fallback also `none`) |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Gemini settings |
| `OMNIROUTE_API_KEY`, `OMNIROUTE_BASE_URL`, `OMNIROUTE_MODEL` | OmniRoute gateway |
| `MAIL_SENDER` / `MAIL_READER` | `smtp` or `graph` / `imap` or `graph`, chosen independently |
| `SMTP_HOST/PORT/STARTTLS/SSL` | Default SMTP server |
| `IMAP_HOST/PORT/SSL/FOLDER` | Default IMAP server |
| `GRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET` | Only if you use Microsoft Graph |
| `INBOX_<REF>_SMTP_PASSWORD` | One per inbox (see 7.3); optional `_IMAP_PASSWORD` |
| `UNSUBSCRIBE_MAILTO`, `REPLY_TO` | Optional sending identity overrides |
| `RUN_WORKERS` | `false` = API only, no background jobs |
| `INTAKE/SEND/POLL_INTERVAL_SECONDS` | Worker timing (300 / 180 / 420) |
| `DRY_RUN` | `true` = nothing is actually sent |
| `API_KEY` | Bearer token for scripts |
| `APP_PUBLIC_URL`, `CMS_PUBLIC_URL`, `SSO_CLIENT_SECRET` | **Required** login settings |
| `HOST_PORT` | Backend port on the host (default 8080) |

### 7.2 Runtime settings (`cold_email.settings`)

| Key | Default | Meaning |
|---|---|---|
| `sequence_enabled` | `true` | **Kill switch** for all sending |
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

### 7.3 Inboxes

Each sending mailbox is a row in `cold_email.inboxes`. Its `credential_ref`
(e.g. `INBOX_A`) points to the password variable in `.env`
(`INBOX_A_SMTP_PASSWORD`). **Passwords never go in the database.** An inbox on a
different provider can override host, port and TLS in its `config` JSON.

---

## 8. API reference

All endpoints except `/health` need a session or the `API_KEY` bearer token.

| Method | Path | Does |
|---|---|---|
| GET | `/health` | Database status, providers, whether dry run is on |
| GET | `/stats` | Pipeline counts and inbox usage |
| POST | `/enroll` | Enroll leads `{"leads":[{"lead_id":"L-1042","job_title":"…"}]}` |
| GET | `/enrollments?status=manual_review,failed` | List enrollments with `last_error` |
| POST | `/enrollments/{id}/retry` | Re-draft a `manual_review` / `failed` / `stopped` lead, optionally correcting data |
| POST | `/preview` | Draft a sequence for a made-up lead; nothing is saved or sent |
| GET | `/route?job_title=…` | Which persona a title gets |
| GET | `/upcoming` | Next scheduled emails (UTC) |
| POST | `/send-now` (`?lead_id=`) | Send the next email now, ignoring business hours only |
| GET | `/replies?type=reply,unsubscribe,bounce,auto_reply` or `all` | Inbound events |
| POST | `/replies/test-alert` | Send a sample reply alert |
| GET | `/settings` · PUT `/settings/{key}` | Read / change a runtime setting `{"value": …}` |
| POST | `/suppress?email=…&reason=…` | Block an address and cancel its sequence |
| POST | `/run/intake`, `/run/sender`, `/run/poller` | Run a worker immediately |
| — | `/auth/start`, `/callback`, `/me`, `/activity`, `/logout` | CMS login flow |

---

## 9. The console

| Page | Use it to |
|---|---|
| **Overview** | See the numbers, pipeline, inbox usage vs. cap; run workers; **Send now**. Refreshes every 30 s |
| **Replies** | Read replies, unsubscribes and bounces; set the alert address; send a test alert |
| **Enroll** | Add leads by `lead_id` with optional overrides; see why any were refused |
| **Preview** | Try the AI on a made-up lead; nothing is saved |
| **Settings** | The on/off switch for sending, plus every runtime setting |
| **Tools** | Block an address; check a job title's persona |

Leads that need attention (`manual_review` / `failed`) are listed with the
reason and a **Retry** button.

---

## 10. Code map

```
service/app/
  main.py            HTTP routes
  auth.py            CMS login, sessions, CSRF
  config.py          .env settings
  settings_store.py  runtime settings (60 s cache) + defaults
  repository.py      every database call (Supabase RPC)
  db.py              REST client
  scheduling.py      business-hours maths
  regions.py         country/state → timezone
  graph/             LangGraph: persona.py, draft.py, validate.py, prompts.py, build.py
  llm/               gemini.py, omniroute.py, factory.py (primary + fallback)
  mail/              smtp_sender, graph_sender, imap_reader, graph_reader, parsing (bounce/opt-out)
  workers/           intake.py, sender.py, poller.py, alerts.py, scheduler.py
service/tests/       65 unit tests + integration tests (need throwaway Postgres + PostgREST)
supabase/migrations/ 0001_init → 0005_replies, run in order
supabase/revert.sql  removes the whole cold_email schema
frontend/src/        App.jsx, api.js, components/*
```

---

## 11. Known limitations

- **No signature is added to emails.** The drafting prompt tells the AI that a
  signature and footer "are appended by the sending system", but no code
  appends one. Emails go out with just the AI body and the sender's display name.
- **Warm-up is manual.** Raise each inbox's `daily_cap` yourself over 2–3 weeks.
- **One backend process only.** Login sessions live in memory. (The workers
  themselves are safe to run concurrently.)
- **The console isn't in Docker.** Compose runs only the backend; the console runs
  with `npm run dev` or as a static build behind a web server.
- **Deviation from the original plan:** the sending and polling cron jobs run in
  the Python service, not in Supabase Edge Functions, because Deno has no
  usable SMTP/IMAP client.
