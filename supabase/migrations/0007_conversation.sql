-- OUT-05 Reply triage — conversation context
--
-- conversation_history: the last few messages exchanged with a lead before a
-- given reply, oldest first, in both directions: our cold emails and replies
-- that were sent, and the lead's earlier replies. The classifier and the
-- reply drafter read it so "yes, Tuesday works" or "as I said..." make sense.
--
-- Only adds a function in the cold_email schema; touches nothing in public.
-- Safe to re-run. Run after 0006_triage.sql.

begin;

create or replace function cold_email.conversation_history(
    p_lead_id       uuid,
    p_before        timestamptz,
    p_exclude_event uuid default null,
    p_limit         int  default 4
)
returns table (direction text, subject text, body text, at timestamptz)
language sql stable security definer set search_path = ''
as $$
    select x.direction, x.subject, x.body, x.at
      from (
        select 'out'::text as direction, m.subject, m.body, m.sent_at as at
          from cold_email.emails m
         where m.lead_id = p_lead_id and m.status = 'sent' and m.sent_at < p_before
        union all
        select 'out', d.subject, d.body, d.sent_at
          from cold_email.reply_drafts d
         where d.lead_id = p_lead_id and d.status = 'sent' and d.sent_at < p_before
        union all
        select 'in', v.subject, v.snippet, v.received_at
          from cold_email.inbox_events v
         where v.lead_id = p_lead_id
           and v.event_type in ('reply','auto_reply','unsubscribe')
           and v.received_at <= p_before
           and v.id is distinct from p_exclude_event
        order by at desc
        limit least(greatest(p_limit, 0), 20)
      ) x
     order by x.at asc;
$$;

revoke all on function cold_email.conversation_history(uuid, timestamptz, uuid, int) from public;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function cold_email.conversation_history(uuid, timestamptz, uuid, int) from %I', r);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant execute on function cold_email.conversation_history(uuid, timestamptz, uuid, int) to service_role;
    end if;
end $$;

insert into cold_email.settings (key, value, description)
select 'triage', '{"context_messages": 4}'::jsonb, 'Reply triage settings.'
 where not exists (select 1 from cold_email.settings where key = 'triage');
update cold_email.settings
   set value = value || '{"context_messages": 4}'::jsonb
 where key = 'triage' and not (value ? 'context_messages');

commit;

notify pgrst, 'reload schema';
