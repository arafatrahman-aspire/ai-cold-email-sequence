-- Email Nurture
--
-- Three new tables (cold_email schema):
--   nurture_enrollments  one row per lead in nurture
--   nurture_messages     the 6 emails of each enrollment
--   nurture_events       the timeline: enrolled, clicks, track changes, hand-off, exit
--
-- Reused instead of new tables:
--   settings          nurture settings, step briefs, fallback emails, resources (JSON)
--   suppression_list  unsubscribes and bounces, respected by every module
--   inbox_events      replies; gets one column, nurture_enrollment_id
--   inboxes           nurture's own mailbox, stored as an INACTIVE row
--                     (the cold sender and poller only use active inboxes)
--   meetings          demos booked, for the nurture-to-demo rate
--
-- Shared public tables are only read (lead details and lead_scores). The one
-- write is leads.status = 'DNC' on unsubscribe, via mark_dnc (as Reply Triage).
--
-- Safe to run more than once. Run after 0009, then 0011.

begin;

-- ===========================================================================
-- Tables
-- ===========================================================================

create table if not exists cold_email.nurture_enrollments (
    id                uuid primary key default gen_random_uuid(),
    lead_id           uuid        not null,                -- public.leads.id
    email             text        not null,
    persona           text        not null,                -- ciso | it | hr
    temperature       text        not null,                -- warm | cold
    status            text        not null default 'active',
    step              int         not null default 0,      -- emails sent so far (0-6)
    started_at        timestamptz not null,                -- day 0 of the cadence
    next_send_at      timestamptz,
    needs_review      boolean     not null default false,
    note              text,                                -- why it is held or flagged
    exit_reason       text,
    test_mode         boolean     not null default false,
    handoff_summary   text,                                -- what sales was told
    sales_notified_at timestamptz,
    enrolled_at       timestamptz not null default now(),
    exited_at         timestamptz,
    constraint nurture_enrollments_persona_chk check (persona in ('ciso','it','hr')),
    constraint nurture_enrollments_temperature_chk check (temperature in ('warm','cold')),
    constraint nurture_enrollments_status_chk check (status in
        ('active','paused','held','handed_off','completed','exited')),
    constraint nurture_enrollments_step_chk check (step between 0 and 6)
);
-- A lead is in nurture at most once at a time.
create unique index if not exists nurture_enrollments_live_idx
    on cold_email.nurture_enrollments (lead_id) where status in ('active','paused','held');
create index if not exists nurture_enrollments_email_idx
    on cold_email.nurture_enrollments (lower(email));

create table if not exists cold_email.nurture_messages (
    id            uuid primary key default gen_random_uuid(),
    enrollment_id uuid        not null references cold_email.nurture_enrollments(id) on delete cascade,
    step          int         not null,
    -- writing -> needs_approval (pilot mode) -> ready -> sending -> sent
    -- or rejected / cancelled / failed
    status        text        not null default 'writing',
    send_at       timestamptz not null,
    subject       text,
    preheader     text,
    body          text,                     -- uses {{CTA_DEMO}} {{CTA_PRICING}} {{RESOURCE_LINK}}
    resource_id   text,                     -- an id from the resource list in settings
    source        text,                     -- ai | fallback | edited
    ai_log        jsonb       not null default '{}'::jsonb,  -- model, versions, checks, judge, tokens
    message_id    text,                     -- SMTP Message-ID, to match replies
    sent_at       timestamptz,
    error         text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    constraint nurture_messages_status_chk check (status in
        ('writing','needs_approval','ready','sending','sent','rejected','cancelled','failed')),
    constraint nurture_messages_step_chk check (step between 1 and 6)
);
-- One live email per enrollment and step.
create unique index if not exists nurture_messages_live_idx
    on cold_email.nurture_messages (enrollment_id, step)
    where status not in ('rejected','cancelled','failed');
create index if not exists nurture_messages_message_id_idx
    on cold_email.nurture_messages (message_id) where message_id is not null;

create table if not exists cold_email.nurture_events (
    id            uuid primary key default gen_random_uuid(),
    enrollment_id uuid        not null references cold_email.nurture_enrollments(id) on delete cascade,
    kind          text        not null,     -- enrolled, click, track, persona, paused, handoff, exit, ...
    detail        jsonb       not null default '{}'::jsonb,
    at            timestamptz not null default now()
);
create index if not exists nurture_events_enrollment_idx
    on cold_email.nurture_events (enrollment_id, at);

alter table cold_email.nurture_enrollments enable row level security;
alter table cold_email.nurture_messages    enable row level security;
alter table cold_email.nurture_events      enable row level security;

