-- OUT-01 Cold Email Automation — initial schema
--
-- This database is shared with other projects. Everything this service owns
-- lives in its own `cold_email` schema, and this file never creates, alters,
-- indexes or triggers anything in `public`. The service only READS these
-- existing tables:
--
--   public.leads             identity, email, name, timezone, state, status
--   public.lead_profiles     company (joined on lead_id)
--   public.company_profiles  company name fallback, location (for timezone)
--   public.prospects         job title / company / country / state
--   public.email_nurture_state  to avoid double-emailing leads in another journey
--
-- Deliberate choices that keep the shared tables untouched:
--   * No foreign keys into public. An FK would install system triggers on
--     public.leads and would block (or cascade into) other projects' deletes.
--     cold_email.enrollments.lead_id is a soft reference to public.leads.id.
--   * No views over public. A view pins the columns it reads, so another
--     project's `alter table leads drop column ...` would start failing.
--     The functions below read public tables instead; a plain function body
--     records no dependency, so it never blocks another project's change.
--   * The service talks to the database only through the Supabase REST API,
--     by calling the functions in the API section. Tables are never exposed.
--
-- Safe to re-run. Runs in one transaction: all or nothing.
--
-- AFTER RUNNING: Supabase dashboard > Project Settings > Data API >
-- Exposed schemas > add `cold_email`. Only service_role can use anything in
-- it (see Privileges at the end); the publishable key gets nothing.

begin;

create schema if not exists cold_email;

-- ---------------------------------------------------------------------------
-- Runtime settings (table-driven: no redeploy needed to change behaviour)
-- ---------------------------------------------------------------------------
create table if not exists cold_email.settings (
    key         text primary key,
    value       jsonb       not null,
    description text,
    updated_at  timestamptz not null default now()
);

insert into cold_email.settings (key, value, description) values
    ('business_hours',
     '{"start_hour": 9, "end_hour": 17, "weekdays": [0,1,2,3,4]}'::jsonb,
     'Default send window, applied in each lead''s own local time. weekdays: 0=Mon .. 6=Sun.'),
    ('business_hours_by_region',
     '{"BD": {"weekdays": [6,0,1,2,3]}, "SA": {"weekdays": [6,0,1,2,3]},
       "IL": {"weekdays": [6,0,1,2,3]}, "EG": {"weekdays": [6,0,1,2,3]},
       "QA": {"weekdays": [6,0,1,2,3]}, "KW": {"weekdays": [6,0,1,2,3]},
       "OM": {"weekdays": [6,0,1,2,3]}, "BH": {"weekdays": [6,0,1,2,3]},
       "JO": {"weekdays": [6,0,1,2,3]}, "IQ": {"weekdays": [6,0,1,2,3]},
       "DZ": {"weekdays": [6,0,1,2,3]}}'::jsonb,
     'Per-region changes to business_hours, keyed by ISO country code (e.g. "BD") or IANA timezone (e.g. "America/Phoenix"). A timezone key beats a country key. Each entry only needs the fields that differ, e.g. {"weekdays": [6,0,1,2,3]} for a Sun-Thu week or {"start_hour": 10}.'),
    ('step_gaps_business_days',
     '[0, 3, 7, 12]'::jsonb,
     'Business-day offset of each sequence step from sequence creation. Length defines sequence length.'),
    ('default_daily_cap',
     '15'::jsonb,
     'Fallback per-inbox daily send cap when the inbox row does not set one.'),
    ('intake_batch_size',   '10'::jsonb,  'Enrollments claimed per intake tick.'),
    ('send_batch_size',     '25'::jsonb,  'Steps claimed per sending tick.'),
    ('max_send_attempts',   '3'::jsonb,   'Attempts before a step is marked failed.'),
    ('sequence_enabled',    'true'::jsonb,'Global kill switch for sending.'),
    ('llm_max_body_chars',  '2200'::jsonb,'Validation ceiling for a single email body.'),
    ('llm_min_body_chars',  '120'::jsonb, 'Validation floor for a single email body.'),
    ('contactable_lead_statuses',
     '["New", "No Answer", "Busy", "Call Later", "In Progress"]'::jsonb,
     'public.leads.status values that may be emailed. Checked at enrollment and again before every send; any other status (DNC, Paid, Not Interested, ...) stops the sequence.'),
    ('skip_if_in_email_nurture',
     'true'::jsonb,
     'Refuse to enroll a lead that already has a public.email_nurture_state journey.'),
    ('auto_enroll',
     '{"enabled": false, "sources": [], "batch_size": 50}'::jsonb,
     'When enabled, each intake tick enrolls contactable leads whose public.leads.source is in "sources". Empty sources = nothing is auto-enrolled.')
