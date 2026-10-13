-- One lead's whole journey on one page
--
--   lead_journey  everything that happened with one lead, in time order:
--                 cold emails, replies (and how triage read them), our
--                 answers, meetings, Email Nurture emails, clicks, hand-off
--
-- Read only. Adds one function; changes no table. Safe to run more than once.
-- Run after 0013.

begin;

create or replace function cold_email.lead_journey(p_lead_id uuid)
returns jsonb
language sql stable security definer set search_path = ''
as $$
    with nurture as (
        select * from cold_email.nurture_enrollments where lead_id = p_lead_id
    ), items as (
        -- Cold sequence: sent emails (any version) and the ones still scheduled.
        select coalesce(m.sent_at, m.due_at) as at,
               jsonb_build_object(
                   'type', case when m.status = 'sent' then 'cold_sent' else 'cold_scheduled' end,
                   'id', m.id, 'step', m.step_number, 'status', m.status,
                   'subject', m.subject, 'body', m.body, 'error', m.last_error) as item
          from cold_email.emails m
          join cold_email.enrollments e on e.lead_id = m.lead_id
         where m.lead_id = p_lead_id
           and (m.status = 'sent' or (m.version = e.sequence_version and m.status in ('pending','sending','failed')))
        union all
        -- What came back: replies, bounces, unsubscribes, auto-replies, from the
        -- cold sequence or from Email Nurture.
        select ev.received_at,
               jsonb_build_object(
                   'type', 'inbound', 'id', ev.id, 'event_type', ev.event_type,
                   'source', case when ev.nurture_enrollment_id is null then 'cold' else 'nurture' end,
                   'from_email', ev.from_email, 'subject', ev.subject, 'snippet', ev.snippet,
                   'category', coalesce(ev.human_category, ev.category),
                   'set_by_person', ev.human_category is not null,
                   'summary', ev.summary, 'triage_status', ev.triage_status,
                   'actions', ev.extracted->'actions')
          from cold_email.inbox_events ev
         where ev.lead_id = p_lead_id
            or ev.nurture_enrollment_id in (select id from nurture)
        union all
        -- Our answers to replies.
        select coalesce(d.sent_at, d.created_at),
               jsonb_build_object(
                   'type', 'answer', 'id', d.id, 'kind', d.kind, 'status', d.status,
                   'subject', d.subject, 'body', d.body, 'auto_send_at', d.auto_send_at,
                   'decided_by', d.decided_by, 'error', d.last_error)
          from cold_email.reply_drafts d
         where d.lead_id = p_lead_id
        union all
        select mt.start_at,
               jsonb_build_object(
                   'type', 'meeting', 'id', mt.id, 'status', mt.status, 'end_at', mt.end_at,
                   'meeting_url', mt.meeting_url, 'booked_at', mt.created_at,
                   'how', case when mt.event_id is null then 'booking link' else 'reply' end)
          from cold_email.meetings mt
         where mt.lead_id = p_lead_id
        union all
        -- Email Nurture: its emails ...
        select coalesce(nm.sent_at, nm.send_at, nm.created_at),
               jsonb_build_object(
                   'type', case when nm.status = 'sent' then 'nurture_sent' else 'nurture_scheduled' end,
                   'id', nm.id, 'step', nm.step, 'status', nm.status, 'subject', nm.subject,
                   'body', nm.body, 'source', nm.source, 'error', nm.error)
          from cold_email.nurture_messages nm
         where nm.enrollment_id in (select id from nurture)
           and nm.status not in ('cancelled','rejected')
        union all
        -- ... and what happened around them (clicks, hand-off, pauses). "sent"
        -- is already shown by the email itself.
        select ne.at,
               jsonb_build_object('type', 'nurture_event', 'kind', ne.kind, 'detail', ne.detail)
          from cold_email.nurture_events ne
         where ne.enrollment_id in (select id from nurture)
           and ne.kind <> 'sent'
    )
    select jsonb_build_object(
        'lead', coalesce(
            cold_email.nurture_lead(p_lead_id),
            (select to_jsonb(d) from cold_email._lead_details(array[p_lead_id]) d limit 1)),
        'cold', (select jsonb_build_object(
                     'status', e.status, 'persona', e.persona, 'enrolled_at', e.enrolled_at,
                     'note', e.last_error, 'snoozed_until', e.snoozed_until,
                     'timezone', e.timezone)
                   from cold_email.enrollments e where e.lead_id = p_lead_id),
        'nurture', (select jsonb_agg(jsonb_build_object(
                         'id', n.id, 'status', n.status, 'persona', n.persona,
                         'temperature', n.temperature, 'step', n.step,
                         'next_send_at', n.next_send_at, 'exit_reason', n.exit_reason,
                         'handoff_summary', n.handoff_summary, 'enrolled_at', n.enrolled_at)
                         order by n.enrolled_at desc)
                      from nurture n),
        'items', coalesce((select jsonb_agg(i.item || jsonb_build_object('at', i.at)
                                            order by i.at desc nulls last)
                             from items i), '[]'::jsonb));
$$;

revoke all on function cold_email.lead_journey(uuid) from public;
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        revoke all on function cold_email.lead_journey(uuid) from anon;
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        revoke all on function cold_email.lead_journey(uuid) from authenticated;
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant execute on function cold_email.lead_journey(uuid) to service_role;
    end if;
end $$;

commit;

notify pgrst, 'reload schema';
