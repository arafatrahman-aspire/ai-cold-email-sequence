# Database migrations

Run them in the Supabase **SQL Editor**, in number order. Every file can be
run again safely. Everything this project owns lives in the `cold_email`
schema; the shared `public` tables (`existing_schema.sql`) are only read,
never altered. The single write to a shared table anywhere is setting
`public.leads.status = 'DNC'` when someone unsubscribes.

| File | What it does |
|---|---|
| `0001_init.sql` | Cold sequence: tables (enrollments, emails, inboxes, replies, suppression list, settings) and their functions |
| `0002_seed.sql` | Your sending inbox. **Edit the email address first.** |
| `0003_review.sql` | "Needs review" list and retry |
| `0004_send_now.sql` | "Send next email now" |
| `0005_replies.sql` | Replies list and reply alerts |
| `0006_triage.sql` | Reply Triage: categories, AI drafts, meetings, referrals, snooze |
| `0007_conversation.sql` | The last few emails of a thread, for the AI |
| `0008_meeting_sync.sql` | Meetings booked through the Cal.com link |
| `0009_lead_browser.sql` | Enroll page: list every lead, enroll or remove with one click |
| `0010_nurture.sql` | Email Nurture: 3 tables (enrollments, emails, timeline) and their functions. Briefs, fallback emails and resources live in the existing `settings` table |
| `0011_nurture_and_cold.sql` | One sequence at a time: three cold functions get one extra line each, marked `-- NEW` |

After running a file, nothing else is needed: each ends by telling the API to
reload. The first time only: Project Settings → Data API → Exposed schemas →
add `cold_email`.

To remove everything this project created: `../revert.sql` (read its header
first).