on conflict (key) do nothing;

-- ---------------------------------------------------------------------------
-- Enrollments: one row per shared lead in cold outreach, and its sequence
-- ---------------------------------------------------------------------------
-- lead_id is public.leads.id (soft reference, see header).
-- email is a snapshot taken at enrollment, used for de-duplication and for
-- matching inbound mail; sends always go to the live public.leads address.
-- job_title / company / timezone override whatever the shared tables say.
-- persona .. sequence_version describe the current sequence; regenerating
-- bumps sequence_version and cancels the previous version's unsent emails.
--
-- status lifecycle:
--   ready_for_outreach -> processing -> sequence_ready -> sending -> sent
--   terminal/interrupt: replied | bounced | unsubscribed | stopped |
--                       manual_review | failed
create table if not exists cold_email.enrollments (
    lead_id          uuid primary key,
    email            text        not null,
    job_title        text,
    company          text,
    timezone         text,       -- IANA name; overrides whatever the shared tables imply
    status           text        not null default 'ready_for_outreach',
    source           text        not null default 'manual',   -- manual | api | auto
    persona          text,
    routing_mode     text,       -- keyword | llm
    provider         text,
    model            text,
    sequence_version int         not null default 0,
    last_error       text,
    enrolled_at      timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    constraint enrollments_status_chk check (status in (
        'ready_for_outreach','processing','sequence_ready',
        'sending','sent','replied','bounced','unsubscribed','stopped',
        'manual_review','failed'
    )),
    constraint enrollments_persona_chk check (persona is null or persona in ('ciso','it','hr'))
);

-- One cold sequence per address, even if several shared leads share it.
create unique index if not exists enrollments_email_idx
    on cold_email.enrollments (lower(email));
create index if not exists enrollments_status_idx
    on cold_email.enrollments (status);

-- ---------------------------------------------------------------------------
-- Inboxes
-- ---------------------------------------------------------------------------
-- credential_ref names an env-var prefix, e.g. 'INBOX_A' resolves to
-- INBOX_A_SMTP_PASSWORD / INBOX_A_IMAP_PASSWORD / INBOX_A_CLIENT_SECRET.
-- Secrets are never stored in the database.
-- poll_cursor: last IMAP UID seen, or the last Graph receive time (epoch secs).
create table if not exists cold_email.inboxes (
    id             uuid primary key default gen_random_uuid(),
    email          text        not null unique,
    display_name   text,
    provider       text        not null default 'smtp',     -- smtp | graph
    credential_ref text        not null,
    daily_cap      int,
    active         boolean     not null default true,
    poll_cursor    bigint      not null default 0,
    config         jsonb       not null default '{}'::jsonb, -- host/port overrides, tenant_id, etc.
    created_at     timestamptz not null default now(),
    constraint inboxes_provider_chk check (provider in ('smtp','graph'))
);

-- ---------------------------------------------------------------------------
-- Emails: every drafted email, and once sent, the record of sending it
-- ---------------------------------------------------------------------------
-- inbox_id + cap_day are set when a send slot is reserved, so an inbox's
-- usage for a day is simply its emails that are sending or sent that day;
-- no separate counter to drift out of step.
create table if not exists cold_email.emails (
    id           uuid primary key default gen_random_uuid(),
    lead_id      uuid        not null references cold_email.enrollments(lead_id) on delete cascade,
    version      int         not null,              -- enrollments.sequence_version it belongs to
    step_number  int         not null,
    subject      text        not null,
    body         text        not null,
    due_at       timestamptz not null,
    status       text        not null default 'pending',   -- pending|sending|sent|cancelled|failed
    attempts     int         not null default 0,
    locked_at    timestamptz,
    last_error   text,
    inbox_id     uuid        references cold_email.inboxes(id),
    cap_day      date,
    to_email     text,
    message_id   text,
    thread_id    text,
    sent_at      timestamptz,
    created_at   timestamptz not null default now(),
    constraint emails_unique_step unique (lead_id, version, step_number),
    constraint emails_status_chk check (status in
        ('pending','sending','sent','cancelled','failed'))
);

