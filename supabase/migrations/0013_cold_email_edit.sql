-- OUT-01 Cold Email Automation — see and edit a lead's drafted emails
--
--   lead_emails  every email of the lead's current sequence (sent ones too)
--   edit_email   change the subject/body of an email not sent yet
--
-- Adds two functions; changes no table. Safe to run more than once.
-- Run after 0001.

begin;

create or replace function cold_email.lead_emails(p_lead_id uuid)
returns table (id uuid, step_number int, subject text, body text, due_at timestamptz,
               status text, sent_at timestamptz, last_error text)
language sql stable security definer set search_path = ''
as $$
    select m.id, m.step_number, m.subject, m.body, m.due_at, m.status, m.sent_at, m.last_error
      from cold_email.emails m
      join cold_email.enrollments e on e.lead_id = m.lead_id
     where m.lead_id = p_lead_id
       and (m.version = e.sequence_version or m.status = 'sent')
     order by m.step_number, m.version;
$$;

-- Only while it is still waiting: false once the sender has taken it, it was
-- sent, or the sequence was cancelled.
create or replace function cold_email.edit_email(p_id uuid, p_subject text, p_body text)
returns boolean
language sql security definer set search_path = ''
as $$
    with changed as (
        update cold_email.emails
           set subject = p_subject, body = p_body
         where id = p_id and status = 'pending'
        returning 1
    )
    select exists (select 1 from changed);
$$;

revoke all on function cold_email.lead_emails(uuid) from public;
revoke all on function cold_email.edit_email(uuid, text, text) from public;
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        revoke all on function cold_email.lead_emails(uuid) from anon;
        revoke all on function cold_email.edit_email(uuid, text, text) from anon;
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        revoke all on function cold_email.lead_emails(uuid) from authenticated;
        revoke all on function cold_email.edit_email(uuid, text, text) from authenticated;
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant execute on function cold_email.lead_emails(uuid) to service_role;
        grant execute on function cold_email.edit_email(uuid, text, text) to service_role;
    end if;
end $$;

commit;

notify pgrst, 'reload schema';
