-- OUT-05 Reply triage — meetings booked through the booking link
--
-- Until now only meetings the triage worker booked itself were recorded. A
-- lead who used the booking link instead never showed up on the Meetings
-- page, and their enrollment never became "meeting_booked". The calendar sync
-- worker now reads the calendar's bookings and records them here:
--
--   sync_meeting       record / update one booking from the calendar
--   meetings_overview  the Meetings page (adds name, source, lead status)
--   open_offers        leads who were sent time options and have not picked
--
-- Also fixes:
--   triage_context     offered slots come only from replies actually sent (a
--                      draft the lead never saw cannot be "option 2")
--   record_meeting     no longer clobbered if the sync saw the booking first
--   triage_stats       unconfirmed (pending) bookings count as meetings
--
-- Only touches the cold_email schema. Safe to re-run. Run after 0007.

begin;

alter table cold_email.meetings add column if not exists attendee_name text;
-- reply: booked by the triage worker from a reply; link: the lead used the
-- booking link (found by the calendar sync).
alter table cold_email.meetings add column if not exists source text not null default 'reply';
alter table cold_email.meetings add column if not exists updated_at timestamptz not null default now();

-- ---------------------------------------------------------------------------
-- The slots a lead can be answering: only from replies that went out.
-- ---------------------------------------------------------------------------
create or replace function cold_email.triage_context(p_lead_id uuid)
returns jsonb
language sql stable security definer set search_path = ''
as $$
    select jsonb_build_object(
        'last_sent', (
            select jsonb_build_object('subject', x.subject, 'body', x.body, 'sent_at', x.sent_at)
              from (
                  select m.subject, m.body, m.sent_at from cold_email.emails m
                   where m.lead_id = p_lead_id and m.status = 'sent'
                  union all
                  select d.subject, d.body, d.sent_at from cold_email.reply_drafts d
                   where d.lead_id = p_lead_id and d.status = 'sent'
              ) x
             order by x.sent_at desc nulls last
             limit 1
        ),
        'offered_slots', coalesce((
            select d.offered_slots from cold_email.reply_drafts d
             where d.lead_id = p_lead_id
               and d.kind in ('meeting_offer','slot_unavailable')
               and d.status = 'sent'
               and jsonb_array_length(d.offered_slots) > 0
             order by d.sent_at desc nulls last
             limit 1
        ), '[]'::jsonb),
        'has_meeting', exists (
            select 1 from cold_email.meetings mt
             where mt.lead_id = p_lead_id and mt.status in ('booked','pending')
               and mt.start_at > now()
        )
    );
$$;

-- ---------------------------------------------------------------------------
-- A meeting booked by the triage worker. If the calendar sync already saw
-- the booking, it is claimed for the reply that led to it.
-- ---------------------------------------------------------------------------
create or replace function cold_email.record_meeting(
    p_lead_id        uuid,
    p_event_id       uuid,
    p_provider       text,
    p_external_id    text,
    p_start_at       timestamptz,
    p_end_at         timestamptz,
    p_attendee_email text,
    p_meeting_url    text
)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
    v_id uuid;
begin
    insert into cold_email.meetings as mt
        (lead_id, event_id, provider, external_id, start_at, end_at, attendee_email, meeting_url)
    values (p_lead_id, p_event_id, p_provider, p_external_id, p_start_at, p_end_at,
            p_attendee_email, p_meeting_url)
    on conflict (provider, external_id) do update
       set start_at    = excluded.start_at,
           end_at      = excluded.end_at,
           lead_id     = coalesce(mt.lead_id, excluded.lead_id),
           event_id    = coalesce(mt.event_id, excluded.event_id),
           meeting_url = coalesce(excluded.meeting_url, mt.meeting_url),
           source      = 'reply',
           status      = 'booked',
           updated_at  = now()
    returning id into v_id;
    update cold_email.enrollments set status = 'meeting_booked', snoozed_until = null
     where lead_id = p_lead_id;
    return v_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- One booking as the calendar reports it. p_status: booked | pending |