create index if not exists emails_due_idx
    on cold_email.emails (due_at) where status = 'pending';
create index if not exists emails_lead_idx on cold_email.emails (lead_id);
create index if not exists emails_message_id_idx
    on cold_email.emails (message_id) where message_id is not null;
create index if not exists emails_inbox_day_idx
    on cold_email.emails (inbox_id, cap_day) where inbox_id is not null;

-- ---------------------------------------------------------------------------
-- Inbound events (IMAP / Graph poll results): replies, bounces, opt-outs
-- ---------------------------------------------------------------------------
create table if not exists cold_email.inbox_events (
    id           uuid primary key default gen_random_uuid(),
    inbox_id     uuid        not null references cold_email.inboxes(id) on delete cascade,
    lead_id      uuid        references cold_email.enrollments(lead_id) on delete set null,
    event_type   text        not null,   -- reply|bounce|unsubscribe|auto_reply|unknown
    from_email   text,
    to_email     text,
    subject      text,
    message_id   text,
    in_reply_to  text,
    snippet      text,
    received_at  timestamptz not null default now(),
    constraint inbox_events_type_chk check (event_type in
        ('reply','bounce','unsubscribe','auto_reply','unknown')),
    constraint inbox_events_unique_msg unique (inbox_id, message_id)
);

-- ---------------------------------------------------------------------------
-- Suppression list — checked before every single send
-- ---------------------------------------------------------------------------
create table if not exists cold_email.suppression_list (
    id         uuid primary key default gen_random_uuid(),
    email      text,
    domain     text,
    reason     text        not null,   -- unsubscribed|hard_bounce|manual|complaint
    created_at timestamptz not null default now(),
    constraint suppression_target_chk check (email is not null or domain is not null)
);

create unique index if not exists suppression_email_idx
    on cold_email.suppression_list (lower(email)) where email is not null;
create unique index if not exists suppression_domain_idx
    on cold_email.suppression_list (lower(domain)) where domain is not null;

-- Row-level security with no policies: nothing reaches these tables through
-- the REST API directly. The functions below run as the table owner, which
-- RLS does not restrict.
alter table cold_email.settings         enable row level security;
alter table cold_email.enrollments      enable row level security;
alter table cold_email.inboxes          enable row level security;
alter table cold_email.emails           enable row level security;
alter table cold_email.inbox_events     enable row level security;
alter table cold_email.suppression_list enable row level security;

-- ===========================================================================
-- API
-- ===========================================================================
-- The service has no Postgres connection. It reaches the database only
-- through Supabase's REST API with the service-role key, by calling the
-- functions below as POST /rest/v1/rpc/<name>. Each call is one transaction,
-- which is what keeps claim / record / cancel atomic.
--
-- Every function is SECURITY DEFINER: it runs with the rights of the role
-- that ran this migration, so the API role needs no access to any table —
-- only EXECUTE on these functions, granted to service_role alone at the end
-- of this file. search_path is pinned to '' and every name is qualified, the
-- standard guard against search_path hijacking in definer functions.
-- ===========================================================================

-- One enrolled lead, assembled from the shared read-only tables.
-- Enrollment overrides win; prospecting data fills gaps. Left joins so a lead
-- deleted from public.leads still comes back (lead_exists = false) instead of
-- silently vanishing.
do $$
begin
    if not exists (
        select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
         where n.nspname = 'cold_email' and t.typname = 'lead_detail'
    ) then
        -- The service turns the raw location fields into one timezone
        -- (app/regions.py): override, then leads.timezone unless it is the
        -- column default, then state/country, then the default, then UTC.
        create type cold_email.lead_detail as (
            id                uuid,
            email             text,
            first_name        text,
            last_name         text,
            company           text,
            job_title         text,
            timezone_override text,
            lead_timezone     text,
            lead_state        text,
            prospect_country  text,
            prospect_state    text,
            company_location  jsonb,
            lead_status       text,
            lead_exists       boolean,
            status            text
        );
    end if;
end $$;

