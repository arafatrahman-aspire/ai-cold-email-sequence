-- Optional seed data. Writes only to the cold_email schema; public.leads is
-- shared with other projects and is never written to.
-- Replace the address below with your real sending mailbox before running.

insert into cold_email.inboxes (email, display_name, provider, credential_ref, daily_cap, config)
values
    ('yourname@gmail.com', 'Your Name', 'smtp', 'INBOX_A', 15, '{}'::jsonb)
on conflict (email) do nothing;

-- To test end to end, enroll an existing lead whose address you control.
-- Use the API (POST /enroll) so the contactable/suppression checks run, or
-- by hand (no checks!):
--
-- insert into cold_email.enrollments (lead_id, email, job_title, source)
-- select l.id, l.email, 'Chief Information Security Officer', 'manual'
--   from public.leads l
--  where l.lead_id = '<your test lead_id>'
-- on conflict do nothing;
