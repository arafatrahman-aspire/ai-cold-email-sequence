-- Email Nurture: a calendar of what goes out, and why a lead was handed off
--
--   nurture_schedule         emails written or sent in a date range, plus the
--                            active enrollments (the console works out the
--                            dates of the emails not written yet)
--   nurture_handoff_signals  for each hand-off: the lead, its real clicks and
--                            its replies, to say in plain words why it is hot
--
-- Read only. Adds two functions; changes no table. Safe to run more than once.
-- Run after 0010.

begin;

create or replace function cold_email.nurture_schedule(p_from timestamptz, p_to timestamptz)
returns jsonb
language sql stable security definer set search_path = ''
as $$
    select jsonb_build_object(
        'messages', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'message_id', m.id, 'enrollment_id', n.id, 'lead_id', n.lead_id,
                       'step', m.step, 'status', m.status, 'at', coalesce(m.sent_at, m.send_at),
                       'subject', m.subject, 'source', m.source, 'resource_id', m.resource_id,
                       'persona', n.persona, 'temperature', n.temperature, 'email', n.email,
                       'first_name', l.first_name, 'last_name', l.last_name)
                   order by coalesce(m.sent_at, m.send_at))
              from cold_email.nurture_messages m
              join cold_email.nurture_enrollments n on n.id = m.enrollment_id
              left join public.leads l on l.id = n.lead_id
             where coalesce(m.sent_at, m.send_at) >= p_from
               and coalesce(m.sent_at, m.send_at) <  p_to
               and m.status not in ('cancelled','rejected')), '[]'::jsonb),
        'live', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'enrollment_id', n.id, 'lead_id', n.lead_id, 'persona', n.persona,
                       'temperature', n.temperature, 'step', n.step, 'started_at', n.started_at,
                       'next_send_at', n.next_send_at, 'test_mode', n.test_mode, 'email', n.email,
                       'timezone', l.timezone, 'first_name', l.first_name, 'last_name', l.last_name,
                       'written', (select coalesce(max(m.step), 0) from cold_email.nurture_messages m
                                    where m.enrollment_id = n.id
                                      and m.status not in ('cancelled','rejected'))))
              from cold_email.nurture_enrollments n
              left join public.leads l on l.id = n.lead_id
             where n.status = 'active'), '[]'::jsonb));
$$;

create or replace function cold_email.nurture_handoff_signals(p_limit int default 100)
returns table (id uuid, lead_id uuid, tier text, clicks jsonb, replies jsonb)
language sql stable security definer set search_path = ''
as $$
    select n.id, n.lead_id,
           (cold_email.nurture_lead(n.lead_id))->>'tier',
           coalesce((select jsonb_agg(jsonb_build_object(
                                'step', ev.detail->>'step', 'link', ev.detail->>'link',
                                'resource_id', ev.detail->>'resource_id', 'at', ev.at) order by ev.at)
                       from cold_email.nurture_events ev
                      where ev.enrollment_id = n.id and ev.kind = 'click'
                        and coalesce((ev.detail->>'bot')::boolean, false) = false), '[]'::jsonb),
           coalesce((select jsonb_agg(jsonb_build_object(
                                'category', coalesce(r.human_category, r.category), 'summary', r.summary,
                                'snippet', left(r.snippet, 300), 'at', r.received_at) order by r.received_at)
                       from cold_email.inbox_events r
                      where r.nurture_enrollment_id = n.id and r.event_type = 'reply'), '[]'::jsonb)
      from cold_email.nurture_enrollments n
     where n.status = 'handed_off'
     order by n.exited_at desc nulls last
     limit least(greatest(p_limit, 1), 500);
$$;

do $$
declare
    f text;
begin
    foreach f in array array[
        'cold_email.nurture_schedule(timestamptz, timestamptz)',
        'cold_email.nurture_handoff_signals(int)'
    ] loop
        execute format('revoke all on function %s from public', f);
        if exists (select 1 from pg_roles where rolname = 'anon') then
            execute format('revoke all on function %s from anon', f);
        end if;
        if exists (select 1 from pg_roles where rolname = 'authenticated') then
            execute format('revoke all on function %s from authenticated', f);
        end if;
        if exists (select 1 from pg_roles where rolname = 'service_role') then
            execute format('grant execute on function %s to service_role', f);
        end if;
    end loop;
end $$;

commit;

notify pgrst, 'reload schema';