create or replace function cold_email._lead_details(p_ids uuid[])
returns setof cold_email.lead_detail
language sql stable security definer set search_path = ''
as $$
    select e.lead_id,
           coalesce(nullif(trim(l.email), ''), lp.email),
           coalesce(l.first_name, lp.first_name),
           coalesce(l.last_name, lp.last_name),
           coalesce(e.company, lp.company, cp.name, p.company_name),
           coalesce(e.job_title, p.title),
           e.timezone,
           l.timezone,
           l.state,
           p.country,
           p.state,
           cp.location,
           coalesce(l.status, 'New'),
           l.id is not null,
           e.status
      from cold_email.enrollments e
      left join public.leads l             on l.id = e.lead_id
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
-- Helpers used both internally and over the API
-- ---------------------------------------------------------------------------
create or replace function cold_email.is_suppressed(p_email text)
returns boolean
language sql stable security definer set search_path = ''
as $$
    select exists (
        select 1 from cold_email.suppression_list s
        where (s.email is not null and lower(s.email) = lower(p_email))
           or (s.domain is not null and lower(s.domain) = lower(split_part(p_email, '@', 2)))
    );
$$;

-- Reserve today's send slot on an inbox for a claimed email, respecting the
-- inbox's daily cap. Returns true if the caller may send. A failed or
-- deferred send gives the slot back by clearing inbox_id / cap_day.
create or replace function cold_email.reserve_inbox_slot(
    p_email_id uuid, p_inbox_id uuid, p_day date, p_cap int
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
    v_used int;
begin
    -- Lock the inbox row so concurrent reservations for it queue up here
    -- instead of both reading the same count.
    perform 1 from cold_email.inboxes where id = p_inbox_id for update;

    select count(*) into v_used
      from cold_email.emails
     where inbox_id = p_inbox_id and cap_day = p_day
       and status in ('sending','sent');
    if v_used >= p_cap then
        return false;
    end if;

    update cold_email.emails set inbox_id = p_inbox_id, cap_day = p_day
     where id = p_email_id;
    return true;
end;
$$;

-- ---------------------------------------------------------------------------
-- Enrollment (the only way a shared lead enters cold outreach)
-- ---------------------------------------------------------------------------
-- p_ref is public.leads.id or public.leads.lead_id. Returns 'enrolled' or the
-- reason the lead was refused.
create or replace function cold_email.enroll_lead(
    p_ref           text,
    p_contactable   text[],
    p_skip_nurture  boolean,
    p_job_title     text default null,
    p_company       text default null,
    p_source        text default 'api',
    p_timezone      text default null
)
returns table (outcome text, lead_id uuid)
language plpgsql security definer set search_path = ''
as $$
#variable_conflict use_column
declare
    v        record;
    v_email  text;
    v_new    uuid;
begin
    select l.id,
           coalesce(nullif(trim(l.email), ''), lp.email) as email,
           coalesce(l.status, 'New') as status,
           exists (select 1 from public.email_nurture_state n
                    where n.lead_id = l.lead_id
                      and n.email_journey_started_at is not null) as in_nurture
      into v
      from public.leads l
      left join public.lead_profiles lp on lp.lead_id = l.lead_id
     where l.id::text = p_ref or l.lead_id = p_ref
     limit 1;

    if not found then
        outcome := 'not_found';
        return next;
        return;
    end if;

    lead_id := v.id;
    v_email := lower(trim(coalesce(v.email, '')));

    if v_email = '' then
        outcome := 'no_email';
    elsif not (v.status = any(p_contactable)) then
        outcome := 'not_contactable (' || v.status || ')';
    elsif p_skip_nurture and v.in_nurture then
        outcome := 'in_email_nurture';
    elsif cold_email.is_suppressed(v_email) then
        outcome := 'suppressed';
    else
        insert into cold_email.enrollments as e
            (lead_id, email, job_title, company, timezone, source)
        values (v.id, v_email, p_job_title, p_company, p_timezone, p_source)
        on conflict do nothing
        returning e.lead_id into v_new;
        outcome := case when v_new is null then 'already_enrolled' else 'enrolled' end;
    end if;
    return next;
end;
$$;

-- Enroll up to p_limit contactable leads whose public.leads.source is listed.
create or replace function cold_email.auto_enroll(
    p_sources      text[],
    p_contactable  text[],
    p_skip_nurture boolean,
    p_limit        int
)
returns int
language plpgsql security definer set search_path = ''
as $$
declare
    v_count int;
begin
    insert into cold_email.enrollments (lead_id, email, source)
    select distinct on (lower(x.email)) x.id, lower(x.email), 'auto'
      from (
            select l.id, l.created_at, l.lead_id,
                   coalesce(nullif(trim(l.email), ''), lp.email) as email
              from public.leads l
              left join public.lead_profiles lp on lp.lead_id = l.lead_id
             where l.source = any(p_sources)
               and coalesce(l.status, 'New') = any(p_contactable)
      ) x
     where x.email is not null
       and not cold_email.is_suppressed(x.email)
       and not exists (select 1 from cold_email.enrollments e where e.lead_id = x.id)
       and not (p_skip_nurture and exists (
             select 1 from public.email_nurture_state n
              where n.lead_id = x.lead_id
                and n.email_journey_started_at is not null))
     order by lower(x.email), x.created_at
     limit p_limit
    on conflict do nothing;

    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

-- ---------------------------------------------------------------------------
-- Intake
-- ---------------------------------------------------------------------------
-- Atomically move up to p_limit ready enrollments into 'processing' and
-- return their lead details. SKIP LOCKED lets several workers run safely.
create or replace function cold_email.claim_leads_for_intake(p_limit int)
returns setof cold_email.lead_detail
language plpgsql security definer set search_path = ''
as $$
declare
    v_ids uuid[];
begin
    with claimed as (
        select e.lead_id from cold_email.enrollments e
         where e.status = 'ready_for_outreach'
         order by e.enrolled_at
         limit p_limit
         for update skip locked
    ), updated as (
        update cold_email.enrollments e
           set status = 'processing'
          from claimed c
         where e.lead_id = c.lead_id
        returning e.lead_id
    )
    select array_agg(u.lead_id) into v_ids from updated u;

    if v_ids is not null then
        return query select * from cold_email._lead_details(v_ids);
    end if;
end;
$$;

create or replace function cold_email.mark_lead(p_lead_id uuid, p_status text, p_error text default null)
returns void
language sql security definer set search_path = ''
as $$
    update cold_email.enrollments set status = p_status, last_error = p_error
     where lead_id = p_lead_id;
$$;

create or replace function cold_email.set_lead_persona(p_lead_id uuid, p_persona text)
returns void
language sql security definer set search_path = ''
as $$
    update cold_email.enrollments set persona = p_persona where lead_id = p_lead_id;
$$;

-- Write a new version of the lead's sequence and flip the enrollment to
-- 'sequence_ready', in one transaction. Returns the version number.
-- p_steps: [{step_number, subject, body, due_at}, ...]
create or replace function cold_email.store_sequence(
    p_lead_id      uuid,
    p_persona      text,
    p_routing_mode text,
    p_provider     text,
    p_model        text,
    p_steps        jsonb
)
returns int
language plpgsql security definer set search_path = ''
as $$
declare
    v_version int;
begin
    -- Regeneration: whatever the previous version had not sent is dropped.
    update cold_email.emails set status = 'cancelled',
                                 last_error = 'superseded by regeneration'
     where lead_id = p_lead_id and status in ('pending','sending');

    update cold_email.enrollments
       set status = 'sequence_ready', persona = p_persona,
           routing_mode = p_routing_mode, provider = p_provider, model = p_model,
           sequence_version = sequence_version + 1, last_error = null
     where lead_id = p_lead_id
    returning sequence_version into v_version;

    insert into cold_email.emails (lead_id, version, step_number, subject, body, due_at)
    select p_lead_id, v_version,
           (s->>'step_number')::int, s->>'subject', s->>'body',
           (s->>'due_at')::timestamptz
      from jsonb_array_elements(p_steps) s;

    return v_version;
end;
$$;

-- ---------------------------------------------------------------------------
-- Sending
-- ---------------------------------------------------------------------------
-- Claim due steps. Suppression is checked here against the enrollment's email
-- snapshot, and again by the sender against the live address, alongside the
-- shared lead status, immediately before anything leaves.
create or replace function cold_email.claim_due_steps(p_limit int)
returns table (id uuid, lead_id uuid, step_number int,
               subject text, body text, attempts int)
language sql security definer set search_path = ''
as $$
    with claimed as (
        select m.id
          from cold_email.emails m
          join cold_email.enrollments e on e.lead_id = m.lead_id
         where m.status = 'pending'
           and m.due_at <= now()
           and e.status in ('sequence_ready','sending')
           and not cold_email.is_suppressed(e.email)
         order by m.due_at
         limit p_limit
         for update of m skip locked
    )
    update cold_email.emails em
       set status = 'sending', locked_at = now(), attempts = em.attempts + 1
      from claimed c
     where em.id = c.id
    returning em.id, em.lead_id, em.step_number, em.subject, em.body, em.attempts;
$$;

create or replace function cold_email.lead_for_step(p_lead_id uuid)
returns setof cold_email.lead_detail
language sql stable security definer set search_path = ''
as $$
    select * from cold_email._lead_details(array[p_lead_id]);
$$;

-- The first message sent to this lead, used to thread follow-ups.
create or replace function cold_email.thread_anchor(p_lead_id uuid)
returns text
language sql stable security definer set search_path = ''
as $$
    select message_id from cold_email.emails
     where lead_id = p_lead_id and message_id is not null
     order by sent_at asc
     limit 1;
$$;

create or replace function cold_email.active_inboxes()
returns setof cold_email.inboxes
language sql stable security definer set search_path = ''
as $$
    select * from cold_email.inboxes where active order by email;
$$;

-- Mark the email sent (its inbox was set at reservation) and advance the
-- enrollment.
create or replace function cold_email.record_send(
    p_email_id   uuid,
    p_to_email   text,
    p_message_id text,
    p_thread_id  text
)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
    v_lead_id uuid;
begin
    update cold_email.emails
       set status = 'sent', sent_at = now(), locked_at = null, last_error = null,
           to_email = p_to_email, message_id = p_message_id, thread_id = p_thread_id
     where id = p_email_id
    returning lead_id into v_lead_id;

    -- 'sending' after the first send, 'sent' once nothing is left.
    update cold_email.enrollments
       set status = case
               when not exists (
                   select 1 from cold_email.emails
                    where lead_id = v_lead_id and status in ('pending','sending')
               ) then 'sent'
               else 'sending'
           end
     where lead_id = v_lead_id and status in ('sequence_ready','sending');
end;
$$;

-- Return an email to 'pending' for another try, or bury it as failed.
-- Either way its reserved send slot is given back.
create or replace function cold_email.fail_step(
    p_step_id uuid, p_error text, p_permanent boolean, p_max_attempts int
)
returns void
language sql security definer set search_path = ''
as $$
    update cold_email.emails
       set status = case when p_permanent or attempts >= p_max_attempts
                         then 'failed' else 'pending' end,
           locked_at = null, inbox_id = null, cap_day = null,
           last_error = left(p_error, 1000)
     where id = p_step_id;
$$;

-- Push a claimed step back to pending with a later due time (hours/cap).
create or replace function cold_email.defer_step(p_step_id uuid, p_due_at timestamptz)
returns void
language sql security definer set search_path = ''
as $$
    update cold_email.emails
       set status = 'pending', due_at = p_due_at, locked_at = null,
           inbox_id = null, cap_day = null,
           attempts = greatest(attempts - 1, 0)
     where id = p_step_id;
$$;

-- Recover steps left in 'sending' by a crashed worker.
create or replace function cold_email.reclaim_stuck_steps(p_stale_minutes int default 30)
returns int
language plpgsql security definer set search_path = ''
as $$
declare
    v_count int;
begin
    update cold_email.emails
       set status = 'pending', locked_at = null, inbox_id = null, cap_day = null
     where status = 'sending'
       and locked_at < now() - make_interval(mins => p_stale_minutes);
    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

-- ---------------------------------------------------------------------------
-- Inbound handling
-- ---------------------------------------------------------------------------
create or replace function cold_email.update_inbox_cursor(p_inbox_id uuid, p_cursor bigint)
returns void
language sql security definer set search_path = ''
as $$
    update cold_email.inboxes set poll_cursor = greatest(poll_cursor, p_cursor)
     where id = p_inbox_id;
$$;

-- Find the lead a reply belongs to via In-Reply-To / References.
create or replace function cold_email.match_lead_by_message_ids(p_message_ids text[])
returns uuid
language sql stable security definer set search_path = ''
as $$
    select lead_id from cold_email.emails
     where message_id = any(p_message_ids)
     limit 1;
$$;

-- Only enrolled leads match: mail from anyone else in the shared leads table
-- is not ours to act on.
create or replace function cold_email.match_lead_by_email(p_address text)
returns uuid
language sql stable security definer set search_path = ''
as $$
    select lead_id from cold_email.enrollments where lower(email) = lower(p_address);
$$;

-- Insert an inbound event. Returns false if it was already recorded.
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
             message_id, in_reply_to, snippet, received_at)
        values (p_inbox_id, p_lead_id, p_event_type, p_from_email, p_to_email,
                p_subject, p_message_id, p_in_reply_to, p_snippet, p_received_at)
        on conflict (inbox_id, message_id) do nothing
        returning 1
    )
    select exists (select 1 from ins);
