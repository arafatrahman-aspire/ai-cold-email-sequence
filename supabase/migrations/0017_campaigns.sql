-- Cold email campaigns: an audience, a playbook, start/stop, results
--
--   campaigns       NEW table: one row per campaign (audience filter, playbook,
--                   status draft | active | paused | ended)
--   campaign_leads  NEW table: which campaign a cold-sequence lead belongs to.
--                   A lead with no row is in "General", which writes and sends
--                   exactly as before campaigns existed.
--
--   campaign_save / campaign_set_status / campaign_get / campaign_list
--   campaign_audience  shared leads that match a campaign and are not in any
--                      cold sequence yet
--   campaign_enroll    enroll one lead into a campaign, in one transaction (the
--                      drafting job never sees it without its campaign)
--   campaign_for_lead  the lead's campaign and playbook (drafting and sending)
--   campaign_results   per campaign: leads, emails, replies, interest, meetings
--
-- Adds two tables and functions; changes no existing table. Two sending
-- functions (claim_due_steps, claim_next_emails) get one line each, marked
-- -- NEW, so a paused campaign's emails wait without blocking anyone else.
-- Safe to run more than once. Run after 0011.

begin;

create table if not exists cold_email.campaigns (
    id          uuid primary key default gen_random_uuid(),
    name        text        not null,
    status      text        not null default 'draft',
    audience    jsonb       not null default '{}'::jsonb,  -- titles, industries, countries, sources, tiers
    playbook    jsonb       not null default '{}'::jsonb,  -- angle, value_points, proof, offer, cta, tone, banned_claims
    auto_add    boolean     not null default false,        -- add new matching leads while active
    created_by  text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    started_at  timestamptz,
    ended_at    timestamptz,
    constraint campaigns_status_chk check (status in ('draft','active','paused','ended')),
    constraint campaigns_name_chk check (length(trim(name)) between 1 and 120)
);
create unique index if not exists campaigns_name_idx on cold_email.campaigns (lower(name));

create table if not exists cold_email.campaign_leads (
    lead_id     uuid primary key references cold_email.enrollments(lead_id) on delete cascade,
    campaign_id uuid        not null references cold_email.campaigns(id) on delete cascade,
    added_at    timestamptz not null default now()
);
create index if not exists campaign_leads_campaign_idx on cold_email.campaign_leads (campaign_id);

alter table cold_email.campaigns      enable row level security;
alter table cold_email.campaign_leads enable row level security;

-- Insert (p_id null) or update. Ended campaigns are read-only.
create or replace function cold_email.campaign_save(
    p_id uuid, p_name text, p_audience jsonb, p_playbook jsonb, p_auto_add boolean, p_user text
)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
    v_id uuid;
begin
    if p_id is null then
        insert into cold_email.campaigns (name, audience, playbook, auto_add, created_by)
        values (trim(p_name), coalesce(p_audience, '{}'), coalesce(p_playbook, '{}'),
                coalesce(p_auto_add, false), left(p_user, 200))
        returning id into v_id;
    else
        update cold_email.campaigns
           set name = trim(p_name), audience = coalesce(p_audience, '{}'),
               playbook = coalesce(p_playbook, '{}'), auto_add = coalesce(p_auto_add, false),
               updated_at = now()
         where id = p_id and status <> 'ended'
        returning id into v_id;
    end if;
    return v_id;
end;
$$;

-- draft -> active (start), active <-> paused, any -> ended (stop for good).
-- Ending stops the campaign's sequences: what is unsent is cancelled; what was
-- sent stays. Returns how many sequences were stopped (0 for other changes),
-- or -1 if the change is not allowed.
create or replace function cold_email.campaign_set_status(p_id uuid, p_status text)
returns int
language plpgsql security definer set search_path = ''
as $$
declare
    v_old text;
    v_name text;
    v_stopped int := 0;
    r record;
begin
    select status, name into v_old, v_name from cold_email.campaigns where id = p_id for update;
    if not found or v_old = 'ended' or p_status not in ('active','paused','ended')
       or (p_status = 'paused' and v_old <> 'active') then
        return -1;
    end if;
    update cold_email.campaigns
       set status = p_status, updated_at = now(),
           started_at = case when p_status = 'active' then coalesce(started_at, now()) else started_at end,
           ended_at = case when p_status = 'ended' then now() else ended_at end
     where id = p_id;
    if p_status = 'active' then
        -- Emails held while the campaign was not running: move each lead's
        -- waiting emails so the first is due now, keeping their spacing (the
        -- sender then fits them into business hours).
        update cold_email.emails m
           set due_at = m.due_at + s.shift
          from (select x.lead_id, now() - min(x.due_at) as shift
                  from cold_email.emails x
                  join cold_email.campaign_leads cl on cl.lead_id = x.lead_id and cl.campaign_id = p_id
                 where x.status = 'pending'
                 group by x.lead_id
                having min(x.due_at) < now()) s
         where m.lead_id = s.lead_id and m.status = 'pending';
    end if;
    if p_status = 'ended' then
        for r in select cl.lead_id from cold_email.campaign_leads cl where cl.campaign_id = p_id loop
            if cold_email.remove_from_sequence(r.lead_id, 'campaign "' || v_name || '" ended') = 'removed' then
                v_stopped := v_stopped + 1;
            end if;
        end loop;
    end if;
    return v_stopped;