-- A reply to a nurture email belongs to the nurture enrollment (lead_id stays
-- empty, so Reply Triage's own queue never picks it up).
alter table cold_email.inbox_events add column if not exists nurture_enrollment_id uuid
    references cold_email.nurture_enrollments(id) on delete set null;

-- ===========================================================================
-- Leads: who may join
-- ===========================================================================

-- The lead as nurture needs it, read from the shared tables.
create or replace function cold_email.nurture_lead(p_lead_id uuid)
returns jsonb
language sql stable security definer set search_path = ''
as $$
    select jsonb_build_object(
        'lead_id', l.id,
        'email', lower(trim(coalesce(nullif(trim(l.email), ''), lp.email, ''))),
        'first_name', coalesce(l.first_name, lp.first_name),
        'last_name', coalesce(l.last_name, lp.last_name),
        'job_title', p.title,
        'company', coalesce(lp.company, cp.name, p.company_name),
        'timezone', l.timezone,
        'lead_status', coalesce(l.status, 'New'),
        'payment_status', l.payment_status,
        'tier', initcap(lower(coalesce(ls.tier, l.lead_score))))
      from public.leads l
      left join public.lead_profiles lp    on lp.lead_id = l.lead_id
      left join public.company_profiles cp on cp.id = lp.company_profile_id
      left join public.lead_scores ls      on ls.lead_id = l.lead_id
      left join lateral (select pr.title, pr.company_name from public.prospects pr
                          where pr.lead_id = l.lead_id
                          order by pr.updated_at desc nulls last limit 1) p on true
     where l.id = p_lead_id;
$$;

create or replace function cold_email._in_nurture(p_lead_id uuid)
returns boolean
language sql stable security definer set search_path = ''
as $$
    select exists (select 1 from cold_email.nurture_enrollments
                    where lead_id = p_lead_id and status in ('active','paused','held'));
$$;

-- Why a lead may not join nurture, or null when it may.
create or replace function cold_email.nurture_why_not(
    p_lead_id uuid, p_cooldown_days int, p_blocked_statuses text[], p_skip_external boolean
)
returns text
language plpgsql stable security definer set search_path = ''
as $$
declare
    v jsonb := cold_email.nurture_lead(p_lead_id);
begin
    if v is null then return 'not_found'; end if;
    if coalesce(v->>'email', '') = '' then return 'no_email'; end if;
    if cold_email.is_suppressed(v->>'email') then return 'suppressed'; end if;
    if (v->>'lead_status') = any(coalesce(p_blocked_statuses, '{}')) then
        return 'lead_status (' || (v->>'lead_status') || ')';
    end if;
    if lower(coalesce(v->>'payment_status', '')) in ('paid','succeeded','complete','completed') then
        return 'customer';
    end if;
    if cold_email._in_nurture(p_lead_id) then return 'in_nurture'; end if;
    -- One sequence at a time: in the cold sequence, or a cold reply still being answered.
    if exists (select 1 from cold_email.enrollments e where e.lead_id = p_lead_id
                  and e.status in ('ready_for_outreach','processing','sequence_ready','sending',
                                   'manual_review','failed','snoozed','meeting_booked'))
       or exists (select 1 from cold_email.reply_drafts d where d.lead_id = p_lead_id
                     and d.status in ('pending','approved')) then
        return 'in_cold_sequence';
    end if;
    if exists (select 1 from cold_email.nurture_enrollments n where n.lead_id = p_lead_id
                  and n.exited_at > now() - make_interval(days => greatest(p_cooldown_days, 0))) then
        return 'cooldown';
    end if;
    if p_skip_external and exists (select 1 from public.email_nurture_state s
                                     join public.leads l on l.lead_id = s.lead_id
                                    where l.id = p_lead_id and s.email_journey_started_at is not null) then
        return 'in_email_nurture';
    end if;
    return null;
end;
$$;

-- Enroll a lead if it may join. Returns {outcome, enrollment_id}.
create or replace function cold_email.nurture_enroll(
    p_lead_id uuid, p_persona text, p_temperature text, p_started_at timestamptz,
    p_needs_review boolean, p_note text, p_test_mode boolean, p_detail jsonb,
    p_cooldown_days int, p_blocked_statuses text[], p_skip_external boolean
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_reason text := cold_email.nurture_why_not(p_lead_id, p_cooldown_days, p_blocked_statuses, p_skip_external);
    v_id uuid;
begin
    if v_reason is not null then
        return jsonb_build_object('outcome', v_reason);
    end if;
    insert into cold_email.nurture_enrollments
        (lead_id, email, persona, temperature, started_at, next_send_at, needs_review, note, test_mode)
    values (p_lead_id, cold_email.nurture_lead(p_lead_id)->>'email', p_persona, p_temperature,
            p_started_at, p_started_at, coalesce(p_needs_review, false), left(p_note, 500),
            coalesce(p_test_mode, false))
    on conflict do nothing
    returning id into v_id;
    if v_id is null then
        return jsonb_build_object('outcome', 'in_nurture');
    end if;
    insert into cold_email.nurture_events (enrollment_id, kind, detail)
    values (v_id, 'enrolled', coalesce(p_detail, '{}'::jsonb)
                              || jsonb_build_object('persona', p_persona, 'temperature', p_temperature));
    return jsonb_build_object('outcome', 'enrolled', 'enrollment_id', v_id);
end;
$$;

-- Leads rescored since p_since, with their current tier (Hot | Warm | Cold).
create or replace function cold_email.nurture_score_changes(p_since timestamptz)
returns table (lead_id uuid, tier text)
language sql stable security definer set search_path = ''
as $$
    select l.id, initcap(lower(ls.tier))
      from public.lead_scores ls
      join public.leads l on l.lead_id = ls.lead_id
     where greatest(ls.updated_at, ls.scored_at) > p_since;
$$;

-- Warm/Cold leads that may join now (the "Enroll eligible" button).
create or replace function cold_email.nurture_candidates(
    p_cooldown_days int, p_blocked_statuses text[], p_skip_external boolean, p_limit int default 500
)
returns table (lead_id uuid, tier text)
language sql stable security definer set search_path = ''
as $$
    select x.id, x.tier
      from (select l.id, l.created_at, initcap(lower(coalesce(ls.tier, l.lead_score))) as tier
              from public.leads l left join public.lead_scores ls on ls.lead_id = l.lead_id) x
     where x.tier in ('Warm','Cold')
       and cold_email.nurture_why_not(x.id, p_cooldown_days, p_blocked_statuses, p_skip_external) is null
     order by x.created_at desc
     limit least(greatest(p_limit, 1), 2000);
$$;

-- Everyone in nurture right now, with their current score.
create or replace function cold_email.nurture_live()
returns table (id uuid, lead_id uuid, temperature text, status text, step int,
               started_at timestamptz, test_mode boolean, timezone text, tier text)
language sql stable security definer set search_path = ''
as $$
    select n.id, n.lead_id, n.temperature, n.status, n.step, n.started_at, n.test_mode,
           l.timezone, initcap(lower(coalesce(ls.tier, l.lead_score)))
      from cold_email.nurture_enrollments n
      left join public.leads l        on l.id = n.lead_id
      left join public.lead_scores ls on ls.lead_id = l.lead_id
     where n.status in ('active','paused','held');
$$;

-- ===========================================================================
-- Changing an enrollment
-- ===========================================================================

create or replace function cold_email.nurture_log(p_enrollment_id uuid, p_kind text, p_detail jsonb)
returns void
language sql security definer set search_path = ''
as $$
    insert into cold_email.nurture_events (enrollment_id, kind, detail)
    values (p_enrollment_id, p_kind, coalesce(p_detail, '{}'::jsonb));
$$;

-- Pause, resume, hold or end an enrollment.
-- p_status: active | paused | held | exited | completed.
create or replace function cold_email.nurture_set_status(
    p_id uuid, p_status text, p_reason text, p_next_send_at timestamptz
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
    v_ending boolean := p_status in ('exited','completed');
    v_next timestamptz;
begin
    update cold_email.nurture_enrollments
       set status       = p_status,
           note         = case when p_status in ('paused','held') then left(p_reason, 500)
                               when p_status = 'active' then null else note end,
           exit_reason  = case when v_ending then p_reason else exit_reason end,
           exited_at    = case when v_ending then now() else exited_at end,
           next_send_at = case when v_ending then null
                               when p_status = 'active' then coalesce(p_next_send_at, greatest(next_send_at, now()))
                               else next_send_at end
     where id = p_id and status in ('active','paused','held')
    returning next_send_at into v_next;
    if not found then
        return false;
    end if;
    if v_ending then
        update cold_email.nurture_messages set status = 'cancelled', updated_at = now()
         where enrollment_id = p_id and status in ('writing','needs_approval','ready');
    elsif p_status = 'active' then
        update cold_email.nurture_messages set send_at = greatest(send_at, v_next), updated_at = now()
         where enrollment_id = p_id and status in ('needs_approval','ready');
    end if;
    perform cold_email.nurture_log(p_id, p_status, jsonb_build_object('reason', p_reason));
    return true;
end;
$$;

-- Hand the lead to sales. The status update locks the row, so of two
-- simultaneous signals (Hot score and a pricing click) only one gets through.
create or replace function cold_email.nurture_handoff(p_id uuid, p_trigger text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
    update cold_email.nurture_enrollments
       set status = 'handed_off', exit_reason = p_trigger, exited_at = now(), next_send_at = null
     where id = p_id and status in ('active','paused','held');
    if not found then
        return false;
    end if;
    update cold_email.nurture_messages set status = 'cancelled', updated_at = now()
     where enrollment_id = p_id and status in ('writing','needs_approval','ready');
    perform cold_email.nurture_log(p_id, 'handoff', jsonb_build_object('trigger', p_trigger));
    return true;
end;
$$;

-- Change persona, Warm/Cold track, review flag or next send (null = unchanged).
-- A new persona or track drops the unsent next email so it is written again.
create or replace function cold_email.nurture_update(
    p_id uuid, p_persona text, p_temperature text, p_needs_review boolean, p_note text,
    p_next_send_at timestamptz
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
    v_old cold_email.nurture_enrollments%rowtype;
begin
    select * into v_old from cold_email.nurture_enrollments
     where id = p_id and status in ('active','paused','held') for update;
    if not found then
        return false;
    end if;
    update cold_email.nurture_enrollments
       set persona      = coalesce(p_persona, persona),
           temperature  = coalesce(p_temperature, temperature),
           needs_review = coalesce(p_needs_review, needs_review),
           note         = case when p_needs_review is null then note else left(p_note, 500) end,
           next_send_at = coalesce(p_next_send_at, next_send_at)
     where id = p_id;
    if coalesce(p_persona, v_old.persona) <> v_old.persona
       or coalesce(p_temperature, v_old.temperature) <> v_old.temperature then
        update cold_email.nurture_messages set status = 'cancelled', updated_at = now()
         where enrollment_id = p_id and status in ('writing','needs_approval','ready');
    elsif p_next_send_at is not null then
        update cold_email.nurture_messages set send_at = greatest(send_at, p_next_send_at), updated_at = now()
         where enrollment_id = p_id and status in ('needs_approval','ready');
    end if;
    return true;
end;
$$;

-- Unsubscribe or bounce: blocked for every module (and DNC for an
-- unsubscribe), and the enrollment ends.
create or replace function cold_email.nurture_suppress(p_id uuid, p_reason text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
    v cold_email.nurture_enrollments%rowtype;
begin
    select * into v from cold_email.nurture_enrollments where id = p_id;
    if not found then
        return false;
    end if;
    perform cold_email.suppress(v.email, case when p_reason = 'bounced' then 'hard_bounce' else p_reason end);
    if p_reason = 'unsubscribed' then
        perform cold_email.mark_dnc(v.lead_id);
    end if;
    perform cold_email.nurture_set_status(p_id, 'exited', p_reason, null);
    return true;
end;
$$;

-- ===========================================================================
-- Writing the emails
-- ===========================================================================

-- Start writing the next email of every active enrollment due before
-- p_horizon. Emails stuck in 'writing' for 15 minutes are handed out again.
create or replace function cold_email.nurture_claim_writing(p_horizon timestamptz, p_limit int)
returns table (message_id uuid)
language sql security definer set search_path = ''
as $$
    with due as (
        select e.id, e.step + 1 as step, e.next_send_at
          from cold_email.nurture_enrollments e
         where e.status = 'active' and e.step < 6 and e.next_send_at <= p_horizon
           and not exists (select 1 from cold_email.nurture_messages m
                            where m.enrollment_id = e.id and m.step = e.step + 1
                              and m.status not in ('rejected','cancelled','failed'))
         order by e.next_send_at
         limit least(greatest(p_limit, 1), 100)
           for update skip locked
    ), new_rows as (
        insert into cold_email.nurture_messages (enrollment_id, step, send_at)
        select id, step, next_send_at from due
        on conflict do nothing
        returning id
    ), stuck as (
        update cold_email.nurture_messages set updated_at = now()
         where status = 'writing' and updated_at < now() - interval '15 minutes'
        returning id
    )
    select id from new_rows union all select id from stuck;
$$;

-- What the writer needs: the email, the enrollment, the lead, what was sent
-- and clicked before, and how often a person rejected this step.
create or replace function cold_email.nurture_message_context(p_message_id uuid)
returns jsonb
language sql stable security definer set search_path = ''
as $$
    select jsonb_build_object(
        'message', jsonb_build_object('id', m.id, 'step', m.step, 'status', m.status, 'send_at', m.send_at),
        'enrollment', to_jsonb(e),
        'lead', cold_email.nurture_lead(e.lead_id),
        'history', coalesce((select jsonb_agg(jsonb_build_object('step', h.step, 'subject', h.subject,
                                     'resource_id', h.resource_id, 'body', h.body) order by h.step)
                               from cold_email.nurture_messages h
                              where h.enrollment_id = e.id and h.status = 'sent'), '[]'::jsonb),
        'clicks', coalesce((select jsonb_agg(v.detail order by v.at) from cold_email.nurture_events v
                             where v.enrollment_id = e.id and v.kind = 'click'
                               and not coalesce((v.detail->>'bot')::boolean, false)), '[]'::jsonb),
        'rejected', (select count(*) from cold_email.nurture_messages x
                      where x.enrollment_id = e.id and x.step = m.step and x.status = 'rejected'))
      from cold_email.nurture_messages m
      join cold_email.nurture_enrollments e on e.id = m.enrollment_id
     where m.id = p_message_id;
$$;

-- Store a written email (only if it is still being written: a hand-off or
-- track change in the meantime cancelled it). p_status: needs_approval | ready.
create or replace function cold_email.nurture_save_message(
    p_id uuid, p_status text, p_subject text, p_preheader text, p_body text,
    p_resource_id text, p_source text, p_ai_log jsonb
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
    update cold_email.nurture_messages
       set status = p_status, subject = p_subject, preheader = p_preheader, body = p_body,
           resource_id = p_resource_id, source = p_source, ai_log = coalesce(p_ai_log, '{}'::jsonb),
           updated_at = now()
     where id = p_id and status = 'writing' and p_status in ('needs_approval','ready');
    return found;
end;
$$;

-- ===========================================================================
-- Sending
-- ===========================================================================

-- Take the emails due now. Emails stuck in 'sending' for 15 minutes go back.
create or replace function cold_email.nurture_claim_sending(p_now timestamptz, p_limit int)
returns table (message_id uuid, enrollment_id uuid, step int, subject text, preheader text,
               body text, resource_id text, email text, first_name text, timezone text,
               temperature text, started_at timestamptz, test_mode boolean)
language sql security definer set search_path = ''
as $$
    with stuck as (
        update cold_email.nurture_messages set status = 'ready', updated_at = now()
         where status = 'sending' and updated_at < now() - interval '15 minutes'
        returning id
    ), due as (
        select m.id
          from cold_email.nurture_messages m
          join cold_email.nurture_enrollments e on e.id = m.enrollment_id
         where m.status = 'ready' and m.send_at <= p_now and e.status = 'active'
           and m.id not in (select id from stuck)
         order by m.send_at
         limit least(greatest(p_limit, 1), 100)
           for update of m skip locked
    ), taken as (
        update cold_email.nurture_messages m set status = 'sending', updated_at = now()
          from due where m.id = due.id
        returning m.*
    )
    select t.id, t.enrollment_id, t.step, t.subject, t.preheader, t.body, t.resource_id,
           e.email, coalesce(l.first_name, lp.first_name), l.timezone, e.temperature, e.started_at, e.test_mode
      from taken t
      join cold_email.nurture_enrollments e on e.id = t.enrollment_id
      left join public.leads l on l.id = e.lead_id
      left join public.lead_profiles lp on lp.lead_id = l.lead_id;
$$;

-- Last check before a send: is this lead still ours to email?
create or replace function cold_email.nurture_send_check(p_enrollment_id uuid)
returns jsonb
language sql stable security definer set search_path = ''
as $$
    select cold_email.nurture_lead(e.lead_id) || jsonb_build_object(
               'status', e.status,
               'suppressed', cold_email.is_suppressed(e.email),
               'in_cold_sequence', exists (select 1 from cold_email.enrollments c
                                            where c.lead_id = e.lead_id
                                              and c.status in ('ready_for_outreach','processing',
                                                  'sequence_ready','sending','manual_review','failed',
                                                  'snoozed','meeting_booked')))
      from cold_email.nurture_enrollments e
     where e.id = p_enrollment_id;
$$;

-- An email went out: the enrollment moves on (completed after the 6th).
create or replace function cold_email.nurture_mark_sent(
    p_message_id uuid, p_smtp_message_id text, p_next_send_at timestamptz
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
    v_enrollment uuid;
    v_step int;
begin
    update cold_email.nurture_messages
       set status = 'sent', sent_at = now(), message_id = p_smtp_message_id, error = null, updated_at = now()
     where id = p_message_id and status = 'sending'
    returning enrollment_id, step into v_enrollment, v_step;
    if v_enrollment is null then
        return false;
    end if;
    update cold_email.nurture_enrollments
       set step = greatest(step, v_step),
           next_send_at = case when v_step >= 6 then null else p_next_send_at end
     where id = v_enrollment;
    perform cold_email.nurture_log(v_enrollment, 'sent', jsonb_build_object('step', v_step));
    if v_step >= 6 then
        perform cold_email.nurture_set_status(v_enrollment, 'completed', 'completed', null);
    end if;
    return true;
end;
$$;

-- Put a claimed email back: 'ready' (send later, at p_send_at), 'cancelled'
-- (not to be sent) or 'failed'.
create or replace function cold_email.nurture_unsend(
    p_message_id uuid, p_status text, p_send_at timestamptz, p_error text
)
returns void
language sql security definer set search_path = ''
as $$
    update cold_email.nurture_messages
       set status = p_status, send_at = coalesce(p_send_at, send_at), error = left(p_error, 500),
           updated_at = now()
     where id = p_message_id and status = 'sending' and p_status in ('ready','cancelled','failed');
$$;

create or replace function cold_email.nurture_sent_since(p_since timestamptz)
returns int
language sql stable security definer set search_path = ''
as $$
    select count(*)::int from cold_email.nurture_messages where status = 'sent' and sent_at >= p_since;
$$;

-- A link in a sent email was clicked. A click within p_bot_seconds of the send
-- or from a known link scanner is marked as a bot (mail gateways open links).
create or replace function cold_email.nurture_click(
    p_message_id uuid, p_link text, p_user_agent text, p_bot_seconds int
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v record;
    v_bot boolean;
begin
    select m.enrollment_id, m.step, m.resource_id, m.sent_at, e.status into v
      from cold_email.nurture_messages m
      join cold_email.nurture_enrollments e on e.id = m.enrollment_id
     where m.id = p_message_id and m.status = 'sent';
    if not found then
        return null;
    end if;
    v_bot := now() < v.sent_at + make_interval(secs => greatest(p_bot_seconds, 0))
          or coalesce(p_user_agent, '') ~* '(bot|crawl|spider|preview|scan|safelinks|proofpoint|mimecast|barracuda|headless|python-requests|curl)';
    perform cold_email.nurture_log(v.enrollment_id, 'click', jsonb_build_object(
        'step', v.step, 'link', p_link, 'resource_id', v.resource_id, 'bot', v_bot));
    return jsonb_build_object('enrollment_id', v.enrollment_id, 'status', v.status, 'step', v.step,
                              'resource_id', v.resource_id, 'bot', v_bot);
end;
$$;

-- ===========================================================================
-- Replies (read by the poller, classified by Reply Triage)
-- ===========================================================================

-- Nurture's own mailbox as an INACTIVE inbox row, so its replies and bounces
-- can be stored as inbox_events. Never changes an existing (cold) inbox.
create or replace function cold_email.nurture_register_mailbox(p_email text, p_name text)
returns table (id uuid, active boolean, poll_cursor bigint)
language plpgsql security definer set search_path = ''
as $$
#variable_conflict use_column
begin
    insert into cold_email.inboxes (email, display_name, provider, credential_ref, active, config)
    values (lower(trim(p_email)), p_name, 'smtp', 'NURTURE', false, '{"purpose": "nurture"}'::jsonb)
    on conflict (email) do nothing;
    return query select i.id, i.active, i.poll_cursor from cold_email.inboxes i
                  where i.email = lower(trim(p_email));
end;
$$;

-- Which enrollment a reply belongs to: our Message-ID in its headers first,
-- then the sender's address against someone in nurture.
create or replace function cold_email.nurture_match_reply(p_message_ids text[], p_email text)
returns uuid
language sql stable security definer set search_path = ''
as $$
    select coalesce(
        (select m.enrollment_id from cold_email.nurture_messages m
          where m.message_id = any(coalesce(p_message_ids, '{}')) limit 1),
        (select n.id from cold_email.nurture_enrollments n
          where lower(n.email) = lower(coalesce(p_email, ''))
            and n.status in ('active','paused','held')
          order by n.enrolled_at desc limit 1));
$$;

create or replace function cold_email.nurture_save_reply(
    p_inbox_id uuid, p_enrollment_id uuid, p_event_type text, p_from_email text, p_to_email text,
    p_subject text, p_message_id text, p_in_reply_to text, p_snippet text, p_received_at timestamptz
)
returns boolean
language sql security definer set search_path = ''
as $$
    with ins as (
        insert into cold_email.inbox_events
            (inbox_id, nurture_enrollment_id, event_type, from_email, to_email, subject,
             message_id, in_reply_to, snippet, received_at, triage_status)
        values (p_inbox_id, p_enrollment_id, p_event_type, p_from_email, p_to_email, p_subject,
                p_message_id, p_in_reply_to, p_snippet, p_received_at,
                case when p_event_type in ('reply','auto_reply') then 'pending' else 'skipped' end)
        on conflict (inbox_id, message_id) do nothing
        returning 1
    )
    select exists (select 1 from ins);
$$;

-- Nurture replies waiting to be classified.
create or replace function cold_email.nurture_claim_replies(p_limit int default 10)
returns table (id uuid, nurture_enrollment_id uuid, event_type text, subject text, snippet text,
               received_at timestamptz, human_category text)
language sql security definer set search_path = ''
as $$
    with claimed as (
        select v.id from cold_email.inbox_events v
         where v.nurture_enrollment_id is not null and v.lead_id is null
           and (v.triage_status = 'pending'
                or (v.triage_status = 'processing' and v.locked_at < now() - interval '15 minutes'))
         order by v.received_at
         limit least(greatest(p_limit, 1), 50)
           for update skip locked
    )
    update cold_email.inbox_events v set triage_status = 'processing', locked_at = now()
      from claimed c where v.id = c.id
    returning v.id, v.nurture_enrollment_id, v.event_type, v.subject, v.snippet,
              v.received_at, v.human_category;
$$;

-- The last few messages before a reply, both directions, oldest first.
create or replace function cold_email.nurture_thread(
    p_enrollment_id uuid, p_before timestamptz, p_exclude_event uuid, p_limit int default 4
)
returns table (direction text, subject text, body text, at timestamptz)
language sql stable security definer set search_path = ''
as $$
    select x.direction, x.subject, x.body, x.at from (
        select 'out'::text as direction, m.subject, m.body, m.sent_at as at
          from cold_email.nurture_messages m
         where m.enrollment_id = p_enrollment_id and m.status = 'sent' and m.sent_at < p_before
        union all
        select 'in', v.subject, v.snippet, v.received_at
          from cold_email.inbox_events v
         where v.nurture_enrollment_id = p_enrollment_id and v.event_type = 'reply'
           and v.received_at < p_before and v.id is distinct from p_exclude_event
         order by 4 desc
         limit greatest(p_limit, 0)
    ) x order by x.at;
$$;

-- ===========================================================================
-- Hand-off notifications
-- ===========================================================================

-- Hand-offs of the last 3 days that sales has not been told about yet.
create or replace function cold_email.nurture_unnotified()
returns table (id uuid)
language sql stable security definer set search_path = ''
as $$
    select id from cold_email.nurture_enrollments
     where status = 'handed_off' and sales_notified_at is null and exited_at > now() - interval '3 days'
     order by exited_at limit 20;
$$;

create or replace function cold_email.nurture_notified(p_id uuid, p_summary text)
returns void
language sql security definer set search_path = ''
as $$
    update cold_email.nurture_enrollments
       set handoff_summary = p_summary, sales_notified_at = now()
     where id = p_id;
$$;

-- ===========================================================================
-- Console (the Email Nurture tab)
-- ===========================================================================

create or replace function cold_email.nurture_list(
    p_search text, p_status text, p_persona text, p_temperature text, p_review boolean,
    p_limit int default 50, p_offset int default 0
)
returns table (id uuid, lead_id uuid, email text, first_name text, last_name text, job_title text,
               company text, persona text, temperature text, status text, step int,
               next_send_at timestamptz, needs_review boolean, note text, exit_reason text,
               enrolled_at timestamptz, test_mode boolean, total bigint)
language sql stable security definer set search_path = ''
as $$
    select n.id, n.lead_id, n.email, l->>'first_name', l->>'last_name', l->>'job_title', l->>'company',
           n.persona, n.temperature, n.status, n.step, n.next_send_at, n.needs_review, n.note,
           n.exit_reason, n.enrolled_at, n.test_mode, count(*) over ()
      from cold_email.nurture_enrollments n
      cross join lateral cold_email.nurture_lead(n.lead_id) l
     where (nullif(trim(p_search), '') is null
            or concat_ws(' ', n.email, l->>'first_name', l->>'last_name', l->>'job_title', l->>'company')
               ilike '%' || trim(p_search) || '%')
       and (nullif(p_status, '') is null
            or (p_status = 'live' and n.status in ('active','paused','held')) or n.status = p_status)
       and (nullif(p_persona, '') is null or n.persona = p_persona)
       and (nullif(p_temperature, '') is null or n.temperature = p_temperature)
       and (p_review is null or n.needs_review = p_review)
     order by (n.status in ('active','paused','held')) desc, n.enrolled_at desc
     limit least(greatest(p_limit, 1), 200) offset greatest(p_offset, 0);
$$;

-- One lead's page: the enrollment, the lead, every email, event and reply.
create or replace function cold_email.nurture_detail(p_id uuid)
returns jsonb
language sql stable security definer set search_path = ''
as $$
    select jsonb_build_object(
        'enrollment', to_jsonb(e),
        'lead', cold_email.nurture_lead(e.lead_id),
        'messages', coalesce((select jsonb_agg(to_jsonb(m) order by m.step, m.created_at)
                                from cold_email.nurture_messages m where m.enrollment_id = e.id), '[]'::jsonb),
        'events', coalesce((select jsonb_agg(to_jsonb(v) order by v.at)
                              from cold_email.nurture_events v where v.enrollment_id = e.id), '[]'::jsonb),
        'replies', coalesce((select jsonb_agg(jsonb_build_object(
                                 'id', r.id, 'event_type', r.event_type, 'subject', r.subject,
                                 'snippet', r.snippet, 'received_at', r.received_at,
                                 'category', coalesce(r.human_category, r.category), 'summary', r.summary)
                               order by r.received_at)
                               from cold_email.inbox_events r where r.nurture_enrollment_id = e.id), '[]'::jsonb))
      from cold_email.nurture_enrollments e
     where e.id = p_id;
$$;

-- Emails waiting for approval, and the ones going out in the next p_hours.
create or replace function cold_email.nurture_review(p_hours int default 72)
returns table (id uuid, enrollment_id uuid, step int, status text, send_at timestamptz,
               subject text, preheader text, body text, source text, ai_log jsonb,
               email text, persona text, temperature text, first_name text, job_title text, company text)
language sql stable security definer set search_path = ''
as $$
    select m.id, m.enrollment_id, m.step, m.status, m.send_at, m.subject, m.preheader, m.body,
           m.source, m.ai_log, e.email, e.persona, e.temperature,
           l->>'first_name', l->>'job_title', l->>'company'
      from cold_email.nurture_messages m
      join cold_email.nurture_enrollments e on e.id = m.enrollment_id
      cross join lateral cold_email.nurture_lead(e.lead_id) l
     where e.status in ('active','paused','held')
       and (m.status = 'needs_approval'
            or (m.status = 'ready' and m.send_at <= now() + make_interval(hours => greatest(p_hours, 1))))
     order by (m.status = 'needs_approval') desc, m.send_at
     limit 300;
$$;

-- A person edits, approves or rejects an email before it is sent.
-- p_action: edit | approve | reject.
create or replace function cold_email.nurture_review_message(
    p_id uuid, p_action text, p_subject text, p_preheader text, p_body text
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
    if p_action = 'edit' then
        update cold_email.nurture_messages
           set subject = p_subject, preheader = p_preheader, body = p_body, source = 'edited', updated_at = now()
         where id = p_id and status in ('needs_approval','ready');
    elsif p_action = 'approve' then
        update cold_email.nurture_messages set status = 'ready', updated_at = now()
         where id = p_id and status = 'needs_approval';
    elsif p_action = 'reject' then
        update cold_email.nurture_messages set status = 'rejected', updated_at = now()
         where id = p_id and status in ('needs_approval','ready');
    else
        raise exception 'action must be edit, approve or reject';
    end if;
    return found;
end;
$$;

create or replace function cold_email.nurture_handoff_list(p_limit int default 100)
returns table (id uuid, email text, first_name text, last_name text, job_title text, company text,
               persona text, temperature text, step int, trigger text, handed_off_at timestamptz,
               summary text, sales_notified_at timestamptz)
language sql stable security definer set search_path = ''
as $$
    select n.id, n.email, l->>'first_name', l->>'last_name', l->>'job_title', l->>'company',
           n.persona, n.temperature, n.step, n.exit_reason, n.exited_at, n.handoff_summary,
           n.sales_notified_at
      from cold_email.nurture_enrollments n
      cross join lateral cold_email.nurture_lead(n.lead_id) l
     where n.status = 'handed_off'
     order by n.exited_at desc
     limit least(greatest(p_limit, 1), 500);
$$;

-- Dashboard numbers for enrollments (and sends) between p_from and p_to.
create or replace function cold_email.nurture_stats(p_from timestamptz, p_to timestamptz)
returns jsonb
language sql stable security definer set search_path = ''
as $$
    with enr as (
        select * from cold_email.nurture_enrollments where enrolled_at >= p_from and enrolled_at < p_to
    ), written as (
        select * from cold_email.nurture_messages
         where source is not null and created_at >= p_from and created_at < p_to
    ), demos as (
        select distinct e.id from enr e
          join cold_email.meetings mt
            on (mt.lead_id = e.lead_id or lower(mt.attendee_email) = lower(e.email))
           and mt.status in ('booked','pending')
           and mt.created_at between e.enrolled_at and e.enrolled_at + interval '45 days'
    )
    select jsonb_build_object(
        'kpis', jsonb_build_object(
            'enrolled', (select count(*) from enr),
            'active', (select count(*) from cold_email.nurture_enrollments where status in ('active','paused','held')),
            'needs_review', (select count(*) from cold_email.nurture_enrollments
                              where needs_review and status in ('active','paused','held')),
            'handed_off', (select count(*) from enr where status = 'handed_off'),
            'completed', (select count(*) from enr where status = 'completed'),
            'unsubscribed_bounced', (select count(*) from enr where exit_reason in ('unsubscribed','bounced')),
            'demos', (select count(*) from demos),
            'demo_rate', (select round(count(*)::numeric / nullif((select count(*) from enr), 0), 4) from demos)),
        'breakdown', coalesce((select jsonb_agg(b) from (
            select persona, temperature, count(*) as enrolled,
                   count(*) filter (where status in ('active','paused','held')) as live,
                   count(*) filter (where status = 'handed_off') as handed_off,
                   count(*) filter (where status = 'completed') as completed,
                   count(*) filter (where status = 'exited') as exited
              from enr group by persona, temperature) b), '[]'::jsonb),
        'steps', (select jsonb_agg(jsonb_build_object(
                     'step', g.step,
                     'sent', (select count(*) from cold_email.nurture_messages m
                               where m.step = g.step and m.status = 'sent' and m.sent_at >= p_from and m.sent_at < p_to),
                     'clicks', (select count(distinct v.enrollment_id) from cold_email.nurture_events v
                                 where v.kind = 'click' and (v.detail->>'step')::int = g.step
                                   and not coalesce((v.detail->>'bot')::boolean, false)
                                   and v.at >= p_from and v.at < p_to),
                     'unsubscribes', (select count(*) from cold_email.nurture_enrollments x
                                       where x.exit_reason = 'unsubscribed' and x.step = g.step
                                         and x.exited_at >= p_from and x.exited_at < p_to))
                     order by g.step)
                    from generate_series(1, 6) g(step)),
        'ai', jsonb_build_object(
            'written', (select count(*) from written),
            'fallbacks', (select count(*) from written where source = 'fallback'),
            'regenerated', (select count(*) from written where (ai_log->>'attempts')::int > 1),
            'avg_judge_score', (select round(avg((ai_log->>'judge_score')::numeric), 3) from written
                                 where ai_log ? 'judge_score' and ai_log->>'judge_score' is not null),
            'tokens_in', (select coalesce(sum((ai_log->>'tokens_in')::int), 0) from written),
            'tokens_out', (select coalesce(sum((ai_log->>'tokens_out')::int), 0) from written),
            'by_model', coalesce((select jsonb_object_agg(model, t) from (
                select coalesce(ai_log->>'model', 'none') as model,
                       jsonb_build_object('in', sum(coalesce((ai_log->>'tokens_in')::int, 0)),
                                          'out', sum(coalesce((ai_log->>'tokens_out')::int, 0))) as t
                  from written group by 1) mm), '{}'::jsonb)));
$$;

-- ===========================================================================
-- Privileges: only the service role may call these (same rule as every file)
-- ===========================================================================
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
