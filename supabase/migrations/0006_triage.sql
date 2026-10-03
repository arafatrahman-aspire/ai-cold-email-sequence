-- OUT-05 Cold Reply Triage & Meeting Booking
--
-- Every reply to a cold email is classified (interested, not now, wrong
-- person, objection, out of office, unsubscribe) and acted on: meeting slots
-- are offered and booked, referrals are enrolled, "not now" leads come back
-- after 60 days, out-of-office pauses the sequence, and unsubscribes are
-- suppressed everywhere. Replies to prospects are AI drafts that wait for a
-- human and auto-send after a delay if nobody approves or rejects them.
--
-- Schema changes stay inside cold_email. The one write outside it is
-- mark_dnc(): on an unsubscribe it sets public.leads.status = 'DNC' (an
-- existing column and an allowed value; no column is added or changed).
--
-- Safe to re-run. Run after 0005_replies.sql. When re-running, run every
-- migration file in number order: an older file run on its own puts back
-- that file's older versions of functions this one replaces.

begin;

-- ---------------------------------------------------------------------------
-- Settings
-- ---------------------------------------------------------------------------
insert into cold_email.settings (key, value, description) values
    ('triage',
     '{"enabled": true,
       "auto_send_delay_minutes": 120,
       "auto_send_kinds": ["meeting_offer", "booking_confirmation", "slot_unavailable",
                           "objection_reply", "not_now_ack", "referral_ack", "referral_ask"],
       "min_confidence": 0.7,
       "not_now_days": 60,
       "ooo_default_days": 7,
       "meeting": {"slots_to_offer": 2, "days_ahead": 7, "min_notice_hours": 12}}'::jsonb,
     'Reply triage. Drafts auto-send auto_send_delay_minutes after creation (inside the lead''s business hours) unless approved or rejected first; kinds not listed always wait for a person. Below min_confidence nothing auto-sends and no irreversible action is taken.')
on conflict (key) do nothing;

