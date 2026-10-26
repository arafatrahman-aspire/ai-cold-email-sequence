-- OUT-05 / Nurture -- 0019: meeting reminders and Brevo engagement
--
-- 1. Meeting reminders: meetings.reminder_sent_at, set when the day-before
--    reminder goes out. Moving a meeting (reschedule, or the lead moving it in
--    Bookings) clears it, so the new time gets its own reminder.
-- 2. Brevo engagement: email_events holds what Brevo reports about our emails
--    (delivered, opened, clicked, bounced, spam, unsubscribed), matched to the
--    lead through the email's Message-ID. Shown in the Lead Journey; hard
--    bounces and spam complaints also stop the lead (done by the service).
--
-- Safe to run: one transaction, nothing existing is changed or deleted, and
-- running it again does nothing new.

begin;

-- ---------------------------------------------------------------------------
-- 1. Meeting reminders
-- ---------------------------------------------------------------------------
alter table cold_email.meetings add column if not exists reminder_sent_at timestamptz;

create or replace function cold_email._meeting_moved()
returns trigger
language plpgsql set search_path = ''
as $$
begin
    if new.start_at is distinct from old.start_at then
        new.reminder_sent_at := null;
    end if;
    return new;
end;
$$;

drop trigger if exists meeting_moved on cold_email.meetings;
create trigger meeting_moved
    before update of start_at on cold_email.meetings
    for each row execute function cold_email._meeting_moved();

-- Booked meetings starting between an hour from now and p_until that have
-- had no reminder yet.
create or replace function cold_email.meetings_due_reminder(p_until timestamptz)
returns table (
    id uuid, lead_id uuid, provider text, external_id text, start_at timestamptz,
    end_at timestamptz, attendee_email text, attendee_name text, meeting_url text,
    created_at timestamptz
)
language sql stable security definer set search_path = ''
as $$
    select m.id, m.lead_id, m.provider, m.external_id, m.start_at, m.end_at,
           m.attendee_email, m.attendee_name, m.meeting_url, m.created_at
      from cold_email.meetings m
     where m.status = 'booked' and m.reminder_sent_at is null
       and m.attendee_email is not null
       and m.start_at > now() + interval '1 hour' and m.start_at <= p_until
     order by m.start_at
     limit 50;
$$;

-- p_sent true: claim the reminder (false if someone else already did).
-- p_sent false: release it again after a failed send.
create or replace function cold_email.mark_meeting_reminded(p_id uuid, p_sent boolean)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
    if p_sent then
        update cold_email.meetings set reminder_sent_at = now()
         where id = p_id and reminder_sent_at is null;
    else
        update cold_email.meetings set reminder_sent_at = null where id = p_id;
    end if;
    return found;
end;
$$;

-- ---------------------------------------------------------------------------
-- 2. Brevo engagement
-- ---------------------------------------------------------------------------
create table if not exists cold_email.email_events (
    id         uuid primary key default gen_random_uuid(),
    message_id text        not null,
    email      text,
    lead_id    uuid,                -- the lead the email went to
    source     text        not null, -- cold | nurture | answer
    event      text        not null, -- delivered | opened | clicked | soft_bounce | hard_bounce | blocked | spam | unsubscribed | deferred
    link       text,                -- clicked: which link
    reason     text,                -- bounces: why
    at         timestamptz not null,
    created_at timestamptz not null default now(),
    constraint email_events_unique unique (message_id, event, at)
);
create index if not exists email_events_lead_idx on cold_email.email_events (lead_id, at desc);
alter table cold_email.email_events enable row level security;

-- Store one event about one of our emails. Returns {outcome: new | seen |
-- unknown, lead_id, source}; "unknown" (not our email) stores nothing.
create or replace function cold_email.record_email_event(
    p_message_id text, p_email text, p_event text, p_link text, p_reason text, p_at timestamptz
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_lead   uuid;
    v_source text;
begin
    select e.lead_id, 'cold' into v_lead, v_source
      from cold_email.emails e where e.message_id = p_message_id limit 1;
    if v_lead is null then
        select n.lead_id, 'nurture' into v_lead, v_source
          from cold_email.nurture_messages m
          join cold_email.nurture_enrollments n on n.id = m.enrollment_id
         where m.message_id = p_message_id limit 1;
    end if;
    if v_lead is null then
        select d.lead_id, 'answer' into v_lead, v_source
          from cold_email.reply_drafts d where d.message_id = p_message_id limit 1;
    end if;
    if v_lead is null then
        return jsonb_build_object('outcome', 'unknown');
    end if;

    insert into cold_email.email_events (message_id, email, lead_id, source, event, link, reason, at)
    values (p_message_id, lower(nullif(trim(p_email), '')), v_lead, v_source, p_event,
            nullif(p_link, ''), left(nullif(p_reason, ''), 300), p_at)
    on conflict (message_id, event, at) do nothing;

    return jsonb_build_object('outcome', case when found then 'new' else 'seen' end,
                              'lead_id', v_lead, 'source', v_source);
end;
$$;

-- One lead's engagement, newest first, with the subject of the email.
create or replace function cold_email.lead_engagement(p_lead_id uuid, p_limit int default 200)
returns table (event text, at timestamptz, link text, reason text, source text, subject text)
language sql stable security definer set search_path = ''
as $$
    select ev.event, ev.at, ev.link, ev.reason, ev.source,
           coalesce(e.subject, m.subject, d.subject)
      from cold_email.email_events ev
      left join cold_email.emails e on e.message_id = ev.message_id
      left join cold_email.nurture_messages m on m.message_id = ev.message_id
      left join cold_email.reply_drafts d on d.message_id = ev.message_id
     where ev.lead_id = p_lead_id
     order by ev.at desc
     limit least(greatest(p_limit, 1), 500);
$$;

-- 0018's cascade also removes the lead's engagement.
create or replace function cold_email.enrollment_deleted()
returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
    delete from cold_email.inbox_events ev
     where ev.nurture_enrollment_id in (
               select n.id from cold_email.nurture_enrollments n where n.lead_id = old.lead_id);
    delete from cold_email.nurture_enrollments where lead_id = old.lead_id;
    delete from cold_email.contacts where id = old.lead_id;
    delete from cold_email.email_events where lead_id = old.lead_id;
    return null;
end;
$$;

-- ---------------------------------------------------------------------------
-- Privileges: service_role may call the functions; nobody touches tables.
-- ---------------------------------------------------------------------------
revoke all on cold_email.email_events from public;
do $$
declare
    f text;
begin
    foreach f in array array[
        'cold_email._meeting_moved()',
        'cold_email.meetings_due_reminder(timestamptz)',
        'cold_email.mark_meeting_reminded(uuid, boolean)',
        'cold_email.record_email_event(text, text, text, text, text, timestamptz)',
        'cold_email.lead_engagement(uuid, int)',
        'cold_email.enrollment_deleted()'
    ] loop
        execute format('revoke all on function %s from public', f);
        if exists (select 1 from pg_roles where rolname = 'anon') then
            execute format('revoke all on function %s from anon', f);
            execute format('revoke all on cold_email.email_events from anon');
        end if;
        if exists (select 1 from pg_roles where rolname = 'authenticated') then
            execute format('revoke all on function %s from authenticated', f);
            execute format('revoke all on cold_email.email_events from authenticated');
        end if;
        if exists (select 1 from pg_roles where rolname = 'service_role') then
            execute format('grant execute on function %s to service_role', f);
        end if;
    end loop;
end $$;

commit;

notify pgrst, 'reload schema';
