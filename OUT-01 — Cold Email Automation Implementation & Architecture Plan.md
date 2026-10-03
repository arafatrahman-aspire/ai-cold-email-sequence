# OUT-01 — Cold Email Automation: Implementation & Architecture Plan

2026-09-22 · @Someone

## Overview & Scope

**Goal:** When a lead is marked ready for outreach, automatically generate and send a personalized 4-email sequence, then stop on reply, bounce, or unsubscribe.

**In scope:**

- Persona-based routing (CISO: risk & compliance; IT: phishing simulation; HR: training completion)
- 4-email sequence drafting per persona
- Automated sending via Outlook SMTP (OAuth2), within daily caps and business hours
- Reply / bounce / unsubscribe detection and sequence cancellation

**Explicitly out of scope:**

- Lead sourcing and enrichment (handled in a separate project) — this project assumes each lead record already has the data needed to draft a relevant sequence (company, contact name, job title)
- Company/web/news research step — dropped from the original design since enrichment now happens upstream

**Success criteria:**

- Sequences running for all 3 personas
- Stop-on-reply and stop-on-unsubscribe verified
- Spam test passed
- Target: 5%+ reply rate (measured once inbox warm-up is complete)

## Architecture

```
Supabase leads table (status = 'ready_for_outreach')
        ↓ (cron Edge Function, poll every 5 min)
Edge Function marks lead 'processing'
        → calls LangGraph service on VPS
             - persona node (keyword match on job title, LLM fallback)
             - draft node (4-email sequence, using existing lead data — no research step)
             - validation node (checks structure, retries once)
        → writes to Supabase (sequences, sequence_steps)
        → marks lead 'sequence_ready'

Cron Edge Function (every few min) — SENDING
        → selects sequence_steps due now, pending
        → checks daily cap per inbox, lead's business hours
        → sends via Outlook SMTP (OAuth2)
        → logs to sends table, schedules next step

Cron Edge Function (every 5-10 min) — REPLIES / BOUNCES
        → IMAP poll each inbox
        → match to lead, update status
        → on reply / bounce / unsubscribe: cancel remaining sequence_steps
```

**Components:**

- **Supabase** — system of record: leads, sequence state, send log, suppression list
- **Edge Functions (cron)** — the orchestration layer: intake, sending, reply/bounce detection. No n8n.
- **LangGraph service (VPS, FastAPI)** — the reasoning layer: persona routing, drafting, validation
- **OmniRoute** — LLM gateway; routes drafting calls to OpenAI (primary) or Anthropic (fallback)
- **Outlook mailboxes (M365, OAuth2)** — sending and receiving, via SMTP and IMAP

## Data Model (Supabase Schema)

| Table | Purpose |
| --- | --- |
| `leads` | Lead record, including `status` (the trigger field: `ready_for_outreach` → `processing` → `sequence_ready` → `sent`/`replied`/`bounced`/`unsubscribed`) |
| `sequences` | One row per lead's generated sequence (persona assigned, created\_at) |
| `sequence_steps` | The 4 individual emails: subject, body, `due_at`, `status` (pending/sent/cancelled) |
| `sends` | Log of every email actually sent: timestamp, inbox used, sequence\_step reference |
| `inbox_events` | Raw IMAP events: replies, bounces, out-of-office, etc. |
| `inboxes` | Sending mailboxes: address, OAuth2 credentials, daily send count, warm-up stage |
| `suppression_list` | Emails/domains that must never be sent to again (unsubscribed, hard bounced) |

Note: `research_briefs` (from the original design) is dropped — no research step in this version.

## Persona Routing & Drafting Logic

**Routing:** keyword match against the lead's job title.

- CISO / security / risk / compliance → risk & compliance angle
- IT / sysadmin / infrastructure → phishing simulation angle
- HR / people ops / talent → training completion angle
- No match → LLM classifies the title into one of the three personas

**Drafting:** the draft node calls OmniRoute (OpenAI primary, Anthropic fallback) with a per-persona prompt template and the lead's existing data (company, contact name, title). No web/company research is performed — this relies entirely on the data already present on the lead record.

**Validation:** checks that all 4 emails are present, non-empty, and under a reasonable length; on failure, retries the draft node once before flagging the lead for manual review.

## Sending Logic

- **Channel:** Outlook SMTP, OAuth2 (app registered in Entra ID — basic auth is deprecated by Microsoft)
- **Daily cap:** 15-20 sends per inbox per day
- **Scheduling:** each step's `due_at` is computed in the lead's own timezone, restricted to business hours
- **Cron cadence:** the sending Edge Function runs every few minutes, selecting steps that are due and still pending
- **Cap enforcement:** each inbox tracks its own sent-today count; once at cap, remaining sends for that inbox roll to the next business day

## Reply, Bounce & Unsubscribe Handling

- **Detection:** IMAP polling on each sending inbox, every 5-10 minutes
- **Reply:** matched to the lead, lead status updated, remaining `sequence_steps` cancelled
- **Bounce:** hard bounces add the address to `suppression_list` and cancel remaining steps
- **Unsubscribe:** any reply-based opt-out is treated the same as a reply — sequence stops and the address is added to `suppression_list`
- Every send checks `suppression_list` first, regardless of sequence state, as a final safeguard

## Domain & Inbox Setup

- **Domain:** a separate outreach domain (confirmed) — keeps cold-send activity isolated from the main company domain's reputation
- **Inboxes:** 3-5 Microsoft 365 Business Basic mailboxes on that domain, each with SPF, DKIM and DMARC configured
- **OAuth2:** one app registered in Entra ID, granted Mail.Send and IMAP access, used by all inboxes
- **Warm-up:** manual gradual ramp-up (confirmed) — start at a low daily volume per inbox and increase gradually over 2-3 weeks; this runs in the background from day 1 and does not block the rest of the build
- Real reply-rate data against the 5% KPI will only be meaningful once warm-up has progressed

## 2-Day Implementation Timeline

**Day 1**

- Confirm domain, set up SPF/DKIM/DMARC
- Provision M365 inboxes; register Entra ID OAuth2 app
- Start warm-up (runs in background for the rest of the project)
- Create Supabase schema
- Build LangGraph service: persona routing, draft, and validation nodes
- Build intake Edge Function (poll → LangGraph → write sequence\_steps)

**Day 2**

- Build sending Edge Function (daily cap, business-hours scheduling, OAuth2 SMTP)
- Build IMAP polling Edge Function (reply/bounce/unsubscribe detection, cancellation)
- End-to-end test across all 3 personas
- Spam test (Mail-Tester or GlockApps)
- Verify stop-on-reply, stop-on-bounce, stop-on-unsubscribe; confirm done-when criteria; go live at low volume

Note: "go live" on Day 2 means the system is functionally complete and verified — actual send volume stays low and increases only as warm-up progresses.

## Open Decisions & Risks

**Decided:**

- Sending domain: separate outreach domain
- Warm-up: manual gradual ramp-up
- Primary LLM: OpenAI, with Anthropic as fallback via OmniRoute

**Still open (defaults assumed below — flag if these should change):**

- Exact number of inboxes to provision (assumed 3-5)
- Exact business-hours window per timezone (assumed a standard 9am-5pm local)

**Risks:**

- Warm-up timeline (2-3 weeks) is the real bottleneck for the 5% reply-rate KPI, independent of build speed
- New sending domain has zero sender reputation at launch — expect low deliverability until warm-up progresses
- Manual warm-up (vs. a paid tool) means less automated pacing — needs consistent daily attention