-- ---------------------------------------------------------------------------
-- inbox_events: the triage result for each reply
-- ---------------------------------------------------------------------------
alter table cold_email.inbox_events
    add column if not exists category       text,
    add column if not exists confidence     real,
    add column if not exists summary        text,
    add column if not exists extracted      jsonb not null default '{}'::jsonb,
    add column if not exists triage_status  text,
    add column if not exists triage_error   text,
    add column if not exists triaged_at     timestamptz,
    add column if not exists human_category text,
    add column if not exists locked_at      timestamptz;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'inbox_events_category_chk') then
        alter table cold_email.inbox_events add constraint inbox_events_category_chk
            check (category is null or category in ('interested','not_now','wrong_person',
                   'objection','out_of_office','unsubscribe','other'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'inbox_events_human_category_chk') then
        alter table cold_email.inbox_events add constraint inbox_events_human_category_chk
            check (human_category is null or human_category in ('interested','not_now','wrong_person',
                   'objection','out_of_office','unsubscribe','other'));
    end if;
    -- pending: waiting for triage; processing; done; needs_human; failed;
    -- skipped: not triaged (bounces, mail from before triage existed).
    if not exists (select 1 from pg_constraint where conname = 'inbox_events_triage_status_chk') then
        alter table cold_email.inbox_events add constraint inbox_events_triage_status_chk
            check (triage_status is null or triage_status in ('pending','processing','done',
                   'needs_human','failed','skipped'));
    end if;
end $$;

-- Mail that arrived before triage existed is not triaged retroactively (it
-- could otherwise trigger replies to old messages); "Run triage" re-queues one.
update cold_email.inbox_events set triage_status = 'skipped'
 where triage_status is null;

create index if not exists inbox_events_triage_idx
    on cold_email.inbox_events (received_at) where triage_status = 'pending';

-- ---------------------------------------------------------------------------
-- enrollments: snoozing and meeting state
-- ---------------------------------------------------------------------------
alter table cold_email.enrollments
    add column if not exists snoozed_until    timestamptz,
    add column if not exists referred_by_name text;

alter table cold_email.enrollments drop constraint if exists enrollments_status_chk;
alter table cold_email.enrollments add constraint enrollments_status_chk check (status in (
    'ready_for_outreach','processing','sequence_ready',
    'sending','sent','replied','bounced','unsubscribed','stopped',
    'manual_review','failed','snoozed','meeting_booked'
));

create index if not exists enrollments_snoozed_idx
    on cold_email.enrollments (snoozed_until) where status = 'snoozed';

-- ---------------------------------------------------------------------------
-- contacts: people referred to us who are not in the shared leads table
-- ---------------------------------------------------------------------------
-- An enrollment's lead_id is either a public.leads.id or a contacts.id.
create table if not exists cold_email.contacts (
    id                  uuid primary key default gen_random_uuid(),
    email               text        not null,
    first_name          text,
    last_name           text,
    company             text,
    job_title           text,
    timezone            text,
    referred_by_lead_id uuid,
    referred_by_name    text,
    source              text        not null default 'referral',
    created_at          timestamptz not null default now()
);
create unique index if not exists contacts_email_idx on cold_email.contacts (lower(email));

-- ---------------------------------------------------------------------------
-- reply_drafts: AI-written replies awaiting approval / auto-send
-- ---------------------------------------------------------------------------
-- kind: meeting_offer | booking_confirmation | slot_unavailable |
--       objection_reply | not_now_ack | referral_ack | referral_ask
-- status: pending (waiting) -> approved -> sending -> sent
--         | rejected | cancelled | failed
-- auto_send_at null = never auto-sends; a person must approve it.
create table if not exists cold_email.reply_drafts (
    id             uuid primary key default gen_random_uuid(),
    event_id       uuid        not null references cold_email.inbox_events(id) on delete cascade,
    lead_id        uuid        not null references cold_email.enrollments(lead_id) on delete cascade,
    inbox_id       uuid        not null references cold_email.inboxes(id),
    kind           text        not null,
    to_email       text        not null,
    subject        text        not null,
    body           text        not null,
    offered_slots  jsonb       not null default '[]'::jsonb,
    in_reply_to    text,
    references_ids text[]      not null default '{}',
    status         text        not null default 'pending',
    auto_send_at   timestamptz,
    decided_by     text,
    decided_at     timestamptz,
    locked_at      timestamptz,
    sent_at        timestamptz,
    message_id     text,
    last_error     text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    constraint reply_drafts_status_chk check (status in
        ('pending','approved','sending','sent','rejected','cancelled','failed'))
);
create index if not exists reply_drafts_due_idx
    on cold_email.reply_drafts (auto_send_at) where status in ('pending','approved');
create index if not exists reply_drafts_lead_idx on cold_email.reply_drafts (lead_id);
create index if not exists reply_drafts_message_id_idx
    on cold_email.reply_drafts (message_id) where message_id is not null;

drop trigger if exists reply_drafts_touch_updated_at on cold_email.reply_drafts;
create trigger reply_drafts_touch_updated_at
    before update on cold_email.reply_drafts
    for each row execute function cold_email.touch_updated_at();

-- ---------------------------------------------------------------------------
-- meetings: bookings made through the calendar provider
-- ---------------------------------------------------------------------------
create table if not exists cold_email.meetings (
    id             uuid primary key default gen_random_uuid(),
    lead_id        uuid        references cold_email.enrollments(lead_id) on delete set null,
    event_id       uuid        references cold_email.inbox_events(id) on delete set null,
    provider       text        not null,
    external_id    text,
    start_at       timestamptz not null,
    end_at         timestamptz,
    attendee_email text,
    meeting_url    text,
    status         text        not null default 'booked',   -- booked | cancelled
    created_at     timestamptz not null default now(),
    constraint meetings_external_unique unique (provider, external_id)
);
create index if not exists meetings_start_idx on cold_email.meetings (start_at);

alter table cold_email.contacts     enable row level security;
alter table cold_email.reply_drafts enable row level security;
alter table cold_email.meetings     enable row level security;

-- ---------------------------------------------------------------------------
-- Lead details now also cover referred contacts. The row shape (type
-- lead_detail) is unchanged, so re-running older migration files stays safe.
-- ---------------------------------------------------------------------------
create or replace function cold_email._lead_details(p_ids uuid[])
returns setof cold_email.lead_detail
language sql stable security definer set search_path = ''
as $$
    select e.lead_id,
           coalesce(nullif(trim(l.email), ''), lp.email, c.email),
           coalesce(l.first_name, lp.first_name, c.first_name),
           coalesce(l.last_name, lp.last_name, c.last_name),
           coalesce(e.company, lp.company, cp.name, p.company_name, c.company),
           coalesce(e.job_title, p.title, c.job_title),
           coalesce(e.timezone, c.timezone),
           l.timezone,
           l.state,
           p.country,
           p.state,
           cp.location,
           coalesce(l.status, 'New'),
           (l.id is not null or c.id is not null),
           e.status
      from cold_email.enrollments e
      left join public.leads l             on l.id = e.lead_id
      left join cold_email.contacts c      on c.id = e.lead_id
      left join public.lead_profiles lp    on lp.lead_id = l.lead_id
      left join public.company_profiles cp on cp.id = lp.company_profile_id
      left join lateral (
            select pr.title, pr.company_name, pr.country, pr.state
              from public.prospects pr
             where pr.lead_id = l.lead_id
             order by pr.updated_at desc nulls last
             limit 1
      ) p on true
     where e.lead_id = any(p_ids);
$$;

-- ---------------------------------------------------------------------------
-- Replacements of 0001 functions
-- ---------------------------------------------------------------------------
-- Replies to our drafts thread under the draft's Message-ID, so match those too.
create or replace function cold_email.match_lead_by_message_ids(p_message_ids text[])
returns uuid
language sql stable security definer set search_path = ''
as $$
    select lead_id from (
        select lead_id from cold_email.emails where message_id = any(p_message_ids)
        union all
        select lead_id from cold_email.reply_drafts where message_id = any(p_message_ids)
    ) m
    limit 1;
$$;

-- A later reply must not knock a booked meeting back to plain 'replied'.
create or replace function cold_email.cancel_sequence(
    p_lead_id uuid, p_lead_status text, p_reason text
)
returns int
language plpgsql security definer set search_path = ''
as $$
declare
    v_count int;
begin
    update cold_email.emails
       set status = 'cancelled', last_error = left(p_reason, 500),
           inbox_id = null, cap_day = null
     where lead_id = p_lead_id and status in ('pending','sending');
    get diagnostics v_count = row_count;

    update cold_email.enrollments
       set status = case when p_lead_status = 'replied' and status = 'meeting_booked'
                         then status else p_lead_status end,
           snoozed_until = case when p_lead_status = 'replied' then null else snoozed_until end,
           last_error = left(p_reason, 500)
     where lead_id = p_lead_id;

    return v_count;
end;
$$;

-- New inbound mail starts out waiting for triage (bounces are not replies).
create or replace function cold_email.record_inbox_event(
    p_inbox_id    uuid,
    p_lead_id     uuid,
    p_event_type  text,
    p_from_email  text,
    p_to_email    text,
    p_subject     text,
    p_message_id  text,
    p_in_reply_to text,
    p_snippet     text,
    p_received_at timestamptz
)
returns boolean
language sql security definer set search_path = ''
as $$
    with ins as (
        insert into cold_email.inbox_events
            (inbox_id, lead_id, event_type, from_email, to_email, subject,
             message_id, in_reply_to, snippet, received_at, triage_status)
        values (p_inbox_id, p_lead_id, p_event_type, p_from_email, p_to_email,
                p_subject, p_message_id, p_in_reply_to, p_snippet, p_received_at,
                case when p_event_type in ('reply','auto_reply','unsubscribe')
                     then 'pending' else 'skipped' end)
        on conflict (inbox_id, message_id) do nothing
        returning 1
    )
    select exists (select 1 from ins);
$$;

-- list_inbox_events gains the triage fields (return type changes, so drop).
drop function if exists cold_email.list_inbox_events(text[], int);
create or replace function cold_email.list_inbox_events(
    p_types      text[] default array['reply'],
    p_limit      int    default 50,
    p_categories text[] default null
)
returns table (
    id             uuid,
    event_type     text,
    lead_id        uuid,
    lead_email     text,
    company        text,
    job_title      text,
    persona        text,
    lead_status    text,
    from_email     text,
    subject        text,
    snippet        text,
    message_id     text,
    inbox_email    text,
    received_at    timestamptz,
    category       text,
    human_category text,
    confidence     real,
    summary        text,
    extracted      jsonb,
    triage_status  text,
    triage_error   text
)
language sql stable security definer set search_path = ''
as $$
    select v.id, v.event_type, v.lead_id, e.email, e.company, e.job_title,
           e.persona, e.status, v.from_email, v.subject, v.snippet,
           v.message_id, i.email, v.received_at,
           v.category, v.human_category, v.confidence, v.summary, v.extracted,
           v.triage_status, v.triage_error
      from cold_email.inbox_events v
      join cold_email.inboxes i on i.id = v.inbox_id
      left join cold_email.enrollments e on e.lead_id = v.lead_id
     where (p_types is null or v.event_type = any(p_types))
       and (p_categories is null or coalesce(v.human_category, v.category) = any(p_categories))
     order by v.received_at desc
     limit least(greatest(p_limit, 1), 200);
$$;

-- ---------------------------------------------------------------------------
-- Triage queue
-- ---------------------------------------------------------------------------
-- Claims pending events (and ones stuck in processing for 15+ minutes).
create or replace function cold_email.claim_untriaged(p_limit int default 10)
returns table (
    id uuid, inbox_id uuid, lead_id uuid, event_type text, from_email text,
    subject text, snippet text, message_id text, in_reply_to text,
    received_at timestamptz, human_category text
)
language sql security definer set search_path = ''
as $$
    with claimed as (
        select v.id from cold_email.inbox_events v
         where v.lead_id is not null
           and (v.triage_status = 'pending'
                or (v.triage_status = 'processing' and v.locked_at < now() - interval '15 minutes'))
         order by v.received_at
         limit least(greatest(p_limit, 1), 50)
         for update skip locked
    )
    update cold_email.inbox_events ev
       set triage_status = 'processing', locked_at = now()
      from claimed c
     where ev.id = c.id
    returning ev.id, ev.inbox_id, ev.lead_id, ev.event_type, ev.from_email,
              ev.subject, ev.snippet, ev.message_id, ev.in_reply_to,
              ev.received_at, ev.human_category;
$$;

-- What the classifier needs besides the reply itself: the last thing we sent
-- this lead, and the slots most recently offered to them.
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
               and d.status in ('sent','pending','approved')
               and jsonb_array_length(d.offered_slots) > 0
             order by d.created_at desc
             limit 1
        ), '[]'::jsonb),
        'has_meeting', exists (
            select 1 from cold_email.meetings mt
             where mt.lead_id = p_lead_id and mt.status = 'booked' and mt.start_at > now()
        )
    );