end;
$$;

create or replace function cold_email.campaign_get(p_id uuid)
returns jsonb
language sql stable security definer set search_path = ''
as $$
    select to_jsonb(c) from cold_email.campaigns c where c.id = p_id;
$$;

create or replace function cold_email.campaign_list()
returns setof cold_email.campaigns
language sql stable security definer set search_path = ''
as $$
    select * from cold_email.campaigns
     order by (status = 'ended'), created_at desc;
$$;

-- p_audience: {"titles": [...], "industries": [...], "countries": [...],
-- "sources": [...], "tiers": [...]}. Each list is "any of these" (titles,
-- industries and countries match as text contained, case-insensitive); an
-- empty or missing list does not filter. Leads already in a cold sequence,
-- without an email, or with a status not contactable are left out; enrolling
-- still applies every rule (nurture, suppression) and reports the reason.
create or replace function cold_email.campaign_audience(
    p_audience jsonb, p_contactable text[], p_limit int default 50, p_offset int default 0
)
returns table (id uuid, lead_ref text, first_name text, last_name text, email text, job_title text,
               company text, industry text, country text, source text, tier text, total bigint)
language sql stable security definer set search_path = ''
as $$
    with f as (
        select coalesce(array(select jsonb_array_elements_text(p_audience->'titles')), '{}')     as titles,
               coalesce(array(select jsonb_array_elements_text(p_audience->'industries')), '{}') as industries,
               coalesce(array(select jsonb_array_elements_text(p_audience->'countries')), '{}')  as countries,
               coalesce(array(select jsonb_array_elements_text(p_audience->'sources')), '{}')    as sources,
               coalesce(array(select initcap(lower(t)) from jsonb_array_elements_text(p_audience->'tiers') t), '{}') as tiers
    ), leads as (
        select l.id, l.lead_id as lead_ref, coalesce(l.first_name, lp.first_name) as first_name,
               coalesce(l.last_name, lp.last_name) as last_name,
               lower(trim(coalesce(nullif(trim(l.email), ''), lp.email))) as email,
               p.title as job_title, coalesce(lp.company, cp.name, p.company_name) as company,
               coalesce(p.industry, cp.industry) as industry, p.country, l.source,
               initcap(lower(coalesce(ls.tier, l.lead_score))) as tier, l.created_at,
               coalesce(l.status, 'New') as lead_status
          from public.leads l
          left join public.lead_profiles lp    on lp.lead_id = l.lead_id
          left join public.company_profiles cp on cp.id = lp.company_profile_id
          left join public.lead_scores ls      on ls.lead_id = l.lead_id
          left join lateral (select pr.title, pr.company_name, pr.industry, pr.country
                               from public.prospects pr where pr.lead_id = l.lead_id
                              order by pr.updated_at desc nulls last limit 1) p on true
    )
    select d.id, d.lead_ref, d.first_name, d.last_name, d.email, d.job_title, d.company, d.industry,
           d.country, d.source, d.tier, count(*) over ()
      from leads d, f
     where coalesce(d.email, '') <> ''
       and d.lead_status = any(p_contactable)
       and not exists (select 1 from cold_email.enrollments e
                        where e.lead_id = d.id or lower(e.email) = d.email)
       and (cardinality(f.titles) = 0
            or exists (select 1 from unnest(f.titles) t where d.job_title ilike '%' || t || '%'))
       and (cardinality(f.industries) = 0
            or exists (select 1 from unnest(f.industries) t where d.industry ilike '%' || t || '%'))
       and (cardinality(f.countries) = 0
            or exists (select 1 from unnest(f.countries) t where d.country ilike '%' || t || '%'))
       and (cardinality(f.sources) = 0 or d.source = any(f.sources))
       and (cardinality(f.tiers) = 0 or d.tier = any(f.tiers))
     order by d.created_at desc nulls last, d.id
     limit least(greatest(p_limit, 1), 500) offset greatest(p_offset, 0);
$$;

-- Same outcomes as enroll_lead, plus campaign_not_open (ended or unknown).
create or replace function cold_email.campaign_enroll(
    p_campaign_id uuid, p_ref text, p_contactable text[], p_skip_nurture boolean
)
returns table (outcome text, lead_id uuid)
language plpgsql security definer set search_path = ''
as $$
#variable_conflict use_column
declare
    v record;
begin
    perform 1 from cold_email.campaigns c where c.id = p_campaign_id and c.status <> 'ended';
    if not found then
        outcome := 'campaign_not_open';
        return next;
        return;
    end if;
    select * into v from cold_email.enroll_lead(p_ref, p_contactable, p_skip_nurture, null, null, 'campaign', null);
    outcome := v.outcome;
    lead_id := v.lead_id;
    if v.outcome = 'enrolled' then
        insert into cold_email.campaign_leads (lead_id, campaign_id) values (v.lead_id, p_campaign_id)
        on conflict do nothing;
    end if;
    return next;