$$;

-- Stop everything outstanding for a lead. Returns steps cancelled.
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
       set status = p_lead_status, last_error = left(p_reason, 500)
     where lead_id = p_lead_id;

    return v_count;
end;
$$;

create or replace function cold_email.suppress(p_email text, p_reason text)
returns void
language sql security definer set search_path = ''
as $$
    insert into cold_email.suppression_list (email, reason) values (p_email, p_reason)
    on conflict (lower(email)) where email is not null do nothing;
$$;

-- ---------------------------------------------------------------------------
-- Settings, stats, health
-- ---------------------------------------------------------------------------
create or replace function cold_email.get_settings()
returns setof cold_email.settings
language sql stable security definer set search_path = ''
as $$
    select * from cold_email.settings order by key;
$$;

create or replace function cold_email.set_setting(
    p_key text, p_value jsonb, p_description text default null
)
returns void
language sql security definer set search_path = ''
as $$
    insert into cold_email.settings as s (key, value, description, updated_at)
    values (p_key, p_value, p_description, now())
    on conflict (key) do update
        set value = excluded.value,
            description = coalesce(excluded.description, s.description),
            updated_at = now();
$$;

create or replace function cold_email.stats()
returns jsonb
language sql stable security definer set search_path = ''
as $$
    select jsonb_build_object(
        'enrollments', coalesce((
            select jsonb_object_agg(status, n)
              from (select status, count(*) as n from cold_email.enrollments group by status) x
        ), '{}'::jsonb),
        'emails', coalesce((
            select jsonb_object_agg(status, n)
              from (select status, count(*) as n from cold_email.emails group by status) x
        ), '{}'::jsonb),
        'inboxes', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'email', i.email, 'active', i.active,
                       'sent_today', (select count(*) from cold_email.emails m
                                       where m.inbox_id = i.id
                                         and m.cap_day = (now() at time zone 'utc')::date
                                         and m.status in ('sending','sent')),
                       'daily_cap', i.daily_cap) order by i.email)
              from cold_email.inboxes i
        ), '[]'::jsonb),
        'suppressed', (select count(*) from cold_email.suppression_list)
    );
