# Outreach Platform — Runbook

How to set up, run, operate and fix each service.
For how the system works inside, see [documentation.md](./documentation.md).

| Part | Covers |
|---|---|
| A | Shared platform: setup, start/stop, login, logs, emergencies, maintenance |
| B | Cold Email Sequence (OUT-01) |
| C | Reply Triage (OUT-05) |
| D | Email Nurture |

Each part starts with a `<!-- PART X: … -->` marker so the file can be split by service.

<!-- ======================================================================= -->
<!-- PART A: SHARED PLATFORM                                                 -->
<!-- ======================================================================= -->

# Part A — Shared platform

## A0. Quick reference

| I want to… | Do this |
|---|---|
| **Stop all cold sending right now** | Cold sequence → **Settings** → sending **off** (see [A6](#a6-emergencies)) |
| **Stop all nurture emails right now** | Email Nurture → **Settings** → **Email Nurture** off |
| **Stop reply drafts auto-sending** | Reply triage → **Settings** → triage off |
| Start the backend (Docker) | `docker compose up -d --build` |
| Stop the backend | `docker compose down` |
| Watch the logs | `docker compose logs -f out01` |
| Start the console | `cd frontend && npm run dev`, then open http://localhost:5173 |
| Check it's healthy | `curl localhost:8080/health` |

### Calling the API from a terminal

Every endpoint except `/health` needs auth. Put `API_KEY=<long random string>`
in `.env`, restart, then:

```bash
export KEY='<your API_KEY>'
api() { curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' "$@"; echo; }

api localhost:8080/stats
```

The examples in every part use this `api` helper.

## A1. First-time setup

### A1.1 What you need

- Access to the shared **Supabase** project (SQL Editor + project settings)
- A **Gemini API key** (https://aistudio.google.com/apikey), or an OmniRoute gateway
- At least one **sending mailbox** with SMTP/IMAP access
  - Gmail: turn on 2-Step Verification, then create an **App Password** and use
    that, not the normal password. IMAP must be enabled in Gmail settings.
- **Docker** (for the backend) and **Node.js 20+** (for the console)
- Access to the **CMS** `.env` (for login)
- For Reply Triage meetings: a **Cal.com** account and API key (optional)

### A1.2 Database (one time)

In Supabase → **SQL Editor**, run these files **in order**. Each one is safe to re-run.

| # | File | Service | Before running |
|---|---|---|---|
| 1 | `supabase/migrations/0001_init.sql` | Cold | — |
| 2 | `supabase/migrations/0002_seed.sql` | Cold | **Change `yourname@gmail.com` to your real sending address** |
| 3 | `supabase/migrations/0003_review.sql` | Cold | — |
| 4 | `supabase/migrations/0004_send_now.sql` | Cold | — |
| 5 | `supabase/migrations/0005_replies.sql` | Cold | — |
| 6 | `supabase/migrations/0006_triage.sql` | Triage | — |
| 7 | `supabase/migrations/0007_conversation.sql` | Triage | — |
| 8 | `supabase/migrations/0008_meeting_sync.sql` | Triage | — |
| 9 | `supabase/migrations/0009_lead_browser.sql` | Cold | — |
| 10 | `supabase/migrations/0010_nurture.sql` | Nurture | — |
| 11 | `supabase/migrations/0011_nurture_and_cold.sql` | Nurture | — (one sequence at a time: the only change to cold functions) |
| 12 | `supabase/migrations/0012_nurture_lead_list.sql` | Nurture | — |

Then: **Project Settings → Data API → Exposed schemas → add `cold_email` → Save.**
(If you skip this, every call fails with a message telling you to do it.)

None of this touches the shared `public` tables.

### A1.3 Configure `.env`

```bash
cp .env.example .env
```

Fill in at least:

| Variable | Value |
|---|---|
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | The `sb_secret_…` key (Project Settings → API Keys) |
| `GEMINI_API_KEY` | Your Gemini key |
| `SMTP_HOST` / `IMAP_HOST` | e.g. `smtp.gmail.com` / `imap.gmail.com` |
| `INBOX_A_SMTP_PASSWORD` | The mailbox (app) password for the inbox row from 0002 |
| `APP_PUBLIC_URL` | Where the console is opened, e.g. `http://localhost:5173` |
| `CMS_PUBLIC_URL` | The CMS address, e.g. `http://localhost:8102` |
| `SSO_CLIENT_SECRET` | Generate one: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `API_KEY` | Optional; any long random string, for terminal/scripts |
| `DRY_RUN` | **`true`** until go-live |

Service-specific variables: Reply Triage → [C1](#c1-setup), Email Nurture → [D1](#d1-setup).

> Not using OmniRoute? Set `LLM_FALLBACK_PROVIDER=none`.
> Running **without Docker**? Change `OMNIROUTE_BASE_URL` from
> `host.docker.internal` to `localhost`.

### A1.4 Connect login to CMS

In the **CMS** `.env`, add and restart CMS:

```dotenv
COLD_EMAIL_URL=<same as this project's APP_PUBLIC_URL>
COLD_EMAIL_SECRET=<same as this project's SSO_CLIENT_SECRET>
```

The console then also shows up in the CMS sidebar. Only CMS users with the
**admin** or **marketing** role can sign in.

### A1.5 Start the backend

**With Docker (recommended):**
```bash
docker compose up -d --build
docker compose logs -f out01      # look for "scheduler started"
```

**Without Docker**, from the project folder (so `.env` is found):
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r service/requirements.txt
python -m uvicorn app.main:app --app-dir service --port 8080
```

### A1.6 Start the console

```bash
cd frontend
npm install        # first time only
npm run dev
```

Open **exactly** the `APP_PUBLIC_URL` (http://localhost:5173) and sign in through CMS.
The top bar should say **Connected**. It also shows a **DRY RUN** badge while
`DRY_RUN=true`, and a dev badge while `APP_ENV=dev`.

### A1.7 Check it works

```bash
curl localhost:8080/health
# "status": "ok", "database": "ok", "dry_run": true
```

## A2. Daily routine (5 minutes, all services)

1. **Cold sequence → Overview**: top bar says *Connected*, sending is on, inbox
   usage is below its caps, and nothing is stuck in **Needs attention** ([B3.4](#b34-fix-a-lead-in-needs-review)).
2. **Reply triage → Replies / Drafts**: check replies flagged for a person and
   approve or fix drafts before they auto-send ([C3](#c3-daily-operations)).
3. **Email Nurture → Dashboard**: no red **Not sending** box; check **Review**
   (if pilot mode is on) and **Hand-offs** ([D3](#d3-daily-operations)).
4. Weekly during warm-up: raise inbox caps a little ([B3.6](#b36-warm-up-an-inbox)).

## A3. Troubleshooting — platform

### Service won't start

| Symptom (in logs) | Cause and fix |
|---|---|
| `APP_PUBLIC_URL must be…` / `…must be HTTPS` | Login URLs must be bare origins (no path). Plain `http://` is only allowed for `localhost` |
| `SSO_CLIENT_SECRET must be…` | Missing or too short; generate one (A1.3) |
| `APP_PUBLIC_URL and CMS_PUBLIC_URL must differ` | They must be different addresses |
| `supabase_url … field required` | `.env` missing or not in the folder you started from |

### Console problems

| Symptom | Cause and fix |
|---|---|
| **Backend offline** | Backend not running, or not on port 8080. Start it, or set `BACKEND_URL=http://host:port npm run dev` |
| **Database unavailable** | See "Database" below |
| "Your CMS account does not have access" | The CMS user needs the **admin** or **marketing** role |
| Login loops or fails after CMS | Open the console at **exactly** `APP_PUBLIC_URL`; CMS's `COLD_EMAIL_URL` / `COLD_EMAIL_SECRET` must match this `.env` |
| Everyone signed out | Normal after a backend restart (sessions are in memory) |

### Database

| Symptom | Cause and fix |
|---|---|
| Error mentions **exposed schemas** | Add `cold_email` in Data API → Exposed schemas (A1.2) |
| `function … does not exist` | A migration wasn't run; run the missing `00XX_*.sql` |
| Everything fails suddenly | Free Supabase projects **pause** after 7 idle days. Restore it in the dashboard |

### AI calls fail (all services)

| Symptom | Cause and fix |
|---|---|
| `llm call failed` | Bad/expired API key, quota, or model name. Check `GEMINI_API_KEY` / `GEMINI_MODEL` |
| Fallback to OmniRoute fails in Docker | `OMNIROUTE_BASE_URL` must use `host.docker.internal`, and OmniRoute must be running on the host |

## A4. Logs

```bash
docker compose logs -f out01
```

| Log line | Means |
|---|---|
| `scheduler started (intake 300s, send 180s, poll 600s, triage 60s, calendar 300s)` | Workers are running |
| `… worker tick failed` | A job crashed this run; the next run still happens. Nurture failures are also emailed |

Service-specific log lines are in [B5](#b5-logs). Set `LOG_LEVEL=DEBUG` in
`.env` (and restart) for more detail.

## A5. Run every worker immediately

Each workspace has buttons for this; from a terminal:

```bash
# Cold sequence
api -X POST localhost:8080/run/intake
api -X POST localhost:8080/run/sender
api -X POST localhost:8080/run/poller
# Reply triage
api -X POST localhost:8080/run/triage
api -X POST localhost:8080/run/calendar-sync
# Email Nurture
api -X POST localhost:8080/nurture/run/score
api -X POST localhost:8080/nurture/run/generate
api -X POST localhost:8080/nurture/run/send
api -X POST localhost:8080/nurture/run/poll
api -X POST localhost:8080/nurture/run/reconcile
```

## A6. Emergencies

| Stop… | How | Details |
|---|---|---|
| Cold sending | Cold sequence → Settings → sending off | [B6](#b6-emergencies) |
| Reply drafts | Reply triage → Settings → triage off | [C6](#c6-emergencies) |
| Nurture emails | Email Nurture → Settings → Email Nurture off | [D6](#d6-emergencies) |
| Everything | `docker compose down` (also stops reply detection) | — |

Block a whole domain across all services (SQL Editor):
```sql
insert into cold_email.suppression_list (domain, reason) values ('example.com', 'manual');
```

## A7. Maintenance

### Update the code

```bash
git pull
docker compose up -d --build
```
If the update adds a new `supabase/migrations/00XX_*.sql`, run it in the SQL Editor
**before** restarting.

### Run the tests

```bash
cd service
pip install -r requirements-dev.txt
python -m pytest tests -q
```
Unit tests need no network or database. Integration tests are skipped unless
you point them at **throwaway** containers (see README). **Never** point them at
the shared Supabase database, because they wipe tables.

### Remove everything

1. Supabase → Data API → **remove `cold_email`** from Exposed schemas.
2. Run `supabase/revert.sql` in the SQL Editor.

This deletes the `cold_email` schema and **all its data** (all three services).
`public` is untouched. If anything outside `cold_email` depends on it, it stops
and deletes nothing.

### Back up the data

All state is in Supabase (`cold_email` schema). Nothing is stored in the container.
Use Supabase's backups, or export the `cold_email` tables before risky changes.

<!-- ======================================================================= -->
<!-- PART B: COLD EMAIL SEQUENCE (OUT-01)                                    -->
<!-- ======================================================================= -->

# Part B — Cold Email Sequence (OUT-01)

## B1. Check it works

1. **Tools** → type "Head of IT" → it should say *IT & infrastructure*.
2. **Preview** → draft for a made-up lead → you should get 4 emails. Nothing is saved.

## B2. Go-live checklist

Do these **in order**, with `DRY_RUN=true`, before real sending:

- [ ] **Three personas:** enroll 3 test leads you control with job-title
      overrides *CISO*, *IT Manager*, *HR Director* → **Overview** → *Run intake* →
      all 3 reach *Scheduled* with different personas.
- [ ] **Review the copy** of each sequence (Preview, or the `cold_email.emails` table).
- [ ] **Reply alert:** **Settings** → set the reply alert address → *Send test alert*.
- [ ] Set `DRY_RUN=false`, restart, and send to **your own** test address.
- [ ] **Stop on reply:** reply to it → after one poll (≤ 10 min, or *Check replies*)
      the lead is *Replied*, remaining emails are *Cancelled*, and an alert arrives.
- [ ] **Stop on unsubscribe:** reply "please remove me" → *Unsubscribed*, and the address is suppressed.
- [ ] **Stop on bounce:** send to a dead address → *Bounced*.
- [ ] **Spam score:** send one email to a https://www.mail-tester.com address; aim for 9+/10.
- [ ] Set each inbox's `daily_cap` low (**5–10**) to start warm-up ([B3.6](#b36-warm-up-an-inbox)).

## B3. Common tasks

### B3.1 Enroll leads

**Cold sequence → Enroll** lists every lead in the shared `leads` table with its
cold-sequence state. Search by name, email, company, job title or lead ID, and
filter with the chips (Ready to enroll, In sequence, Finished, ...).

- **Enroll** starts the sequence (drafted within 5 minutes).
- **Remove** takes a lead out: emails not sent yet and unsent reply drafts are
  cancelled; what was sent stays in the history. The lead shows as *Stopped*
  and has **Enroll again**, which drafts a fresh sequence from the first email.
  While a sequence is being drafted (a few seconds) Remove waits; try again.
- Tick several leads to enroll or remove them together.

A lead that cannot be enrolled shows why instead of a button. To correct a
job title, company or timezone at enrollment (the job title decides the
persona), open **Enroll by ID, with corrections** at the bottom. Reasons:

| Result | Fix |
|---|---|
| Lead not found | Check the `lead_id` (either `leads.lead_id` or `leads.id` works) |
| No email address | Add an email in the shared lead data |
| Not contactable (status) | The lead's status isn't allowed; see `contactable_lead_statuses` |
| In another email journey | It's in `email_nurture_state` or in Email Nurture; intentional, to avoid double emailing |
| On suppression list | It was blocked/unsubscribed/bounced; leave it |
| Already enrolled | Nothing to do |
| Same email already enrolled | Another lead with this address is in the sequence; one sequence per address |

Terminal:
```bash
api -X POST localhost:8080/enroll -d '{"leads":[{"lead_id":"L-1042","job_title":"Head of IT"}]}'
api "localhost:8080/leads?view=available&search=acme"          # browse
api -X POST localhost:8080/enrollments/remove -d '{"lead_ids":["<leads.id uuid>"]}'
```

### B3.2 Turn on auto-enroll

```bash
api -X PUT localhost:8080/settings/auto_enroll \
  -d '{"value":{"enabled":true,"sources":["apollo"],"batch_size":50}}'
```
Every intake run (5 min) then enrolls up to 50 contactable leads whose
`leads.source` is `apollo`. Set `"enabled": false` to stop.

### B3.3 Change business hours

```bash
# Default window for everyone (weekdays: 0=Mon … 6=Sun)
api -X PUT localhost:8080/settings/business_hours \
  -d '{"value":{"start_hour":9,"end_hour":17,"weekdays":[0,1,2,3,4]}}'
```

Per region: `business_hours_by_region` **replaces the whole map**, so first
copy the current value from **Settings**, then add to it:
```bash
api -X PUT localhost:8080/settings/business_hours_by_region \
  -d '{"value":{"BD":{"weekdays":[6,0,1,2,3]},"GB":{"start_hour":10}}}'
```

One lead in the wrong timezone? Enroll or retry it with `"timezone":"Asia/Dhaka"`.
These hours are also used by Reply Triage for meeting slots and auto-send times.

### B3.4 Fix a lead in *Needs review*

The drafted emails failed the quality checks twice. Read the reason on
**Overview → Needs attention**. Common causes:
- **Wrong or missing job title**: enter the correct one, then **Retry**.
- **LLM error / timeout**: just **Retry**. If it keeps happening, check the API key or quota.

Terminal:
```bash
api localhost:8080/enrollments?status=manual_review,failed
api -X POST localhost:8080/enrollments/<lead uuid>/retry -d '{"job_title":"CISO"}'
```

### B3.5 Add another sending inbox

1. In Supabase SQL Editor:
   ```sql
   insert into cold_email.inboxes (email, display_name, provider, credential_ref, daily_cap, config)
   values ('sam@outreach-domain.com', 'Sam Lee', 'smtp', 'INBOX_B', 5, '{}'::jsonb);
   ```
2. Add `INBOX_B_SMTP_PASSWORD=...` to `.env` and restart the backend.
3. Different provider (e.g. Zoho)? Override its server settings:
   ```sql
   update cold_email.inboxes
      set config = '{"smtp_host":"smtp.zoho.com","smtp_port":465,"smtp_ssl":true,
                     "smtp_starttls":false,"imap_host":"imap.zoho.com"}'::jsonb
    where email = 'sam@outreach-domain.com';
   ```

### B3.6 Warm up an inbox

New mailboxes must start slowly or they land in spam.

| Week | `daily_cap` |
|---|---|
| 1 | 5–10 |
| 2 | 10–15 |
| 3+ | 15–20 |

```sql
update cold_email.inboxes set daily_cap = 10 where email = 'sam@outreach-domain.com';
```
Takes effect on the next sender run. No restart needed. If Email Nurture sends
from this inbox (no `NURTURE_FROM_EMAIL`), its emails count against Gmail's
daily limit too.

### B3.7 Block an address

**Tools → Block an address**. This suppresses it and cancels its remaining emails.
```bash
api -X POST 'localhost:8080/suppress?email=someone@example.com&reason=manual'
```

### B3.8 Change sequence length or spacing

`step_gaps_business_days` sets both. `[0,3,7,12]` = 4 emails, `[0,4,10]` = 3 emails.
It applies to sequences drafted **after** the change.

### B3.9 Send without waiting for business hours

**Overview → Send now**: sends each scheduled lead's **next** email now. Every
other safety check still applies, and leads emailed in the last 24 hours are
skipped. Useful for testing, not for daily use.

## B4. Troubleshooting

### Nothing is being sent

Check in this order:

1. **Sending switched off?** Settings → kill switch.
2. **`DRY_RUN=true`?** `/health` shows `"dry_run"`. In dry run, emails are marked sent but nothing leaves.
3. **Not due yet?** **Overview / `GET /upcoming`** shows due times (in UTC). Emails
   only go out in the **lead's** business hours, so a lead abroad may be asleep.
4. **Caps reached?** Run sender: `deferred_cap` > 0 means every inbox is at its daily cap.
5. **No active inbox?** Logs say `no active inboxes configured`.
6. **Leads stuck in *Waiting to draft*?** Run intake and read its result; check the LLM key.

### Sending fails

| Symptom | Cause and fix |
|---|---|
| Authentication failed (SMTP) | Wrong password. Gmail needs an **App Password** |
| Error about a missing `INBOX_X_SMTP_PASSWORD` | The inbox's `credential_ref` has no matching variable in `.env`; add it and restart |
| Emails marked **Failed** | They hit `max_send_attempts`; the error is on the email row. Fix the cause, then retry the lead |
| Emails land in spam | Lower caps, check SPF/DKIM/DMARC for the domain, run a mail-tester check |

### Replies aren't detected

| Symptom | Cause and fix |
|---|---|
| `/run/poller` returns `errors` | IMAP login/host problem; the reason is in the list. Gmail: IMAP must be enabled |
| First poll says `tracking_started` | Normal. Existing mail is skipped the first time; replies are caught from then on |
| A reply was missed | It came from an unknown address **and** without thread headers. Block or stop the lead by hand (B3.7) |
| Out-of-office didn't stop the sequence | By design; auto-replies don't stop it |

### Drafting fails

| Symptom | Cause and fix |
|---|---|
| `llm call failed` | See [A3](#a3-troubleshooting--platform) |
| Many leads in *Needs review* | Read the reasons; usually missing job titles or a model that ignores the length rules |

## B5. Logs

| Log line | Means |
|---|---|
| `lead … routed to it by keyword` | Persona chosen |
| `lead …: 4-step it sequence ready, timezone Asia/Dhaka (from …)` | Drafted and scheduled; shows which timezone was used and why |
| `lead … needs manual review: …` | Draft failed checks twice |
| `sender tick: {'sent': 3, 'deferred_hours': 5, …}` | Result of one send run |
| `step … deferred to …: all inboxes at daily cap` | Caps reached |
| `lead … replied; sequence cancelled` | Reply handled |
| `sending is disabled via … sequence_enabled` | Kill switch is off |

## B6. Emergencies

### Stop all cold sending immediately

Any one of these works. The first is fastest and needs no restart:

1. Cold sequence → **Settings** → sending **off**
2. Terminal:
   ```bash
   api -X PUT localhost:8080/settings/sequence_enabled -d '{"value": false}'
   ```
3. In Supabase SQL Editor:
   ```sql
   update cold_email.settings set value = 'false' where key = 'sequence_enabled';
   ```
4. `docker compose down` (stops everything, including reply detection)

This takes effect within about a minute (settings cache). Turn it back on the same way with `true`.

### Stop one lead or company

- One address: **Tools → Block an address**.
- A whole domain: see [A6](#a6-emergencies).

### Wrong leads were enrolled

Block their addresses (B3.7), or tick them on **Enroll** and **Remove**. Both
cancel every unsent email for them.

<!-- ======================================================================= -->
<!-- PART C: REPLY TRIAGE (OUT-05)                                           -->
<!-- ======================================================================= -->

# Part C — Reply Triage (OUT-05)

## C1. Setup

1. Run migrations `0006_triage.sql`, `0007_conversation.sql` and
   `0008_meeting_sync.sql` (part of [A1.2](#a12-database-one-time)).
2. In `.env`, for meeting booking:
   ```dotenv
   CALENDAR_PROVIDER=calcom            # or fake to try it without Cal.com, none to skip
   CALCOM_API_KEY=...
   CALCOM_EVENT_TYPE_ID=1234567        # or the slug, e.g. 30min
   CALCOM_BOOKING_URL=https://cal.com/you/30min
   ```
   Restart the backend.
3. **Reply triage → Settings → Calendar → Check free times**: you should see the
   next free slots.

Triage itself needs nothing else: it uses the same LLM and inboxes as the cold sequence.

## C2. Go-live checklist

With `DRY_RUN=false`, using a test lead you control that has already received a cold email:

- [ ] Reply "Sounds interesting, can we talk?" → after the next poll, **Replies**
      shows *Interested*, and **Drafts** has a meeting offer with 2 times.
- [ ] Reply to that with one of the offered times → a meeting appears on **Meetings**
      and a confirmation draft is written.
- [ ] Book through the booking link instead → after **Check calendar now** the
      meeting appears and the lead's sequence stops.
- [ ] Reply "not right now, maybe next quarter" → *Not now*; the lead shows on **Follow-ups**.
- [ ] Reply "please remove me" → *Unsubscribe*; no draft, and the lead is `DNC` in `public.leads`.
- [ ] **Accuracy** → run the test set; aim for 95%.
- [ ] Decide `auto_send_delay_minutes` and which draft kinds may auto-send (Settings).

## C3. Daily operations

1. **Replies**: anything flagged for a person (AI less than 70% sure, or
   *other*) needs a decision. Pick the right category and **Re-run**; triage
   then takes that category's action.
2. **Drafts**: approve, edit or reject before the auto-send time. Approved
   drafts send within a minute.
3. **Meetings**: upcoming bookings; leads offered times who have not answered.
4. **Follow-ups**: snoozed "not now" leads and when they restart.

Every reply still also needs a human eye: the alert email (Cold sequence →
Settings → reply alert address) arrives for each one.

## C4. Common tasks

### C4.1 Correct a category

**Replies** → pick the category → **Re-run**. Terminal:
```bash
api localhost:8080/triage/events
api -X POST localhost:8080/triage/events/<event id>/rerun -d '{"category":"not_now"}'
```

### C4.2 Approve, edit or reject a draft

```bash
api localhost:8080/triage/drafts
api -X PUT  localhost:8080/triage/drafts/<id> -d '{"subject":"…","body":"…"}'
api -X POST localhost:8080/triage/drafts/<id>/approve
api -X POST localhost:8080/triage/drafts/<id>/reject
```

### C4.3 Change auto-send behaviour

**Reply triage → Settings**, or:
```bash
# Wait 4 hours, and never auto-send objection replies
api -X PUT localhost:8080/settings/triage -d '{"value":{
  "auto_send_delay_minutes":240,
  "auto_send_kinds":["meeting_offer","booking_confirmation","slot_unavailable",
                     "not_now_ack","referral_ack","referral_ask"]}}'
```
Fields left out keep their defaults. To have a person approve **every** draft,
set `"auto_send_kinds": []`.

### C4.4 Change meeting slots or snooze length

```bash
api -X PUT localhost:8080/settings/triage -d '{"value":{
  "not_now_days":90,
  "meeting":{"slots_to_offer":3,"days_ahead":10,"min_notice_hours":24}}}'
```

### C4.5 Read the calendar now

**Meetings → Check calendar now**, or `api -X POST localhost:8080/run/calendar-sync`.

## C5. Troubleshooting

### Replies aren't classified

| Symptom | Cause and fix |
|---|---|
| Replies show no category | Triage is off (Settings), or `0006_triage.sql` wasn't run. Run `/run/triage` and read the result |
| The reply itself never appears | It was never detected; see Part B, "Replies aren't detected" |
| Category often wrong | Run **Accuracy**; check `context_messages`; correct and **Re-run** |

### Drafts don't send

| Symptom | Cause and fix |
|---|---|
| Draft stays *pending* past its time | Its kind isn't in `auto_send_kinds`, or the AI was unsure (needs a person) |
| Auto-send time is later than expected | It is moved into the lead's business hours |
| Draft *cancelled* | The address was suppressed or the lead marked `DNC` meanwhile, or the lead was removed or booked a meeting |
| Draft *failed* | SMTP error; the reason is on the draft. See Part B, "Sending fails" |

### Meetings page is empty

| Symptom | Cause and fix |
|---|---|
| "No calendar" note | `CALENDAR_PROVIDER` is `none` or Cal.com settings are wrong; the note says which |
| "Calendar check failed" | The Cal.com API key or event type is wrong, or Cal.com is down; the reason is shown |
| A lead booked through the link but it is missing | Press **Check calendar now**. Bookings for other event types only appear when the attendee's address is one of your leads |
| Error mentioning `meetings_overview` | Run `0008_meeting_sync.sql` |
| Interested replies get no times offered | No free slots in `days_ahead` inside the lead's business hours, or no calendar; the draft then asks the lead for times |

## C6. Emergencies

- **Stop drafts sending**: Reply triage → **Settings** → triage **off**.
  Replies still stop sequences and alert you, but are not classified and no
  drafts send. Or:
  ```bash
  api -X PUT localhost:8080/settings/triage -d '{"value":{"enabled":false}}'
  ```
- **One bad draft**: **Drafts** → **Reject**.
- **Wrong booking**: cancel it in Cal.com; the next calendar sync marks it
  cancelled and puts the lead back to "replied".

<!-- ======================================================================= -->
<!-- PART D: EMAIL NURTURE                                                   -->
<!-- ======================================================================= -->

# Part D — Email Nurture

## D1. Setup

1. Run migrations `0010`, `0011` and `0012` (part of [A1.2](#a12-database-one-time)).
2. `.env` (all optional to start):

   | Variable | Value |
   |---|---|
   | `APP_ENV` | `dev` while testing (emails only go with **Send now**), `production` for scheduled sending |
   | `NURTURE_FROM_EMAIL`, `NURTURE_FROM_NAME`, `NURTURE_SMTP_PASSWORD` | A separate sending account. Empty = send from the first active cold inbox |
   | `NURTURE_REPLY_TO` | An inbox Reply Triage reads. Empty = the first active cold inbox |
   | `NURTURE_PUBLIC_URL` | Empty = `APP_PUBLIC_URL/api` (fine for testing). For real leads: an HTTPS subdomain, e.g. `https://go.yourdomain.com/api` |

   Restart the backend.
3. **Email Nurture → Settings → Branding and links**: pricing link, demo link
   (or `CALCOM_BOOKING_URL` is used), company address, sales email.
4. **Dashboard**: the red **Not sending** box should be gone.

## D2. First run (test mode)

1. Settings: turn **Test mode** on (1 minute per day) and add your own
   addresses to the test list.
2. Give a test lead (with one of those addresses) a Warm or Cold score in the
   shared tables. Within 5 minutes it is enrolled; email 1 arrives within a
   minute or two, the rest over about 30 minutes. (With `APP_ENV=dev`, send
   each from **Review → Send now**.)
3. Click the pricing link in one email: the lead appears under **Hand-offs**
   and the sales email gets a summary. Reply "interested" to another: same.
4. Unsubscribe from a third: it is suppressed and marked `DNC`.
5. Turn test mode off, and set `APP_ENV=production`, before real leads are enrolled.

## D3. Daily operations

1. **Dashboard**: no **Not sending** box; fallback rate and AI health look normal.
2. **Review** (when pilot mode is on): approve, edit or reject drafts.
3. **Hand-offs**: confirm sales picked up each one.
4. **Leads** with a review flag: check the persona, then **Reviewed**.

## D4. Common tasks

### D4.1 Enroll leads already Warm or Cold

Only score *changes* enroll automatically. For leads that were already Warm or
Cold when nurture was switched on: **Leads → Enroll eligible leads**, or tick
leads and enroll them.
```bash
api localhost:8080/nurture/eligible
api -X POST localhost:8080/nurture/enroll
```

### D4.2 Turn on pilot mode (review every draft)

Settings → Review → *Pilot mode*. Every AI draft is held until a person
approves it in **Review**. Rejecting a draft has it written again; after two
rejections the pre-approved fallback is used. *Sample* sends a share (20%) there.

### D4.3 Act on one lead

**Leads** → open the lead → Pause / Resume / Remove / Hand off now / change persona.
```bash
api -X POST localhost:8080/nurture/enrollments/<id>/pause
api -X POST localhost:8080/nurture/enrollments/<id>/handoff
```

### D4.4 Edit content

**Content** → briefs (36: persona × track × step), fallback emails (18) and
the resource library. *Auto-tag* suggests tags for a resource. Changes apply to
emails written afterwards.

### D4.5 Run a nurture job now

See [A5](#a5-run-every-worker-immediately) (`/nurture/run/score`, `generate`,
`send`, `poll`, `reconcile`).

### D4.6 Move to a separate sending account

Set `NURTURE_FROM_EMAIL`, `NURTURE_FROM_NAME` and `NURTURE_SMTP_PASSWORD`
(plus host variables if not Gmail), restart. The dashboard warning about sharing
the cold inbox disappears.

## D5. Troubleshooting

### Nurture isn't sending

Email Nurture → **Dashboard** shows a red **Not sending** box listing exactly
what is missing:

| Message | Fix |
|---|---|
| `No account to send from` | Add an active cold inbox, or set `NURTURE_FROM_EMAIL` |
| `NURTURE_SMTP_PASSWORD is not set` | `NURTURE_FROM_EMAIL` is set to a non-cold address; add its app password and restart |
| `NURTURE_PUBLIC_URL (or APP_PUBLIC_URL) is not set` | Set one of them and restart |
| `No pricing link` / `No demo link` | Email Nurture → Settings → Branding and links |
| `No reply-to address` | Set `NURTURE_REPLY_TO` or add an active cold inbox |

Other reasons an email waits: nurture is paused; `APP_ENV=dev` (only **Send
now** sends); test mode is on and the lead is not on the allow-list; it is
outside the sending window in the lead's timezone; the daily limit is reached;
pilot mode is on and the draft is in **Review**; `DRY_RUN=true`.

Warnings (yellow) do not stop sending: sharing a cold inbox, plain-HTTP links,
dry run.

### Leads don't join

| Symptom | Cause and fix |
|---|---|
| A Warm/Cold lead never enrolled | It was already Warm/Cold before nurture started: use **Enroll eligible leads** |
| **Leads** shows a reason instead of Enroll | In the cold sequence, blocked status, suppressed, or left nurture within 90 days; intentional |
| Auto-enroll does nothing | Settings → auto-enroll is off, or `NURTURE_WORKERS=false` |

### Many fallback emails

The dashboard and an alert email warn above 30%. Read the AI log on a message
(**Leads** → timeline) for the failed checks; usually banned phrases, word
limits or the judge score. Check the LLM key too.

## D6. Emergencies

### Stop all nurture emails

Email Nurture → **Settings** → switch **Email Nurture** off. Nothing is written
or sent from the next cycle (within a minute). Replies and unsubscribes are
still handled. `NURTURE_WORKERS=false` in `.env` plus a restart stops its jobs
entirely.

### Wrong leads were enrolled

**Leads** → tick them → **Remove**. Unsent emails are cancelled.