end;
$$;

create or replace function cold_email.campaign_for_lead(p_lead_id uuid)
returns jsonb
language sql stable security definer set search_path = ''
as $$
    select jsonb_build_object('id', c.id, 'name', c.name, 'status', c.status, 'playbook', c.playbook)
      from cold_email.campaign_leads cl
      join cold_email.campaigns c on c.id = cl.campaign_id
     where cl.lead_id = p_lead_id;
$$;

-- One row per campaign, and one with campaign_id null for General.
create or replace function cold_email.campaign_results()
returns table (campaign_id uuid, leads bigint, in_sequence bigint, emails_sent bigint,
               replied bigint, interested bigint, meetings bigint, bounced bigint, unsubscribed bigint)
language sql stable security definer set search_path = ''
as $$
    with e as (
        select en.lead_id, en.status, cl.campaign_id
          from cold_email.enrollments en
          left join cold_email.campaign_leads cl on cl.lead_id = en.lead_id
    )
    select e.campaign_id,
           count(*),
           count(*) filter (where e.status in ('ready_for_outreach','processing','sequence_ready','sending')),
           coalesce(sum((select count(*) from cold_email.emails m
                          where m.lead_id = e.lead_id and m.status = 'sent')), 0),
           count(*) filter (where exists (select 1 from cold_email.inbox_events ev
                                           where ev.lead_id = e.lead_id and ev.event_type = 'reply')),
           count(*) filter (where exists (select 1 from cold_email.inbox_events ev
                                           where ev.lead_id = e.lead_id and ev.event_type = 'reply'
                                             and coalesce(ev.human_category, ev.category) = 'interested')),
           count(*) filter (where exists (select 1 from cold_email.meetings mt
                                           where mt.lead_id = e.lead_id and mt.status <> 'cancelled')),
           count(*) filter (where e.status = 'bounced'),
           count(*) filter (where e.status = 'unsubscribed')
      from e
     group by e.campaign_id;
$$;

-- ---------------------------------------------------------------------------
-- Sending: a campaign that is not started or is paused holds its leads'
-- emails. The two claim functions are the 0001 / 0004 versions with one line
-- added, marked -- NEW; leads in no campaign (General) are unaffected.
-- ---------------------------------------------------------------------------
create or replace function cold_email._campaign_holds(p_lead_id uuid)
returns boolean
language sql stable security definer set search_path = ''
as $$
    select exists (select 1 from cold_email.campaign_leads cl
                     join cold_email.campaigns c on c.id = cl.campaign_id
                    where cl.lead_id = p_lead_id and c.status in ('draft','paused'));
$$;

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
           and not cold_email._campaign_holds(m.lead_id)          -- NEW
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

create or replace function cold_email.claim_next_emails(
    p_lead_id       uuid default null,
    p_limit         int  default 25,
    p_min_gap_hours int  default 24
)
returns table (id uuid, lead_id uuid, step_number int,
               subject text, body text, attempts int)
language sql security definer set search_path = ''
as $$
    with next_per_lead as (
        select distinct on (m.lead_id) m.id
          from cold_email.emails m
          join cold_email.enrollments e on e.lead_id = m.lead_id
         where m.status = 'pending'
           and e.status in ('sequence_ready','sending')
           and (p_lead_id is null or m.lead_id = p_lead_id)
           and not cold_email.is_suppressed(e.email)
           and not cold_email._campaign_holds(m.lead_id)          -- NEW
           and not exists (
                 select 1 from cold_email.emails s
                  where s.lead_id = m.lead_id
                    and (s.status = 'sending'
                         or s.sent_at > now() - make_interval(hours => p_min_gap_hours)))
         order by m.lead_id, m.step_number
    ), claimed as (
        -- Row locks can't be taken together with DISTINCT ON, hence two steps.
        select m.id
          from cold_email.emails m
          join next_per_lead n on n.id = m.id
         where m.status = 'pending'
         order by m.due_at
         limit least(greatest(p_limit, 1), 100)
         for update of m skip locked
    )
    update cold_email.emails em
       set status = 'sending', locked_at = now(), attempts = em.attempts + 1
      from claimed c
     where em.id = c.id
    returning em.id, em.lead_id, em.step_number, em.subject, em.body, em.attempts;
$$;

do $$
declare
    f text;
    r text;
begin
    revoke all on cold_email.campaigns, cold_email.campaign_leads from public;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on cold_email.campaigns, cold_email.campaign_leads from %I', r);
        end if;
    end loop;
    foreach f in array array[
        'cold_email.campaign_save(uuid, text, jsonb, jsonb, boolean, text)',
        'cold_email.campaign_set_status(uuid, text)',
        'cold_email.campaign_get(uuid)',
        'cold_email.campaign_list()',
        'cold_email.campaign_audience(jsonb, text[], int, int)',
        'cold_email.campaign_enroll(uuid, text, text[], boolean)',
        'cold_email.campaign_for_lead(uuid)',
        'cold_email.campaign_results()',
        'cold_email._campaign_holds(uuid)',
        'cold_email.claim_due_steps(int)',
        'cold_email.claim_next_emails(uuid, int, int)'
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