$$;

create or replace function cold_email.save_triage(
    p_event_id   uuid,
    p_category   text,
    p_confidence real,
    p_summary    text,
    p_extracted  jsonb,
    p_status     text,
    p_error      text default null
)
returns void
language sql security definer set search_path = ''
as $$
    update cold_email.inbox_events
       set category = p_category, confidence = p_confidence,
           summary = left(p_summary, 500), extracted = coalesce(p_extracted, '{}'::jsonb),
           triage_status = p_status, triage_error = left(p_error, 1000),
           triaged_at = now(), locked_at = null
     where id = p_event_id;
$$;

-- Re-queue an event, optionally with a person's correction of the category.
create or replace function cold_email.retriage_event(
    p_event_id uuid, p_category text default null
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
    update cold_email.inbox_events
       set triage_status = 'pending', triage_error = null, locked_at = null,
           human_category = coalesce(p_category, human_category)
     where id = p_event_id and lead_id is not null
       and event_type in ('reply','auto_reply','unsubscribe');
    return found;
end;
$$;

-- ---------------------------------------------------------------------------
-- Actions
-- ---------------------------------------------------------------------------
-- "Not now": stop everything and come back later with a fresh sequence.
create or replace function cold_email.snooze_lead(
    p_lead_id uuid, p_until timestamptz, p_reason text
)
returns void
language sql security definer set search_path = ''
as $$
    update cold_email.emails
       set status = 'cancelled', last_error = left(p_reason, 500), inbox_id = null, cap_day = null
     where lead_id = p_lead_id and status in ('pending','sending');
    update cold_email.enrollments
       set status = 'snoozed', snoozed_until = p_until, last_error = left(p_reason, 500)
     where lead_id = p_lead_id;
$$;

-- Snoozed leads whose time has come go back to intake for a new sequence.
create or replace function cold_email.wake_snoozed(p_limit int default 50)
returns int
language plpgsql security definer set search_path = ''
as $$
declare
    v_count int;
begin
    with due as (
        select lead_id from cold_email.enrollments
         where status = 'snoozed' and snoozed_until <= now()
         order by snoozed_until
         limit least(greatest(p_limit, 1), 500)
         for update skip locked
    )
    update cold_email.enrollments e
       set status = 'ready_for_outreach', snoozed_until = null, last_error = null
      from due d
     where e.lead_id = d.lead_id
       and not cold_email.is_suppressed(e.email);
    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

-- Out of office: move the remaining emails so the next one lands on
-- p_until, keeping the gaps between them. Returns emails moved.
create or replace function cold_email.pause_sequence_until(
    p_lead_id uuid, p_until timestamptz
)
returns int
language plpgsql security definer set search_path = ''
as $$
declare
    v_first timestamptz;
    v_count int := 0;
begin
    select min(due_at) into v_first
      from cold_email.emails
     where lead_id = p_lead_id and status = 'pending';
    if v_first is not null and v_first < p_until then
        update cold_email.emails
           set due_at = due_at + (p_until - v_first)
         where lead_id = p_lead_id and status = 'pending';
        get diagnostics v_count = row_count;
    end if;
    -- An auto-reply is not a human reply: keep the sequence running.
    update cold_email.enrollments
       set status = case when status = 'replied' then 'sending' else status end
     where lead_id = p_lead_id
       and exists (select 1 from cold_email.emails
                    where lead_id = p_lead_id and status = 'pending');
    return v_count;
end;
$$;

-- Enroll a referred person. If they are already in the shared leads table
-- that lead is used; otherwise a cold_email contact is created.
create or replace function cold_email.add_referral(
    p_from_lead_id     uuid,
    p_email            text,
    p_first_name       text,
    p_last_name        text,
    p_job_title        text,
    p_company          text,
    p_timezone         text,
    p_referred_by_name text
)
returns table (outcome text, lead_id uuid)
language plpgsql security definer set search_path = ''
as $$
#variable_conflict use_column
declare
    v_email text := lower(trim(coalesce(p_email, '')));
    v_id    uuid;
begin
    if v_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then
        outcome := 'invalid_email'; return next; return;
    end if;
    if cold_email.is_suppressed(v_email) then
        outcome := 'suppressed'; return next; return;
    end if;
    select e.lead_id into v_id from cold_email.enrollments e where lower(e.email) = v_email;
    if found then
        outcome := 'already_enrolled'; lead_id := v_id; return next; return;
    end if;

    select l.id into v_id from public.leads l where lower(trim(l.email)) = v_email limit 1;
    if v_id is null then
        insert into cold_email.contacts as c
            (email, first_name, last_name, company, job_title, timezone,
             referred_by_lead_id, referred_by_name)
        values (v_email, nullif(trim(p_first_name), ''), nullif(trim(p_last_name), ''),
                nullif(trim(p_company), ''), nullif(trim(p_job_title), ''),
                nullif(trim(p_timezone), ''), p_from_lead_id, nullif(trim(p_referred_by_name), ''))
        on conflict (lower(email)) do update set email = excluded.email
        returning c.id into v_id;
    end if;

    insert into cold_email.enrollments (lead_id, email, job_title, company, source, referred_by_name)
    values (v_id, v_email, nullif(trim(p_job_title), ''), nullif(trim(p_company), ''), 'referral',
            nullif(trim(p_referred_by_name), ''))
    on conflict do nothing;
    outcome := 'enrolled'; lead_id := v_id; return next;
end;
$$;

-- Who referred these leads, for the sequence's opening line.
create or replace function cold_email.referral_names(p_ids uuid[])
returns table (lead_id uuid, referred_by_name text)
language sql stable security definer set search_path = ''
as $$
    select e.lead_id, e.referred_by_name from cold_email.enrollments e
     where e.lead_id = any(p_ids) and e.referred_by_name is not null;
$$;

-- Unsubscribe "everywhere": the shared lead's status becomes DNC so other
-- projects stop too. Only the status value is written. Returns true if a
-- shared lead was updated (referred contacts have none).
create or replace function cold_email.mark_dnc(p_lead_id uuid)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
    update public.leads set status = 'DNC', updated_at = now()
     where id = p_lead_id and status is distinct from 'DNC';
    return found;
end;
$$;

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
    insert into cold_email.meetings
        (lead_id, event_id, provider, external_id, start_at, end_at, attendee_email, meeting_url)
    values (p_lead_id, p_event_id, p_provider, p_external_id, p_start_at, p_end_at,
            p_attendee_email, p_meeting_url)
    on conflict (provider, external_id) do update set start_at = excluded.start_at
    returning id into v_id;
    update cold_email.enrollments set status = 'meeting_booked', snoozed_until = null
     where lead_id = p_lead_id;
    return v_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- Drafts
-- ---------------------------------------------------------------------------
-- A new draft supersedes the lead's older unsent ones.
create or replace function cold_email.create_draft(
    p_event_id      uuid,
    p_lead_id       uuid,
    p_inbox_id      uuid,
    p_kind          text,
    p_to_email      text,
    p_subject       text,
    p_body          text,
    p_offered_slots jsonb,
    p_in_reply_to   text,
    p_references    text[],
    p_auto_send_at  timestamptz
)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
    v_id uuid;
begin
    update cold_email.reply_drafts
       set status = 'cancelled', last_error = 'superseded by a newer reply'
     where lead_id = p_lead_id and status in ('pending','approved');
    insert into cold_email.reply_drafts
        (event_id, lead_id, inbox_id, kind, to_email, subject, body, offered_slots,
         in_reply_to, references_ids, auto_send_at)
    values (p_event_id, p_lead_id, p_inbox_id, p_kind, p_to_email, p_subject, p_body,
            coalesce(p_offered_slots, '[]'::jsonb), p_in_reply_to,
            coalesce(p_references, '{}'), p_auto_send_at)
    returning id into v_id;
    return v_id;
end;
$$;

create or replace function cold_email.cancel_lead_drafts(p_lead_id uuid, p_reason text)
returns int
language plpgsql security definer set search_path = ''
as $$
declare
    v_count int;
begin
    update cold_email.reply_drafts set status = 'cancelled', last_error = left(p_reason, 500)
     where lead_id = p_lead_id and status in ('pending','approved');
    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

create or replace function cold_email.list_drafts(
    p_statuses text[] default array['pending','approved'],
    p_limit    int    default 50
)
returns table (
    id uuid, event_id uuid, lead_id uuid, kind text, to_email text,
    subject text, body text, offered_slots jsonb, status text,
    auto_send_at timestamptz, decided_by text, decided_at timestamptz,
    sent_at timestamptz, last_error text, created_at timestamptz,
    company text, job_title text, category text, confidence real,
    reply_subject text, reply_snippet text, reply_received_at timestamptz,
    inbox_email text
)
language sql stable security definer set search_path = ''
as $$
    select d.id, d.event_id, d.lead_id, d.kind, d.to_email, d.subject, d.body,
           d.offered_slots, d.status, d.auto_send_at, d.decided_by, d.decided_at,
           d.sent_at, d.last_error, d.created_at,
           e.company, e.job_title, coalesce(v.human_category, v.category), v.confidence,
           v.subject, v.snippet, v.received_at, i.email
      from cold_email.reply_drafts d
      join cold_email.inbox_events v on v.id = d.event_id
      join cold_email.inboxes i on i.id = d.inbox_id
      left join cold_email.enrollments e on e.lead_id = d.lead_id
     where p_statuses is null or d.status = any(p_statuses)
     order by case when d.status in ('pending','approved') then 0 else 1 end,
              coalesce(d.auto_send_at, d.created_at) asc, d.created_at desc
     limit least(greatest(p_limit, 1), 200);
$$;

-- A person edits a waiting draft.
create or replace function cold_email.update_draft(p_id uuid, p_subject text, p_body text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
    update cold_email.reply_drafts set subject = p_subject, body = p_body
     where id = p_id and status in ('pending','failed');
    return found;
end;
$$;

-- approve: send at the next sender run (also retries a failed send).
-- reject: never send.
create or replace function cold_email.decide_draft(p_id uuid, p_decision text, p_user text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
    update cold_email.reply_drafts
       set status = case when p_decision = 'approve' then 'approved' else 'rejected' end,
           decided_by = left(p_user, 200), decided_at = now(), last_error = null
     where id = p_id and p_decision in ('approve','reject')
       and (status = 'pending' or (status = 'failed' and p_decision = 'approve'));
    return found;
end;
$$;

-- Drafts to send now: approved ones, and waiting ones past auto_send_at.
create or replace function cold_email.claim_drafts_to_send(p_limit int default 10)
returns table (
    id uuid, event_id uuid, lead_id uuid, inbox_id uuid, kind text,
    to_email text, subject text, body text, in_reply_to text,
    references_ids text[], created_at timestamptz, status_before text
)
language sql security definer set search_path = ''
as $$
    with claimed as (
        select d.id, d.status from cold_email.reply_drafts d
         where d.status = 'approved'
            or (d.status = 'pending' and d.auto_send_at is not null and d.auto_send_at <= now())
            or (d.status = 'sending' and d.locked_at < now() - interval '30 minutes')
         order by coalesce(d.decided_at, d.auto_send_at)
         limit least(greatest(p_limit, 1), 50)
         for update skip locked
    )
    update cold_email.reply_drafts rd
       set status = 'sending', locked_at = now()
      from claimed c
     where rd.id = c.id
    returning rd.id, rd.event_id, rd.lead_id, rd.inbox_id, rd.kind, rd.to_email,
              rd.subject, rd.body, rd.in_reply_to, rd.references_ids, rd.created_at,
              c.status;
$$;

create or replace function cold_email.finish_draft(
    p_id uuid, p_status text, p_message_id text default null, p_error text default null
)
returns void
language sql security definer set search_path = ''
as $$
    update cold_email.reply_drafts
       set status = p_status, locked_at = null,
           message_id = coalesce(p_message_id, message_id),
           sent_at = case when p_status = 'sent' then now() else sent_at end,
           last_error = left(p_error, 1000)
     where id = p_id;
$$;

-- Has the lead written again since this draft was made?
create or replace function cold_email.newer_reply_exists(p_lead_id uuid, p_since timestamptz)
returns boolean
language sql stable security definer set search_path = ''
as $$
    select exists (
        select 1 from cold_email.inbox_events
         where lead_id = p_lead_id and received_at > p_since
           and event_type in ('reply','unsubscribe')
    );
$$;

-- ---------------------------------------------------------------------------
-- Reporting
-- ---------------------------------------------------------------------------
create or replace function cold_email.list_meetings(p_limit int default 100)
returns table (
    id uuid, lead_id uuid, provider text, external_id text, start_at timestamptz,
    end_at timestamptz, attendee_email text, meeting_url text, status text,
    company text, job_title text, created_at timestamptz
)
language sql stable security definer set search_path = ''
as $$
    select m.id, m.lead_id, m.provider, m.external_id, m.start_at, m.end_at,
           m.attendee_email, m.meeting_url, m.status, e.company, e.job_title, m.created_at
      from cold_email.meetings m
      left join cold_email.enrollments e on e.lead_id = m.lead_id
     order by m.start_at desc
     limit least(greatest(p_limit, 1), 500);
$$;

create or replace function cold_email.list_snoozed(p_limit int default 100)
returns table (
    lead_id uuid, email text, company text, job_title text,
    snoozed_until timestamptz, last_error text, updated_at timestamptz
)
language sql stable security definer set search_path = ''
as $$
    select e.lead_id, e.email, e.company, e.job_title, e.snoozed_until, e.last_error, e.updated_at
      from cold_email.enrollments e
     where e.status = 'snoozed'
     order by e.snoozed_until
     limit least(greatest(p_limit, 1), 500);
$$;

-- KPI: reply-to-meeting rate = leads with a booked meeting / leads who replied.
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
             where status = 'booked' and lead_id is not null),
        'meetings_upcoming', (
            select count(*) from cold_email.meetings where status = 'booked' and start_at > now()),
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
