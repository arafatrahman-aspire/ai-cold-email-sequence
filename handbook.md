# Outreach Platform — Handbook

Everything you need to work on, run, or use the Outreach Platform, from top to
bottom. Start here. Two deeper references sit next to it:

- [`documentation.md`](./documentation.md): field-by-field reference (every table, setting and endpoint).
- [`runbook.md`](./runbook.md): step-by-step operations (setup commands, troubleshooting, emergencies).

| You are… | Read first |
|---|---|
| **A marketing user** of the console | §1, §2, §4 (the user guide), §11 (FAQ) |
| **A project manager** | §1, §2, §3, §5 (workflows), §10 (operations), §12 (risks and roadmap) |
| **A new developer** | Everything, especially §6 (architecture), §7 (setup), §8 (configuration), §9 (developer guide) |

---

## Contents

1. [What the platform is, and why it exists](#1-what-the-platform-is-and-why-it-exists)
2. [Glossary](#2-glossary)
3. [Project manager's guide](#3-project-managers-guide)
4. [User guide: the console, workspace by workspace](#4-user-guide-the-console-workspace-by-workspace)
5. [Workflows: how each service works, end to end](#5-workflows-how-each-service-works-end-to-end)
6. [Architecture](#6-architecture)
7. [Setting it up](#7-setting-it-up)
8. [Configuration reference](#8-configuration-reference)
9. [Developer guide](#9-developer-guide)
10. [Operations: daily routine, monitoring, emergencies](#10-operations-daily-routine-monitoring-emergencies)
11. [FAQ for users](#11-faq-for-users)
12. [Known limitations, risks and roadmap](#12-known-limitations-risks-and-roadmap)

---

## 1. What the platform is, and why it exists

### 1.1 The problem

Aspire Tech sells security awareness and phishing-simulation services to
companies. Getting meetings with the right people (security leaders, IT
managers, HR / compliance) means a lot of repetitive email work:

- writing a sensible, personal email sequence for every lead;
- sending it at a decent hour in *their* timezone, without burning the sending
  inbox's reputation;
- noticing replies, bounces and opt-outs immediately and stopping the sequence;
- answering every reply quickly and correctly (offer times, book the meeting,
  handle "not now", "wrong person", objections, unsubscribes);
- keeping in touch with leads who are interested but not ready, and handing them
  to sales the moment they are.

Done by hand this is slow, inconsistent, and easy to get wrong in ways that
hurt (emailing someone who unsubscribed, double-booking, writing at 3 am).

### 1.2 The solution, in one paragraph

The platform does that work automatically, with a person in control. It has
**three services** in **one console**:

| Service | What it does | Console workspace |
|---|---|---|
| **Cold sequence** (OUT-01) | Enrolled leads get a 4-email AI-written sequence, sent in their own business hours, stopped the moment they reply, bounce or opt out | **Cold sequence** |
| **Reply triage** (OUT-05) | Every reply is read by AI, sorted into a category, acted on (book a meeting, snooze, refer, unsubscribe…) and answered with a draft a person can approve | **Reply triage** |
| **Email Nurture** | Interested-but-not-ready leads get six helpful emails over ~30 days, and are handed to sales the moment they show buying intent | **Email Nurture** |

### 1.3 Principles (the "why" behind the design)

These explain most decisions you will see in the code. Keep them.

1. **Safe by default.** Every email passes several checks right before it
   leaves (kill switch, lead status, suppression list, business hours, daily
   cap). `DRY_RUN=true` runs everything without sending. When the AI is unsure,
   nothing irreversible happens; a person decides.
2. **A person stays in control.** The AI drafts; people approve, edit, redraft,
   correct categories and mark things handled. Auto-send exists, but only for
   confident, low-risk answers, after a delay.
3. **Never harm the shared database.** The Supabase project is shared with
   other Aspire projects. This platform only *reads* `public.*` and writes to its
   own `cold_email` schema (one deliberate exception: unsubscribes set
   `public.leads.status = 'DNC'` so nobody else emails them either).
4. **Respect the recipient.** Local business hours, one sequence per person at
   a time, honest content (the AI may not invent facts, meetings or prices), an
   easy way to opt out, and opt-outs honoured everywhere.
5. **Deliverability first.** Cold email looks like a personal email (no banners,
   images or tracking-heavy design) so it lands in the Primary inbox and gets
   replies. Per-inbox daily caps and warm-up protect sender reputation.
6. **Swappable providers.** The AI model, the email sender, the email reader and
   the calendar are each chosen in `.env`, behind a small interface. Switching
   provider is a configuration change, not a rewrite.
7. **Behaviour is data, not code.** Business hours, sequence spacing, caps,
   thresholds, templates and content live in the `cold_email.settings` table and
   are edited in the console; changes apply within a minute, without a deploy.

### 1.4 Who uses it

- **Marketing team**: enrolls leads, runs campaigns, approves replies, books
  meetings, edits nurture content and the email design.
- **Sales staff** (Microsoft Bookings round robin): take the meetings; receive
  hand-off summaries from Email Nurture.
- **Admins / developers**: configure, deploy and monitor.

Sign-in is through **CMS** (Aspire's content management system). Only CMS users
with the `admin` or `marketing` role get in. Admins also see configuration,
test and developer tools.

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Lead** | A person in the shared `public.leads` table (with profile, company, score) |
| **Enrollment** | A lead taken into the cold sequence (`cold_email.enrollments`). One per address |
| **Sequence** | The 4 cold emails written for one lead; emails 2–4 are replies in the same thread |
| **Persona** | Which angle a lead gets: **CISO** (security/risk/compliance), **IT** (phishing simulation, low admin), **HR** (training completion) |
| **Campaign** | A named group of cold leads with its own audience filter and *playbook* (angle, value points, proof, offer, call to action, tone, banned claims) |
| **Inbox** | A sending mailbox (`cold_email.inboxes`) with a daily cap; used round-robin |
| **Suppression list** | Addresses / domains that must never be emailed (all services check it) |
| **Kill switch** | Setting `sequence_enabled`; off = no cold email is sent |
| **Dry run** | `DRY_RUN=true`: the whole pipeline runs but no email leaves |
| **Inbox event** | Something that arrived: reply, bounce, unsubscribe, auto-reply |
| **Triage** | Reading a reply, putting it in a category, acting, drafting an answer |
| **Category** | Interested, Not now, Wrong person, Objection, Out of office, Unsubscribe, Unclear (other) |
| **Draft / answer** | An AI-written reply to a lead's reply; pending → approved → sent (or rejected / cancelled / failed) |
| **Needs you** | A reply the AI was unsure about or that failed, or an answer waiting for approval |
| **Auto-send** | A confident draft of an allowed kind sends itself after a delay (default 2 h) inside the lead's hours |
| **Redraft** | Ask the AI for a new answer, optionally with instructions ("shorter", "offer Thursday") |
| **Round robin** | Microsoft Bookings meetings are spread over the staff in turn; a time is offered while anyone is free |
| **Reschedule** | A booked lead asks for another time; the meeting is moved automatically if free |
| **Nurture track** | Persona × temperature (Warm or Cold): six emails with fixed spacing |
| **Brief** | The instructions for one nurture email (36 = 3 personas × 2 temperatures × 6 steps) |
| **Fallback** | A pre-approved nurture email used when the AI draft fails its checks |
| **Hand-off** | Nurture lead passed to sales (Hot score, pricing/demo click, interested reply, or by hand) |
| **Engagement** | What Brevo saw happen to an email: delivered, opened, clicked, bounced, spam, unsubscribed |
| **Lead Journey** | One page showing a lead's whole story across all three services |

---

## 3. Project manager's guide

### 3.1 What "working well" looks like

| Metric | Where | What to watch |
|---|---|---|
| Sequences drafted vs. **Needs review** | Cold sequence → Overview | Needs review should be rare; a spike means the AI provider or data is broken |
| Emails sent vs. inbox **daily cap** | Overview | Caps used fully every day = time to add or warm up an inbox |
| **Reply rate** and **Reply → meeting rate** | Reply triage → Replies (stat tiles) | The core outcome of the whole platform |
| **Needs you** count | Reply triage → Replies | Should be near zero at the end of each day |
| **Answers waiting** | Replies / Drafts | Approve or redraft daily; auto-send covers confident ones |
| Meetings coming up | Reply triage → Meetings | Includes meetings booked by hand in Bookings |
| Hard bounces / spam complaints | Lead Journey (engagement), Brevo logs | Rising bounces = bad lead data; any spam complaint deserves a look at content and targeting |
| Nurture: fallback rate, hand-offs | Email Nurture → Dashboard | Fallback rate above 30 % raises an alert: AI or briefs need attention |
| Triage **accuracy** | Reply triage → Accuracy | Target ≥ 95 % on the 50-reply test set |

### 3.2 Governance and safety rules (non-negotiable)

- **Never turn off the suppression check, the business-hours rule or the daily
  caps** to "go faster". They protect the company domain.
- **Keep `DRY_RUN=true`** in any new environment until one full end-to-end test
  (enroll → send → reply → triage → answer) has passed.
- **`APP_ENV=dev`** means nurture emails are written but only sent with
  **Send now**. Switch to `production` only when content and settings are approved.
- **Unsubscribes are permanent.** Removing someone from the suppression list is
  a deliberate, rare, documented act.
- **Cold email stays plain.** Designed, image-heavy cold email lands in
  Promotions. Use the *Card* layout or the *Unsubscribe header* only knowingly.
- **One sequence at a time per person.** Cold and nurture never run together
  for the same lead.

### 3.3 How work is organised

- Code lives in this repository; `main` is deployed to the VPS by `git pull` +
  `docker compose up -d --build` (see §7.6).
- **Database changes are numbered SQL migrations** in `supabase/migrations/`,
  run by hand in the Supabase SQL Editor, in order, each safe to re-run. A
  release that needs a migration must say so; run the migration *before*
  deploying code that uses it (the code tolerates a missing migration where
  possible, but features stay off).
- Configuration that is secret or environment-specific is in `.env` (never
  committed). Business behaviour is in the console's Settings pages.
- When adding a `.env` key, add it to `.env.example` **and** to the local `.env`
  (empty, with a comment) so whoever deploys knows it exists.

### 3.4 Decision log (why things are the way they are)

| Decision | Why |
|---|---|
| Workers run in the Python service (APScheduler), not Supabase Edge Functions | Edge Functions run Deno, which has no usable SMTP/IMAP client |
| The service talks to the database only through SQL functions over the Supabase REST API | No database password on the server; tables are never exposed |
| One LLM call writes all 4 cold emails | Cheaper and more coherent than 4 calls; validated as a whole, one retry |
| Brevo sends; Microsoft Graph reads the Aspire mailbox | Brevo keeps threading and unsubscribe headers and deliverability tooling; Graph reads Microsoft 365 without passwords (basic-auth IMAP is disabled at Microsoft) |
| Microsoft Bookings replaces Cal.com | The company already pays for Microsoft 365; round robin across staff; Teams links. Cal.com code remains, switchable in `.env` |
| Cold emails in a plain, personal layout by default | Primary-tab placement and reply rate matter more than looks |
| Nurture auto-enroll **off** by default | During development leads must join only when someone enrolls them |
| Deleting an enrollment cascades to all its data | Manual cleanup across many tables was error-prone (migration 0018) |
| Reply triage answers from the inbox the reply arrived in | Keeps the conversation in one thread with one sender |

---

## 4. User guide: the console, workspace by workspace

### 4.1 Getting around

- **Top bar**: the three workspaces (**Cold sequence**, **Reply triage**,
  **Email Nurture**), status badges (*Connected*, *Dev*, *Dry run*), theme
  (Auto / Light / Dark), your account, **Sign out**.
- **Left sidebar**: the pages of each workspace, in groups you can fold. The
  **Collapse** button at the bottom turns the sidebar into an icon rail. Both
  are remembered in your browser.
- **Toasts** (bottom right) confirm what you did; **dialogs** ask before
  anything important (a red button means it can't be undone easily).
- Click any lead's email address to open its **Lead Journey**: everything that
  ever happened with that lead.
- Sessions end after 30 minutes idle (8 hours at most). Signing out here also
  signs you out of CMS.

### 4.2 Cold sequence

| Page | What you do there |
|---|---|
| **Overview** | Pipeline numbers, inbox usage vs. daily cap, leads that need attention (with **Retry**), upcoming sends, **Send now** (skips only the business-hours wait) |
| **Campaigns** | Create a campaign: pick the audience (titles, industries, countries, sources, score tiers), write the playbook (angle, value points, proof, offer, call to action, tone, banned claims). Start, pause, resume or end it. Leads added to a paused campaign wait |
| **Enroll** | Browse every shared lead with its sequence state; tick leads to **Enroll**, **Remove** or **Enroll again**. Correct a missing job title, company or timezone when enrolling. See and edit a lead's drafted emails before they are sent |
| **Email design** | Signature (name, title, company, website, phone), brand colour, sign-off, opt-out line, layout (**Plain** recommended, or **Card**), whether to send HTML, and whether to add the List-Unsubscribe header. The **preview** shows each email type exactly as it arrives, on desktop or phone |
| **Preview** *(admin)* | Have the AI write a sequence for a made-up lead; nothing is saved or sent |
| **Settings** *(admin)* | The sending on/off switch, business hours (per region too), sequence spacing, caps, reply alert address, every runtime setting |
| **Tools** *(admin)* | Block an address; check which persona a job title gets |

**Typical day**

1. Open **Overview**: anything in *Needs attention*? Fix the data and **Retry**.
2. **Enroll** the next batch (or add leads to a running campaign).
3. Spot-check a few drafted sequences (open a lead → its emails) before they go.

**Lead statuses you will see**: Waiting to draft → Drafting → Scheduled →
Sending → Sequence finished; or Replied, Bounced, Unsubscribed, Stopped,
Needs review (AI draft failed checks twice), Failed.

### 4.3 Reply triage

| Page | What you do there |
|---|---|
| **Replies** | The inbox. Left: searchable list (email, company, words) with filter chips and counts (*Needs you*, *Interested*, …); ↑/↓ or j/k to move. Right: the selected reply with tabs **Reply** (AI summary, what they wrote, what happened, our answer), **Conversation** (the whole thread) and **Book a time** |
| **Drafts** | Every AI answer: approve, edit, reject, **Redraft**. Filter waiting / sent / rejected |
| **Meetings** | Booked meetings (from replies, from the booking link, and booked by hand in Bookings), and leads offered times who haven't answered. **Check calendar now** |
| **Follow-ups** | "Not now" leads and when their fresh sequence starts |
| **Answer library** | Approved wording the AI reuses when a reply asks the same thing (pricing, security, integrations…) |
| **Accuracy** *(admin)* | Run the 50-reply test of the classifier |
| **Settings** *(admin)* | Auto-send delay and kinds, confidence threshold, snooze length, meeting options (times to offer, look-ahead, minimum notice, **reminder email** hours), calendar check |

**What to do with a reply**

| You see | Do this |
|---|---|
| **Answer waiting** | Read it; edit if needed; **Approve & send**. Not right? **Redraft** with instructions, or **Reject** |
| **Needs you**, no answer | The AI wasn't sure. Pick the right category in **Wrong category?** (it re-runs), or **Read again**, or handle it yourself and **Mark as handled** |
| Wrong category | **Wrong category?** → choose → it acts again with the right one |
| They asked for a time | Usually already booked (or offered nearby times). Otherwise **Book a time** tab → click a free slot |
| They want to move the meeting | Moved automatically if the new time is free; otherwise they are offered the nearest free times |
| A long error in "What happened" | Open **details** for the full text; usually a configuration problem for an admin |

**Auto-send**: confident answers of allowed kinds send themselves after the
delay (default 2 hours), inside the lead's business hours, unless you act first.
Unsure ones *always* wait for you.

### 4.4 Email Nurture

| Page | What you do there |
|---|---|
| **Dashboard** | KPIs, performance per email, AI health (fallback rate, cost). A red **Not sending** box lists what's missing |
| **Leads** | Every shared lead with its nurture state; tick to **Enroll** or **Remove**; open a lead to pause, resume, hand off, change persona, see the timeline |
| **Review** | Drafts waiting for approval (pilot mode) and emails due soon; edit, approve, reject; **Send now** in dev |
| **Calendar** | What goes out when |
| **Hand-offs** | Leads passed to sales, with the trigger and the AI summary |
| **Content** | The 36 briefs, 18 fallback emails, and the resource library (guides, case studies) the emails may link to |
| **Settings** *(admin)* | Pause-all, auto-enroll on score change (off by default), test mode, review mode, cadence, sending window and caps, branding and links, sales and alert addresses |

Nurture leads join **only when you enroll them** (unless *Enroll on score change*
is switched on). In `APP_ENV=dev`, emails are only sent with **Send now**.

### 4.5 Lead Journey

One page per lead: cold status and campaign, nurture status, counts of replies
and meetings, **email engagement** (delivered / opened / clicked, bounces, spam;
opens only while tracking is on in Brevo), the hand-off summary, and a full
timeline of everything sent and received.

---

## 5. Workflows: how each service works, end to end

### 5.1 Cold sequence

```
 enroll ─▶ intake (every 30 min) ─▶ persona ─▶ AI draft (4 emails) ─▶ validate ─┬─ ok ─▶ scheduled
                                                         ▲                       │
                                                         └── retry with errors ──┤ (1st failure)
                                                                                 └─▶ Needs review (2nd)
 scheduled ─▶ sender (every 30 min): kill switch → lead still contactable → not suppressed
              → lead's business hours → inbox daily cap → send (thread steps 2–4)
 poller (every 10 min): reply → stop sequence, alert, queue for triage
                        hard bounce / unsubscribe → suppress, stop
```

- **Who can be enrolled**: has an email; `leads.status` is contactable; not
  suppressed; not already enrolled; not in another email journey.
- **Persona**: keyword match on job title (security first), executives → CISO,
  otherwise the AI classifies; no title → CISO.
- **Drafting**: one AI call returns the whole sequence as JSON. Rules: 60–130
  words, one ask per email, no invented facts, no signature (added by the
  template), greeting line in email 1, 2–3 short paragraphs. Validation rejects
  wrong counts, spam words, placeholders, bad lengths, one-block layout; one
  retry with the errors; then *Needs review*.
- **Scheduling**: default Mon–Fri 09:00–17:00 *in the lead's timezone*; Sun–Thu
  weeks for several countries; spacing `[0, 3, 7, 12]` business days (the length
  of the list is the sequence length). Due outside hours → next business morning.
- **Sending**: the template (`mail/templates.py`) wraps the AI text in the chosen
  layout with the signature; HTML + plain text. Steps 2–4 thread under email 1.
  Inboxes rotate; caps are reserved atomically.

### 5.2 Reply triage

```
 reply arrives ─▶ poller stores it, stops the sequence, sends an alert
 triage worker (right after a poll that found mail, else every 30 min):
   1. wake snoozed leads whose "not now" period ended (fresh sequence)
   2. for each new reply: read the thread → classify (category, confidence, extracted data)
      → plan (pure rules) → act → draft an answer
   3. send approved answers and confident ones whose delay has passed
```

| Category | Action | Answer drafted |
|---|---|---|
| Interested | Offer free slots (lead's hours, different days) + booking link; book a picked/proposed time if free; **reschedule** if they already have a meeting | Meeting times / booking confirmation / slot unavailable |
| Not now | Snooze `not_now_days` (60), then a fresh sequence | Short acknowledgement |
| Wrong person | Enroll the named referral (if an email is given) | Thanks / ask who the right person is |
| Objection | — | An answer to the objection (a flat "no" gets a graceful close) |
| Out of office | Move remaining emails past the return date | — |
| Unsubscribe | Suppress, cancel drafts, mark `DNC` in `public.leads` | Never answered |
| Unclear | Held for a person | — |

**Safety**: below `min_confidence` (70 %) nothing irreversible happens (no
booking, suppression, DNC, snooze, reschedule); the draft never auto-sends; the
reply shows *Needs you*. Drafts may not invent meetings or times: a draft that
names an unoffered time or promises an invite with nothing booked is replaced
by a template. A draft is cancelled at send time if the lead has since been
suppressed. Approving an answer (or **Mark as handled**) clears *Needs you*.

**Learning**: when you edit an AI answer before sending, the AI's original is
kept; later answers of the same kind use your edited versions as style
examples. The **Answer library** gives it approved wording.

### 5.3 Meetings (Microsoft Bookings)

- **Free times**: Graph `getStaffAvailability` for the staff assigned to the
  Bookings service, cut into slots of the service's length on its interval,
  respecting the service's minimum lead time (currently 1 day) and hours.
  A time is offered while *any* staff member is free (round robin capacity).
- **Booking**: the next free staff member after the last one assigned gets it
  (rotation survives restarts by reading recent bookings). Bookings sends the
  invite and creates the Teams link.
- **Reschedule**: the same appointment is moved (Teams link unchanged); its staff
  member keeps it if free, otherwise the next free one.
- **Sync** (every hour): reads all appointments, including those booked by hand
  on the Bookings page; a meeting that disappears is treated as cancelled.
- **Reminder**: about 24 h before (setting *Reminder email*), a short email with
  the time in the lead's timezone, the join link, and "reply with a better time"
  (which triggers an automatic reschedule). Meetings booked within that window
  get none (the confirmation is recent).

### 5.4 Email Nurture

```
 enroll (button, or score change if switched on) ─▶ persona × temperature track
 write job (every min): email written ~24 h ahead from its brief + lead data + resources
   → rule checks → AI judge (≥ 0.7) → one retry → else pre-approved fallback
   → (pilot mode: waits in Review)
 send job (every min): lead's sending window, daily cap, re-check the lead, tracked links
 hand-off: Hot score · pricing/demo click (not within 2 min = mail scanners) · interested reply · by hand
   → unsent emails cancelled, sales gets an AI summary
```

- **Tracks**: Cold = days 0, 5, 10, 16, 23, 30 (educational, no pitch before email
  4); Warm = 0, 3, 7, 12, 19, 28 (proof and stronger calls to action). A score
  change between Warm and Cold switches track from the next email.
- **Links**: the AI writes placeholders (`{{CTA_DEMO}}`, `{{CTA_PRICING}}`,
  `{{RESOURCE_LINK}}`) that become tracked links; it never writes URLs.
- **Replies** to nurture emails are classified with triage's classifier but
  nurture's own actions are taken (no reply drafts).
- **Safety**: pause-all, test mode (days become minutes, allow-list only),
  pilot mode (every or a sample of drafts reviewed), alerts for failed jobs and
  high fallback rates.

### 5.5 Engagement (Brevo)

Every 10 minutes the transactional event log is read (the last 2 days) and each
event about one of *our* emails, matched by Message-ID (cold step, nurture email
or reply answer), is stored once:

| Event | Effect |
|---|---|
| Delivered, opened, clicked, deferred, soft bounce | Recorded; shown in the Lead Journey |
| Hard bounce, invalid, blocked | Address suppressed, sequence stopped |
| Spam complaint | Suppressed, sequence stopped, lead marked `DNC` |
| Unsubscribed | Suppressed, sequence stopped |

Suppression also ends Email Nurture at its next send.

### 5.6 Deleting a lead's data

Deleting a row in `cold_email.enrollments` (e.g. in the Supabase Table Editor)
deletes everything of that lead: emails, replies, answers, meetings, nurture
journey, engagement, referred contact. **The suppression list is kept on
purpose**: an opted-out address stays blocked.

---

## 6. Architecture

### 6.1 The big picture

```
 Browser (signed in via CMS)
   │  https://…/            Outreach Console — React 19 + Vite, served by nginx (container "out01-console")
   │  /api/*  ──proxied──▶  FastAPI service — Python 3.12 (container "out01-service", port 8080 on loopback)
   │                          ├─ APScheduler: all background jobs, single-instance each
   │                          ├─ LLM gateway: Gemini (primary) → OmniRoute/OpenRouter (fallback)
   │                          ├─ Mail out:  Brevo API  (or SMTP / Microsoft Graph)
   │                          ├─ Mail in:   Microsoft Graph (or IMAP), per inbox
   │                          └─ Calendar:  Microsoft Bookings via Graph (or Cal.com / fake / none)
   │                          │
   │                          ▼  Supabase REST API (PostgREST), secret key, SQL functions only
   └────────────────────▶  Supabase Postgres
                              public.*      shared leads data — READ ONLY (except DNC on unsubscribe)
                              cold_email.*  owned by this platform
```

### 6.2 Background jobs

| Job | Default interval | Does |
|---|---|---|
| `intake` | 30 min | Claim enrolled leads, draft sequences |
| `sender` | 30 min | Send due cold emails |
| `poller` | 10 min (and at start) | Read inboxes: replies, bounces, opt-outs |
| `triage` | 30 min, and right after a poll that found mail | Wake snoozed, triage replies, send answers |
| `calendar` | 1 h | Read Bookings appointments into Meetings |
| `reminders` | 15 min | Day-before meeting reminders |
| `engagement` | 10 min | Brevo events: delivered/opened/clicked/bounced/spam |
| `nurture_score` / `nurture_reconcile` | 15 min / 1 h | Score checks (enroll only if auto-enroll is on), hand-off on Hot |
| `nurture_write` / `nurture_send` | 15 min / 15 min | Write and send nurture emails |
| `nurture_mailbox` | 15 min | Nurture's own mailbox, if separate |

Intervals are `*_INTERVAL_SECONDS` in `.env`. `RUN_WORKERS=false` runs the API
only; `NURTURE_WORKERS=false` turns off nurture jobs. To keep Supabase request
volume low, settings are cached for 30 minutes (a change saved in the console
applies at once), the inbox list for an hour (restart the backend after
editing `cold_email.inboxes`), and `/health` pings the database at most every
30 minutes.

### 6.3 Swappable providers

| Concern | `.env` | Options | Interface |
|---|---|---|---|
| AI model | `LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER` | `gemini`, `omniroute` (any OpenAI-compatible gateway) | `app/llm/base.py` |
| Sending | `MAIL_SENDER` | `brevo` (in use), `smtp`, `graph` | `app/mail/base.py` `MailSender` |
| Reading | `MAIL_READER` (+ per inbox `config.reader`) | `graph` (in use), `imap` | `MailReader` |
| Calendar | `CALENDAR_PROVIDER` | `microsoft` (in use), `calcom`, `fake`, `none` | `app/calendar/base.py` `CalendarProvider` |

### 6.4 Data model (the `cold_email` schema)

| Table | Holds |
|---|---|
| `settings` | Every runtime setting (key → JSON) |
| `inboxes` | Sending mailboxes: address, display name, daily cap, credential reference, active, poll cursor, `config` (host overrides, `reader`, `read`) |
| `enrollments` | Leads in the cold sequence: status, persona, overrides, campaign |
| `emails` | Every cold email: step, subject, body, due time, status, Message-ID |
| `inbox_events` | Replies / bounces / opt-outs / auto-replies + triage result (category, confidence, summary, extracted data, status) |
| `reply_drafts` | AI answers: kind, text, offered slots, status, auto-send time, Message-ID |
| `draft_edits` | The AI's original when a person edited an answer (style learning) |
| `meetings` | Bookings: provider, external id, time, attendee, link, status, reminder sent |
| `contacts` | Referred people who are not in `public.leads` |
| `suppression_list` | Blocked addresses / domains, with reason |
| `campaigns`, `campaign_leads` | Campaigns and their leads |
| `nurture_enrollments`, `nurture_messages`, `nurture_events` | Nurture journeys, emails, timeline |
| `email_events` | Brevo engagement events |

The service never touches tables directly: every read and write is a
`cold_email.*` SQL function called over PostgREST. Row-level security is on and
nobody but `service_role` may execute the functions.

### 6.5 Email rendering

The AI writes **plain text only** (subject + body). `app/mail/templates.py`
turns it into the email:

- **Plain** (cold emails, answers, reminders; default): reads like a personal
  email — 600 px table column, one font, signature set off by a short brand-colour
  line, no background or text colour (dark mode safe), no images or buttons.
- **Card**: the same text in a white card with the company name above.
- **Branded** (nurture): logo/header, card, one button, footer with address and
  one-click unsubscribe.
- **Internal** (alerts, hand-offs): heading, facts table, quoted reply, button.

Every email also has a plain-text version.

### 6.6 Security

- CMS single sign-on; HttpOnly session cookie (30 min idle, 8 h max); CSRF token
  on every change; sessions in memory (one backend process).
- Secrets only in `.env` (inbox passwords referenced by name from the database).
- Microsoft Graph app restricted by an Exchange **Application Access Policy** to
  the `sales.outreach@aspiretss.com` mailbox; Bookings permissions only for the
  booking business.
- Brevo API key restricted to the VPS IP (Brevo → Security → Authorised IPs).
- Lead data given to the AI is delimited and marked as data, never instructions.

---

## 7. Setting it up

### 7.1 What you need

- Access to the **Supabase** project (SQL Editor, API keys).
- A **Brevo** account (API key, verified sender, authenticated domain).
- **Microsoft 365**: an Entra app registration (tenant ID, client ID, secret)
  with application permissions `Mail.Read`, `Bookings.Read.All`,
  `BookingsAppointment.ReadWrite.All` (admin-consented); a Bookings page with a
  service and staff.
- An **AI key**: Gemini (Google AI Studio) and/or an OmniRoute / OpenRouter key.
- **CMS** running, with `COLD_EMAIL_SECRET` shared with this app.
- Docker (server) or Python 3.12 + Node 20 (local development).

### 7.2 Database (one time, then per new migration)

1. Supabase → **Project Settings → Data API → Exposed schemas** → add `cold_email`.
2. SQL Editor: run `supabase/migrations/0001_init.sql` … the highest number,
   **in order**. Each is safe to re-run. Edit `0002_seed.sql` (your inbox) first.
3. Later releases add new numbered files; run each once, before deploying the
   code that needs it. `supabase/migrations/README.md` lists what each does.

### 7.3 Email: Brevo (sending)

1. **Senders**: add and verify `sales.outreach@aspiretss.com`.
2. **Domains**: authenticate `aspiretss.com` (DKIM + DMARC DNS records).
   Without it, recipients see `…@…brevosend.com` and mail lands in Promotions/spam.
3. **SMTP & API → API keys**: create a key → `BREVO_API_KEY`.
4. **Security → Authorised IPs**: add the server's IP.
5. Decide on **open/click tracking** (Transactional settings): on = engagement
   data; off = slightly better Primary-tab placement. Bounces and complaints are
   reported either way.

### 7.4 Microsoft 365 (reading replies + Bookings)

1. **Entra admin center → App registrations → New registration** (single tenant).
   Overview → `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`; Certificates & secrets → new
   secret's **Value** → `GRAPH_CLIENT_SECRET` (note its expiry date).
2. **API permissions → Microsoft Graph → Application**: `Mail.Read`,
   `Bookings.Read.All`, `BookingsAppointment.ReadWrite.All` → **Grant admin consent**.
3. Ask the M365 admin to restrict `Mail.Read` with an **Application Access Policy**
   to the sales mailbox (`New-ApplicationAccessPolicy … -AccessRight RestrictAccess`).
4. **Bookings** (outlook.office.com/bookings): booking page, a service (e.g. 30-min
   meeting, online), **assign the staff to the service**, set hours and minimum
   lead time; publish.
5. Find the IDs: after setting the `GRAPH_*` keys and `MSBOOKINGS_BUSINESS_ID`
   (the booking page's mailbox address), open `/api/calendar/microsoft/discover`
   as an admin → copy `MSBOOKINGS_SERVICE_ID` (and staff IDs if you want a fixed
   rotation order). `/api/calendar/microsoft/check` confirms the setup.
6. Remove any Outlook rule forwarding the sales mailbox elsewhere; set
   `MAIL_READER=graph`; make sure the sales inbox row is active and has no
   `"read": false` in its `config`.

### 7.5 `.env`

Copy `.env.example` to `.env` and fill it in. The minimum for the current setup:

```dotenv
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=

LLM_PROVIDER=gemini
GEMINI_API_KEY=
LLM_FALLBACK_PROVIDER=omniroute
OMNIROUTE_API_KEY=
OMNIROUTE_BASE_URL=
OMNIROUTE_MODEL=                  # with a provider prefix, e.g. openrouter/google/...
LLM_MAX_OUTPUT_TOKENS=8192

MAIL_SENDER=brevo
BREVO_API_KEY=
MAIL_READER=graph
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CLIENT_SECRET=
REPLY_TO=                         # empty: replies go to the sending address

NURTURE_FROM_EMAIL=sales.outreach@aspiretss.com
NURTURE_FROM_NAME=AspireTSS
NURTURE_REPLY_TO=sales.outreach@aspiretss.com

CALENDAR_PROVIDER=microsoft
MSBOOKINGS_BUSINESS_ID=           # the booking page's mailbox address
MSBOOKINGS_SERVICE_ID=
MSBOOKINGS_BOOKING_URL=
MSBOOKINGS_TIMEZONE=Asia/Dhaka

APP_PUBLIC_URL=
CMS_PUBLIC_URL=
SSO_CLIENT_SECRET=

DRY_RUN=true                      # until the end-to-end test passes
APP_ENV=dev                       # production = nurture sends on schedule
```

Keep **one line per key** (a later duplicate silently wins). See §8.1 for all keys.

### 7.6 Run

**Server (Docker, as on the VPS)**

```bash
cd ~/ai-cold-email-sequence
git pull
docker compose up -d --build        # backend + console
docker compose logs -f backend      # watch it start
```

The console is on `FRONTEND_PORT` (must match `APP_PUBLIC_URL`); the API is on
`127.0.0.1:HOST_PORT` for scripts. `.env` changes need
`docker compose up -d backend` (recreates the container).

**Local development**

```bash
# backend
cd service && python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8080      # reads ../.env
# console
cd frontend && npm install && npm run dev       # proxies /api to :8080
```

### 7.7 First end-to-end test

1. `DRY_RUN=true`: enroll yourself (a personal address) → sequence drafted →
   check the emails in Enroll / the preview.
2. `DRY_RUN=false`: **Send now** → email arrives (check Primary vs. Promotions,
   sender, signature).
3. Reply "Tuesday at 3 pm works" → within ~10 min it appears in **Replies**, the
   meeting is booked in Bookings, a confirmation is drafted → approve → arrives
   from the sales address in the same thread.
4. Reply "can we move it to Wednesday 11 am?" → meeting moved.
5. Check **Meetings**, the **Lead Journey**, and Brevo logs.

---

## 8. Configuration reference

### 8.1 `.env` (wiring and secrets; restart to apply)

| Group | Keys |
|---|---|
| Database | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` |
| AI | `LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `OMNIROUTE_API_KEY`, `OMNIROUTE_BASE_URL`, `OMNIROUTE_MODEL` (with provider prefix, e.g. `openrouter/google/…`), `OMNIROUTE_FALLBACK_MODEL`, `LLM_TEMPERATURE`, `LLM_MAX_OUTPUT_TOKENS` |
| Mail | `MAIL_SENDER`, `MAIL_READER`, `BREVO_API_KEY`, `GRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET`, `SMTP_*`, `IMAP_*`, `INBOX_<REF>_SMTP_PASSWORD` / `_IMAP_PASSWORD` (SMTP/IMAP only), `REPLY_TO`, `UNSUBSCRIBE_MAILTO` |
| Calendar | `CALENDAR_PROVIDER`, `MSBOOKINGS_BUSINESS_ID`, `MSBOOKINGS_SERVICE_ID`, `MSBOOKINGS_STAFF_IDS`, `MSBOOKINGS_BOOKING_URL`, `MSBOOKINGS_TIMEZONE` (Cal.com: `CALCOM_*`, unused) |
| Nurture | `APP_ENV`, `NURTURE_FROM_EMAIL`, `NURTURE_FROM_NAME`, `NURTURE_REPLY_TO`, `NURTURE_PUBLIC_URL`, `NURTURE_WORKERS`, `NURTURE_*_INTERVAL_SECONDS` |
| Workers | `RUN_WORKERS`, `INTAKE_/SEND_/POLL_/TRIAGE_/CALENDAR_SYNC_/REMINDER_/ENGAGEMENT_INTERVAL_SECONDS` |
| Safety | `DRY_RUN` |
| Login | `APP_PUBLIC_URL`, `CMS_PUBLIC_URL`, `SSO_CLIENT_SECRET`, `API_KEY` (scripts) |
| Ports | `HOST_PORT`, `FRONTEND_PORT` |

### 8.2 Runtime settings (console Settings pages; apply within 60 s)

| Key | Workspace | Highlights |
|---|---|---|
| `sequence_enabled` | Cold → Settings | **Kill switch** for cold sending |
| `business_hours`, `business_hours_by_region` | Cold → Settings | Send window, per country/timezone |
| `step_gaps_business_days` | Cold → Settings | Spacing and length of the sequence |
| `default_daily_cap`, batch sizes, `max_send_attempts` | Cold → Settings | Throughput and retries |
| `contactable_lead_statuses`, `skip_if_in_email_nurture` | Cold → Settings | Who may be emailed |
| `auto_enroll` | Cold → Settings | Auto-enroll by lead source (off) |
| `reply_alert_email` | Cold → Settings | Who is alerted for each reply |
| `email_style` | Cold → Email design | Signature, colour, sign-off, opt-out line, layout, HTML on/off, unsubscribe header |
| `triage` | Reply triage → Settings | Auto-send delay and kinds, `min_confidence`, snooze, OOO default, context size, meeting options incl. `reminder_hours` |
| `triage_snippets` | Reply triage → Answer library | Approved answers |
| `nurture` (+ briefs, fallbacks, resources) | Email Nurture → Settings / Content | Pause-all, auto-enroll on score (off), test/pilot mode, cadence, window, caps, branding, addresses |

---

## 9. Developer guide

### 9.1 Repository map

```
service/                  Python backend (FastAPI)
  app/main.py             HTTP routes (cold + triage + calendar + email design)
  app/auth.py             CMS login, sessions, CSRF
  app/config.py           .env settings (pydantic-settings)
  app/settings_store.py   runtime settings + defaults (60 s cache)
  app/repository.py       every database call (SQL functions over PostgREST)
  app/db.py               REST client
  app/scheduling.py, regions.py   business hours, timezones
  app/llm/                gemini.py, omniroute.py, factory.py (primary + fallback)
  app/mail/               brevo_sender, smtp_sender, graph_sender, imap_reader, graph_reader,
                          parsing (bounces, opt-outs), templates.py (all email HTML), factory.py
  app/graph/              cold drafting (LangGraph): persona, prompts, draft, validate, build
  app/triage/             classify, plan (pure rules), drafting, conversation, evaluate, testset.jsonl
  app/calendar/           base (interface), msbookings, calcom, fake, factory, slots
  app/nurture/            api, enroll, jobs, writer, checks, render, cadence, persona,
                          sending, replies, exits, safety, options, content, alerts, repo
  app/workers/            scheduler, intake, sender, poller, alerts, triage,
                          calendar_sync, reminders, engagement
  tests/                  unit tests; tests/integration/ need Postgres + PostgREST
frontend/                 React 19 + Vite console
  src/App.jsx             shell: top bar, sidebar, routing (#workspace/page)
  src/api.js              every API call
  src/components/ui.jsx   Badge, Card, Field, Spinner, useAction, Toaster/toast, Modal, confirmDialog
  src/components/         cold pages; triage/; nurture/; EmailPreview, LeadJourney
  src/styles.css          tokens (light/dark) + layout + components + theme layer
supabase/migrations/      0001 … NNNN, run in order
supabase/revert.sql       removes the whole cold_email schema (read its header first)
docker-compose.yml        backend + console containers
```

### 9.2 Conventions

- **Database**: add a new numbered migration; never edit an applied one.
  Migrations are idempotent (`create or replace`, `if not exists`), wrapped in
  `begin; … commit;`, revoke from `public`/`anon`/`authenticated`, grant execute
  to `service_role`, end with `notify pgrst, 'reload schema'`. Keep functions
  `security definer set search_path = ''` and fully qualify names. Never write
  to `public.*` except the documented DNC update.
- **Code reads like the surrounding code**: short docstrings that say *why*,
  plain-English user messages, no clever abstractions.
- **Missing migration tolerance**: repository functions that depend on a new
  migration catch "function not found" (`_not_installed`) and degrade gracefully.
- **Pure rules are separate from I/O** (`triage/plan.py`, `calendar/slots.py`,
  `scheduling.py`) so they are easy to test.
- **Providers sit behind interfaces**; adding one = one class + one branch in
  its `factory.py`, then run the contract tests.
- **Settings**: new business knobs go in `settings_store.DEFAULTS` (or nurture
  `options.DEFAULTS`) and the matching Settings page; new `.env` keys go in
  `config.py`, `.env.example` and the local `.env`.
- **UI**: use `ui.jsx` building blocks; `toast.*` for confirmations,
  `confirmDialog()` (await) instead of `window.confirm`, `Modal` for forms;
  colours only through CSS variables so light and dark both work.
- **AI output is untrusted**: validate structure, lengths, placeholders, invented
  facts; have a template or fallback for when it fails.

### 9.3 Tests

```bash
cd service && pip install -r requirements-dev.txt && python -m pytest -q
cd frontend && npx vite build          # the console must build
```

- Calendar contract tests (`tests/test_calendar.py`) run against every provider
  with a mocked HTTP layer that behaves like the real API.
- The triage worker is tested end to end with a scripted LLM and the fake
  calendar (`tests/test_triage_worker.py`).
- Integration tests (`tests/integration/`) need a throwaway Postgres + PostgREST.
- Known: three `test_auth.py::test_unsafe_config_is_rejected` cases fail on
  local `.env` values; unrelated to features.

### 9.4 Common development tasks

| Task | Where |
|---|---|
| Change cold email prompt/rules | `app/graph/prompts.py`, `validate.py` |
| Change how a reply category is handled | `app/triage/plan.py` (rules) + `workers/triage.py` (actions) |
| Change an answer's wording rules | `app/triage/drafting.py` (`INSTRUCTIONS`, `_prompt`) |
| Change email HTML | `app/mail/templates.py` (+ tests in `tests/test_email_design.py`) |
| Add a background job | `app/workers/<job>.py` with `run_once()`, register in `workers/scheduler.py`, add `/run/<job>`, add interval to `config.py` and env files, update `tests/test_scheduler.py` |
| Add a calendar or mail provider | subclass the interface, add to the factory, add to contract tests |
| Add a console page | component in `frontend/src/components/…`, tab in `App.jsx` `WORKSPACES`, icon in `PAGE_ICONS` |

### 9.5 Running a worker by hand

Signed in, or with `Authorization: Bearer $API_KEY` on the backend port:
`POST /run/intake | sender | poller | triage | calendar-sync | reminders | engagement`,
and `POST /nurture/run/{score,reconcile,generate,send,poll}`.

---

## 10. Operations: daily routine, monitoring, emergencies

### 10.1 Daily routine (10 minutes)

1. **Top bar**: *Connected*; no *Backend offline*.
2. **Cold → Overview**: needs attention, inbox caps, upcoming sends.
3. **Reply triage → Replies**: clear *Needs you*; approve or redraft waiting answers.
4. **Meetings**: today's and tomorrow's meetings look right.
5. **Email Nurture → Dashboard**: *Not sending* box empty; fallback rate normal.

### 10.2 Logs

```bash
docker compose logs -f backend                         # everything
docker compose logs --since 1h backend | grep -i -E "error|warn|fail"
docker compose logs backend | grep -E "sent via Brevo|triage tick|calendar sync|reminder|engagement"
```

Useful lines: `sent via Brevo to … from …`, `triaged reply … as …`,
`Microsoft Bookings: … booked with staff …`, `poller tick`, `Brevo engagement: {…}`.

### 10.3 Emergencies

| Situation | Do this |
|---|---|
| Stop all cold email now | Cold → Settings → turn off sending (`sequence_enabled=false`) |
| Stop nurture now | Email Nurture → Settings → **Pause all** |
| Stop answers auto-sending | Reply triage → Settings → empty *auto-send kinds* (or triage off) |
| Stop everything | `DRY_RUN=true` in `.env`, `docker compose up -d backend` |
| Wrong leads enrolled | Enroll → tick → **Remove** (or delete enrollments: cascades) |
| Someone must never be emailed | Tools → block the address (or `POST /suppress`) |

### 10.4 Troubleshooting (most common)

| Symptom | Likely cause → fix |
|---|---|
| Nothing sends | Kill switch off, `DRY_RUN=true`, outside the lead's hours, caps used, Brevo IP not authorised (401 "unrecognised IP") |
| Mail in Promotions / shows `brevosend.com` | Domain not authenticated in Brevo; tracking on; designed layout; unsubscribe header on |
| Drafting fails / "Fallback text" | Gemini quota (429); OmniRoute model name without provider prefix; OpenRouter credits (set `LLM_MAX_OUTPUT_TOKENS`) |
| Replies not appearing | Reader can't read the mailbox (Graph permission/policy), inbox inactive or `"read": false`, forwarding rule elsewhere |
| Booking fails | Staff not assigned to the Bookings service; requested time outside hours or within the minimum lead time; Graph permissions |
| Meetings page empty | Calendar sync error (see logs), wrong `MSBOOKINGS_*`, migration not run |
| "Needs you" won't clear | Approve the answer or **Mark as handled** (needs migration 0020) |
| Answer sent from the wrong address | Answers go from the inbox the reply arrived at; check forwarding and `REPLY_TO` |

The [runbook](./runbook.md) has the full troubleshooting tables.

---

## 11. FAQ for users

**Will it email someone at night?** No. Every email goes in the lead's own
business hours; if due outside them it waits for the next business morning.

**Can the AI book a meeting on its own?** Yes, when it is confident and the time
is free in Bookings. When unsure it offers times and waits for you.

**Can I change what the AI wrote?** Yes: edit before approving, **Redraft** with
instructions, or reject. Your edits teach it the house style.

**A lead says "stop". What happens?** Unsubscribed everywhere, immediately, in
all three services and in the shared leads data. They are never answered.

**Someone replied from a different address.** Matched by email thread, so it
still counts; triage answers in the same thread.

**Why did an answer not send yet?** It waits for the auto-send delay inside the
lead's business hours, or for your approval if the AI was unsure.

**The lead wants another time.** Reply triage moves the meeting automatically if
the time is free; otherwise it offers the nearest free times.

**Do leads get a reminder?** Yes, about a day before, with the join link.

**How do I see everything about one lead?** Click their email anywhere → Lead Journey.

**Why does nurture not send in dev?** `APP_ENV=dev`: use **Send now** in Review.

**I deleted a lead by mistake.** Deleting an enrollment deletes its data and
cannot be undone (export first). Their suppression entry stays.

---

## 12. Known limitations, risks and roadmap

### 12.1 Limitations

- **One backend process** (sessions in memory; a restart signs everyone out).
- **Search on Replies** covers the 200 most recent replies.
- **Graph → IMAP switch back** for an inbox needs its `poll_cursor` reset by hand.
- **Opens/clicks** need Brevo tracking on (a deliverability trade-off).
- **Cal.com** code exists but is not used or maintained for new features.
- **Warm-up is manual**: raise each inbox's `daily_cap` over 2–3 weeks.

### 12.2 Risks to watch

| Risk | Mitigation |
|---|---|
| Domain reputation (spam complaints, bounces) | Caps, plain layout, suppression, engagement sync stops complainers, authenticated domain |
| AI provider outage or quota | Fallback provider, output cap, templates/fallbacks, retries |
| Wrong action on a misread reply | Confidence threshold, nothing irreversible when unsure, human review |
| Secrets expiry (Graph client secret) | Note the expiry; calendar and reading stop when it lapses |
| Shared database changes by other projects | No foreign keys into `public`; status re-checked before each send |

### 12.3 Roadmap ideas (prioritised)

1. **No-show follow-up**: after a missed meeting, draft "sorry we missed you" with new times.
2. **Sales handover brief** before each meeting (who, what they said, talking points).
3. **Lead score + hot-lead alerts** (Teams/email) from engagement and replies.
4. **Funnel dashboard** by campaign and persona; best send times and subjects.
5. **A/B tests** of subjects and openers with automatic winner.
6. **Smarter "not now"** ("talk in Q2" → exact date) and new contacts from OOO replies.
7. **CRM / CMS sync** of meetings and outcomes.
8. **Gamified team leaderboard** (replies handled, meetings booked, response time).
9. **UX**: two-pane layouts for Enroll, Nurture Leads and Nurture Review, as on Replies.
