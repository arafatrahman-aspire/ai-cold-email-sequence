# Cold Email Automation (OUT-01) — Runbook

How to set up, run, operate and fix the service.
For how the system works inside, see [documentation.md](./documentation.md).

---

## 0. Quick reference

| I want to… | Do this |
|---|---|
| **Stop all sending right now** | Console → **Settings** → turn sending **off** (or see [§7](#7-emergencies)) |
| Start the backend (Docker) | `docker compose up -d --build` |
| Stop the backend | `docker compose down` |
| Watch the logs | `docker compose logs -f out01` |
| Start the console | `cd frontend && npm run dev`, then open http://localhost:5173 |
| Check it's healthy | `curl localhost:8080/health` |
| See what's going out next | Console → **Overview**, or `GET /upcoming` |

### Calling the API from a terminal

Every endpoint except `/health` needs auth. Put `API_KEY=<long random string>`
in `.env`, restart, then:

```bash
export KEY='<your API_KEY>'
api() { curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' "$@"; echo; }

api localhost:8080/stats
```

The examples below use this `api` helper.

---

## 1. First-time setup

### 1.1 What you need

- Access to the shared **Supabase** project (SQL Editor + project settings)
- A **Gemini API key** (https://aistudio.google.com/apikey), or an OmniRoute gateway
- At least one **sending mailbox** with SMTP/IMAP access
  - Gmail: turn on 2-Step Verification, then create an **App Password** and use
    that, not the normal password. IMAP must be enabled in Gmail settings.
- **Docker** (for the backend) and **Node.js 20+** (for the console)
- Access to the **CMS** `.env` (for login)

### 1.2 Database (one time)

In Supabase → **SQL Editor**, run these files **in order**. Each one is safe to re-run.

| # | File | Before running |
|---|---|---|
| 1 | `supabase/migrations/0001_init.sql` | — |
| 2 | `supabase/migrations/0002_seed.sql` | **Change `yourname@gmail.com` to your real sending address** |
| 3 | `supabase/migrations/0003_review.sql` | — |
| 4 | `supabase/migrations/0004_send_now.sql` | — |
| 5 | `supabase/migrations/0005_replies.sql` | — |

Then: **Project Settings → Data API → Exposed schemas → add `cold_email` → Save.**
(If you skip this, every call fails with a message telling you to do it.)

None of this touches the shared `public` tables.

### 1.3 Configure `.env`

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
| `DRY_RUN` | **`true`** until go-live ([§2](#2-go-live-checklist)) |

> Not using OmniRoute? Set `LLM_FALLBACK_PROVIDER=none`.
> Running **without Docker**? Change `OMNIROUTE_BASE_URL` from
> `host.docker.internal` to `localhost`.

### 1.4 Connect login to CMS

In the **CMS** `.env`, add and restart CMS:

```dotenv
COLD_EMAIL_URL=<same as this project's APP_PUBLIC_URL>
COLD_EMAIL_SECRET=<same as this project's SSO_CLIENT_SECRET>
```

The console then also shows up in the CMS sidebar. Only CMS users with the
**admin** or **marketing** role can sign in.

### 1.5 Start the backend

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

### 1.6 Start the console

```bash
cd frontend
npm install        # first time only
npm run dev
```

Open **exactly** the `APP_PUBLIC_URL` (http://localhost:5173) and sign in through CMS.
The top bar should say **Connected**.

### 1.7 Check it works

```bash
curl localhost:8080/health
# "status": "ok", "database": "ok", "dry_run": true
```

Then in the console:
1. **Tools** → type "Head of IT" → it should say *IT & infrastructure*.
2. **Preview** → draft for a made-up lead → you should get 4 emails. Nothing is saved.

---

## 2. Go-live checklist

Do these **in order**, with `DRY_RUN=true`, before real sending:

- [ ] **Three personas:** enroll 3 test leads you control with job-title
      overrides *CISO*, *IT Manager*, *HR Director* → **Overview** → *Run intake* →
      all 3 reach *Scheduled* with different personas.
- [ ] **Review the copy** of each sequence (Preview, or the `cold_email.emails` table).
- [ ] **Reply alert:** **Replies** → set the alert address → *Send test alert*.
- [ ] Set `DRY_RUN=false`, restart, and send to **your own** test address.
- [ ] **Stop on reply:** reply to it → after one poll (≤ 7 min, or *Run poller*)
      the lead is *Replied*, remaining emails are *Cancelled*, and an alert arrives.
- [ ] **Stop on unsubscribe:** reply "please remove me" → *Unsubscribed*, and the address is suppressed.
- [ ] **Stop on bounce:** send to a dead address → *Bounced*.
- [ ] **Spam score:** send one email to a https://www.mail-tester.com address; aim for 9+/10.
- [ ] Set each inbox's `daily_cap` low (**5–10**) to start warm-up ([§4.6](#46-warm-up-an-inbox)).

---

## 3. Daily operations

A 5-minute routine:

1. **Overview**: check the top bar says *Connected*, sending is on, and inbox
   usage is below its caps.
2. **Replies**: every reply means a person must answer it **from the inbox it
   came to**. The sequence has already stopped by itself.
3. **Needs attention** (on Overview): leads in *Needs review* / *Failed*. Read
   the reason, fix the data if needed, and press **Retry** ([§4.4](#44-fix-a-lead-in-needs-review)).
4. Weekly during warm-up: raise inbox caps a little.

---

## 4. Common tasks

### 4.1 Enroll leads

**Console → Enroll**: paste `lead_id`s. Add a job title if the shared data lacks
one, because the job title decides the persona. The result shows why any lead was
refused:

| Result | Fix |
|---|---|
| Lead not found | Check the `lead_id` (either `leads.lead_id` or `leads.id` works) |
| No email address | Add an email in the shared lead data |
| Not contactable (status) | The lead's status isn't allowed; see `contactable_lead_statuses` |
| In another email journey | It's in `email_nurture_state`; this is intentional to avoid double emailing |
| On suppression list | It was blocked/unsubscribed/bounced; leave it |
| Already enrolled | Nothing to do |

Terminal:
```bash
api -X POST localhost:8080/enroll -d '{"leads":[{"lead_id":"L-1042","job_title":"Head of IT"}]}'
```

### 4.2 Turn on auto-enroll

```bash
api -X PUT localhost:8080/settings/auto_enroll \
  -d '{"value":{"enabled":true,"sources":["apollo"],"batch_size":50}}'
```
Every intake run (5 min) then enrolls up to 50 contactable leads whose
`leads.source` is `apollo`. Set `"enabled": false` to stop.

### 4.3 Change business hours

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

### 4.4 Fix a lead in *Needs review*

The drafted emails failed the quality checks twice. Read the reason on
**Overview → Needs attention**. Common causes:
- **Wrong or missing job title**: enter the correct one, then **Retry**.
- **LLM error / timeout**: just **Retry**. If it keeps happening, check the API key or quota.

Terminal:
```bash
api localhost:8080/enrollments?status=manual_review,failed
api -X POST localhost:8080/enrollments/<lead uuid>/retry -d '{"job_title":"CISO"}'
```

### 4.5 Add another sending inbox

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

### 4.6 Warm up an inbox

New mailboxes must start slowly or they land in spam.

| Week | `daily_cap` |
|---|---|
| 1 | 5–10 |
| 2 | 10–15 |
| 3+ | 15–20 |

```sql
update cold_email.inboxes set daily_cap = 10 where email = 'sam@outreach-domain.com';
```
Takes effect on the next sender run. No restart needed.

### 4.7 Block an address

**Tools → Block an address**. This suppresses it and cancels its remaining emails.
```bash
api -X POST 'localhost:8080/suppress?email=someone@example.com&reason=manual'
```

### 4.8 Change sequence length or spacing

`step_gaps_business_days` sets both. `[0,3,7,12]` = 4 emails, `[0,4,10]` = 3 emails.
It applies to sequences drafted **after** the change.

### 4.9 Send without waiting for business hours

**Overview → Send now**: sends each scheduled lead's **next** email now. Every
other safety check still applies, and leads emailed in the last 24 hours are
skipped. Useful for testing, not for daily use.

### 4.10 Run a worker immediately

**Overview** buttons, or:
```bash
api -X POST localhost:8080/run/intake    # draft waiting leads
api -X POST localhost:8080/run/sender    # send what's due
api -X POST localhost:8080/run/poller    # check inboxes for replies
```

---

## 5. Troubleshooting

### Service won't start

| Symptom (in logs) | Cause and fix |
|---|---|
| `APP_PUBLIC_URL must be…` / `…must be HTTPS` | Login URLs must be bare origins (no path). Plain `http://` is only allowed for `localhost` |
| `SSO_CLIENT_SECRET must be…` | Missing or too short; generate one (§1.3) |
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
| Error mentions **exposed schemas** | Add `cold_email` in Data API → Exposed schemas (§1.2) |
| `function … does not exist` | A migration wasn't run; run the missing `000X_*.sql` |
| Everything fails suddenly | Free Supabase projects **pause** after 7 idle days. Restore it in the dashboard |

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
| A reply was missed | It came from an unknown address **and** without thread headers. Block or stop the lead by hand (§4.7) |
| Out-of-office didn't stop the sequence | By design; auto-replies don't stop it |

### Drafting fails

| Symptom | Cause and fix |
|---|---|
| `llm call failed` | Bad/expired API key, quota, or model name. Check `GEMINI_API_KEY` / `GEMINI_MODEL` |
| Fallback to OmniRoute fails in Docker | `OMNIROUTE_BASE_URL` must use `host.docker.internal`, and OmniRoute must be running on the host |
| Many leads in *Needs review* | Read the reasons; usually missing job titles or a model that ignores the length rules |

---

## 6. Logs — what to look for

```bash
docker compose logs -f out01
```

| Log line | Means |
|---|---|
| `scheduler started (intake 300s, send 180s, poll 420s)` | Workers are running |
| `lead … routed to it by keyword` | Persona chosen |
| `lead …: 4-step it sequence ready, timezone Asia/Dhaka (from …)` | Drafted and scheduled; shows which timezone was used and why |
| `lead … needs manual review: …` | Draft failed checks twice |
| `sender tick: {'sent': 3, 'deferred_hours': 5, …}` | Result of one send run |
| `step … deferred to …: all inboxes at daily cap` | Caps reached |
| `lead … replied; sequence cancelled` | Reply handled |
| `sending is disabled via … sequence_enabled` | Kill switch is off |

Set `LOG_LEVEL=DEBUG` in `.env` (and restart) for more detail.

---

## 7. Emergencies

### Stop all sending immediately

Any one of these works. The first is fastest and needs no restart:

1. Console → **Settings** → sending **off**
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
- A whole domain, in the SQL Editor:
  ```sql
  insert into cold_email.suppression_list (domain, reason) values ('example.com', 'manual');
  ```
  Every email to that domain is then skipped at send time.

### Wrong leads were enrolled

Block their addresses (§4.7). This cancels every unsent email for them.

---

## 8. Maintenance

### Update the code

```bash
git pull
docker compose up -d --build
```
If the update adds a new `supabase/migrations/000X_*.sql`, run it in the SQL Editor
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

This deletes the `cold_email` schema and **all its data**. `public` is untouched.
If anything outside `cold_email` depends on it, it stops and deletes nothing.

### Back up the data

All state is in Supabase (`cold_email` schema). Nothing is stored in the container.
Use Supabase's backups, or export the `cold_email` tables before risky changes.