-- cancelled. p_ours: the booking is for our own event type, so it is kept
-- even when the attendee is not a known lead; other bookings in the same
-- calendar are only kept when the attendee is one of our leads.
--
-- A new booking by a lead stops their sequence and cancels unsent replies,
-- exactly as when triage books it. A cancelled one puts the lead back to
-- "replied" unless they have another meeting coming up.
-- ---------------------------------------------------------------------------
create or replace function cold_email.sync_meeting(
    p_provider       text,
    p_external_id    text,
    p_start_at       timestamptz,
    p_end_at         timestamptz,
    p_status         text,
    p_attendee_email text,
    p_attendee_name  text,
    p_meeting_url    text,
    p_ours           boolean
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_email   text := lower(trim(coalesce(p_attendee_email, '')));
    v_lead    uuid;
    v_old     cold_email.meetings%rowtype;
    v_outcome text;
begin
    if p_status not in ('booked','pending','cancelled') then
        raise exception 'unknown meeting status %', p_status;
    end if;

    if v_email <> '' then
        select e.lead_id into v_lead from cold_email.enrollments e
         where lower(e.email) = v_email limit 1;
        if v_lead is null then
            -- They replied from (or we answered) another address.
            select x.lead_id into v_lead from (
                select ev.lead_id, ev.received_at as at from cold_email.inbox_events ev
                 where lower(ev.from_email) = v_email and ev.lead_id is not null
                union all
                select d.lead_id, d.created_at from cold_email.reply_drafts d
                 where lower(d.to_email) = v_email
            ) x order by x.at desc limit 1;
        end if;
    end if;

    select * into v_old from cold_email.meetings
     where provider = p_provider and external_id = p_external_id;

    if not found then
        if p_status = 'cancelled' or (v_lead is null and not p_ours) then
            return jsonb_build_object('outcome', 'ignored');
        end if;
        insert into cold_email.meetings
            (lead_id, provider, external_id, start_at, end_at, attendee_email, attendee_name,
             meeting_url, status, source)
        values (v_lead, p_provider, p_external_id, p_start_at, p_end_at, nullif(v_email, ''),
                nullif(trim(p_attendee_name), ''), p_meeting_url, p_status, 'link');
        v_outcome := 'new';
    else
        v_lead := coalesce(v_old.lead_id, v_lead);
        if v_old.start_at is not distinct from p_start_at
           and v_old.end_at is not distinct from p_end_at
           and v_old.status = p_status
           and v_old.lead_id is not distinct from v_lead
           and (p_meeting_url is null or v_old.meeting_url is not distinct from p_meeting_url)
           and (nullif(trim(p_attendee_name), '') is null or v_old.attendee_name is not null) then
            return jsonb_build_object('outcome', 'unchanged', 'lead_id', v_lead);
        end if;
        update cold_email.meetings
           set start_at      = p_start_at,
               end_at        = p_end_at,
               status        = p_status,
               lead_id       = v_lead,
               meeting_url   = coalesce(p_meeting_url, meeting_url),
               attendee_name = coalesce(attendee_name, nullif(trim(p_attendee_name), '')),
               updated_at    = now()
         where id = v_old.id;
        v_outcome := case when p_status = 'cancelled' and v_old.status <> 'cancelled'
                          then 'cancelled' else 'updated' end;
    end if;

    if v_lead is not null then
        if p_status <> 'cancelled'
           and exists (select 1 from cold_email.enrollments e
                        where e.lead_id = v_lead and e.status <> 'meeting_booked') then
            perform cold_email.cancel_sequence(v_lead, 'meeting_booked',
                                               'booked a meeting through the booking link');
            update cold_email.reply_drafts
               set status = 'cancelled', last_error = 'meeting booked'
             where lead_id = v_lead and status in ('pending','approved');
        elsif v_outcome = 'cancelled'
              and not exists (select 1 from cold_email.meetings mt
                               where mt.lead_id = v_lead and mt.status in ('booked','pending')
                                 and mt.start_at > now()) then
            update cold_email.enrollments
               set status = 'replied', last_error = 'their meeting was cancelled'
             where lead_id = v_lead and status = 'meeting_booked';
        end if;
    end if;

    return jsonb_build_object('outcome', v_outcome, 'lead_id', v_lead);
end;
$$;

-- ---------------------------------------------------------------------------
-- Meetings page
-- ---------------------------------------------------------------------------
create or replace function cold_email.meetings_overview(p_limit int default 100)
returns table (
    id uuid, lead_id uuid, event_id uuid, provider text, external_id text,
    start_at timestamptz, end_at timestamptz, attendee_email text, attendee_name text,
    meeting_url text, status text, source text, company text, job_title text,
    lead_status text, created_at timestamptz
)
language sql stable security definer set search_path = ''
as $$
    select m.id, m.lead_id, m.event_id, m.provider, m.external_id, m.start_at, m.end_at,
           m.attendee_email, m.attendee_name, m.meeting_url, m.status, m.source,
           e.company, e.job_title, e.status, m.created_at
      from cold_email.meetings m
      left join cold_email.enrollments e on e.lead_id = m.lead_id
     order by (m.status <> 'cancelled' and m.start_at > now()) desc,
              case when m.start_at > now() then m.start_at end asc,
              m.start_at desc
     limit least(greatest(p_limit, 1), 500);
$$;

-- Leads whose latest reply from us offered times, with no meeting since and
-- no answer from them yet.
create or replace function cold_email.open_offers(p_limit int default 50)
returns table (
    lead_id uuid, email text, company text, job_title text, kind text,
    offered_slots jsonb, sent_at timestamptz
)
language sql stable security definer set search_path = ''
as $$
    select d.lead_id, e.email, e.company, e.job_title, d.kind, d.offered_slots, d.sent_at
      from (
        select distinct on (rd.lead_id) rd.*
          from cold_email.reply_drafts rd
         where rd.status = 'sent'
         order by rd.lead_id, rd.sent_at desc nulls last
      ) d
      join cold_email.enrollments e on e.lead_id = d.lead_id
     where d.kind in ('meeting_offer','slot_unavailable')
       and e.status not in ('meeting_booked','unsubscribed','bounced')
       and not exists (select 1 from cold_email.meetings mt
                        where mt.lead_id = d.lead_id and mt.status in ('booked','pending')
                          and mt.start_at > now())
       and not exists (select 1 from cold_email.inbox_events ev
                        where ev.lead_id = d.lead_id and ev.event_type = 'reply'
                          and ev.received_at > d.sent_at)
     order by d.sent_at desc
     limit least(greatest(p_limit, 1), 200);
$$;

-- ---------------------------------------------------------------------------
-- KPI: unconfirmed bookings count as meetings too.
-- ---------------------------------------------------------------------------
create or replace function cold_email.triage_stats()
returns jsonb
language sql stable security definer set search_path = ''
as $$
    select jsonb_build_object(
        'categories', coalesce((
            select jsonb_object_agg(c, n) from (
                select coalesce(human_category, category) as c, count(*) as n
                  from cold_email.inbox_events
                 where coalesce(human_category, category) is not null
                 group by 1) x
        ), '{}'::jsonb),
        'triage', coalesce((
            select jsonb_object_agg(triage_status, n) from (
                select triage_status, count(*) as n from cold_email.inbox_events
                 where triage_status is not null group by 1) x
        ), '{}'::jsonb),
        'drafts', coalesce((
            select jsonb_object_agg(status, n) from (
                select status, count(*) as n from cold_email.reply_drafts group by 1) x
        ), '{}'::jsonb),
        'replied_leads', (
            select count(distinct lead_id) from cold_email.inbox_events
             where event_type = 'reply' and lead_id is not null),
        'meeting_leads', (
            select count(distinct lead_id) from cold_email.meetings
             where status in ('booked','pending') and lead_id is not null),
        'meetings_upcoming', (
            select count(*) from cold_email.meetings
             where status in ('booked','pending') and start_at > now()),
        'snoozed', (select count(*) from cold_email.enrollments where status = 'snoozed')
    );
$$;

-- ---------------------------------------------------------------------------
-- Privileges: same model as 0001 (service_role may call functions; nobody
-- touches tables directly).
-- ---------------------------------------------------------------------------
revoke all on all tables    in schema cold_email from public;
revoke all on all functions in schema cold_email from public;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on all tables in schema cold_email from %I', r);
            execute format('revoke all on all functions in schema cold_email from %I', r);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant usage on schema cold_email to service_role;
        grant execute on all functions in schema cold_email to service_role;
        revoke execute on function cold_email._lead_details(uuid[]) from service_role;
        revoke execute on function cold_email.touch_updated_at() from service_role;
    end if;
end $$;

commit;

notify pgrst, 'reload schema';