$$;

create or replace function cold_email.ping()
returns text
language sql stable security definer set search_path = ''
as $$
    select 'ok'::text;
$$;

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
create or replace function cold_email.touch_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists enrollments_touch_updated_at on cold_email.enrollments;
create trigger enrollments_touch_updated_at
    before update on cold_email.enrollments
    for each row execute function cold_email.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Privileges
-- ---------------------------------------------------------------------------
-- Postgres lets everyone execute new functions by default; take that away.
-- Then: anon / authenticated (the publishable key and logged-in users) get
-- nothing at all; service_role (the secret key) may call the API functions
-- and touch no table directly. The Supabase roles are looked up first so the
-- file also runs on a plain Postgres (tests).
revoke all on all tables    in schema cold_email from public;
revoke all on all functions in schema cold_email from public;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on schema cold_email from %I', r);
            execute format('revoke all on all tables in schema cold_email from %I', r);
            execute format('revoke all on all functions in schema cold_email from %I', r);
        end if;
    end loop;

    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant usage on schema cold_email to service_role;
        grant execute on all functions in schema cold_email to service_role;
        -- Internal only.
        revoke execute on function cold_email._lead_details(uuid[]) from service_role;
        revoke execute on function cold_email.touch_updated_at() from service_role;
    end if;
end $$;

commit;

-- Tell the Supabase REST API to pick up the new functions. Harmless elsewhere.
notify pgrst, 'reload schema';
