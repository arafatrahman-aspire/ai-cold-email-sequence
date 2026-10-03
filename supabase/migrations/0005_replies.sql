-- OUT-01 Cold Email Automation — replies view & reply alerts
--
-- list_inbox_events: replies (and optionally opt-outs, bounces, auto-replies)
-- with the lead they belong to, for the dashboard's Replies page.
-- reply_alert_email setting: where to send an alert for each new reply.
-- Only adds a function and a setting in the cold_email schema; touches
-- nothing in public. Safe to re-run. Run after 0004_send_now.sql.

begin;

insert into cold_email.settings (key, value, description) values
    ('reply_alert_email',
     '""'::jsonb,
     'Address that gets an email for every new prospect reply, sent from the inbox that received it. Empty = no alerts.')
on conflict (key) do nothing;

create or replace function cold_email.list_inbox_events(
    p_types text[] default array['reply'],
    p_limit int    default 50
)
returns table (
    id          uuid,
    event_type  text,
    lead_id     uuid,
    lead_email  text,
    company     text,
    job_title   text,
    persona     text,
    lead_status text,
    from_email  text,
    subject     text,
    snippet     text,
    message_id  text,
    inbox_email text,
    received_at timestamptz
)
language sql stable security definer set search_path = ''
as $$
    select v.id, v.event_type, v.lead_id, e.email, e.company, e.job_title,
           e.persona, e.status, v.from_email, v.subject, v.snippet,
           v.message_id, i.email, v.received_at
      from cold_email.inbox_events v
      join cold_email.inboxes i on i.id = v.inbox_id
      left join cold_email.enrollments e on e.lead_id = v.lead_id
     where p_types is null or v.event_type = any(p_types)
     order by v.received_at desc
     limit least(greatest(p_limit, 1), 200);
$$;

-- Same privilege model as 0001: service_role only.
revoke all on function cold_email.list_inbox_events(text[], int) from public;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function cold_email.list_inbox_events(text[], int) from %I', r);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant execute on function cold_email.list_inbox_events(text[], int) to service_role;
    end if;
end $$;

commit;

notify pgrst, 'reload schema';
