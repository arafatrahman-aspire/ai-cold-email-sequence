-- Email Nurture: what changes for the cold sequence
--
-- A lead is in only one sequence at a time. Three existing functions get
-- ONE extra line each (marked "-- NEW"); everything else in them is exactly
-- as in 0001_init.sql:
--
--   enroll_lead   refuses a lead who is in nurture (outcome 'in_nurture')
--   auto_enroll   skips leads who are in nurture
--   stats         the cold Overview does not list nurture's own mailbox
--                 (stored as an inactive inbox, so cold never sends from it)
--
-- _in_nurture(lead) comes from 0010.
--
-- Shared public tables are NOT changed. Safe to run more than once.
-- Run after 0010.

begin;

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
    elsif cold_email._in_nurture(v.id) then          -- NEW
        outcome := 'in_nurture';                     -- NEW
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
       and not cold_email._in_nurture(x.id)                                -- NEW
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
             where coalesce(i.config->>'purpose', '') <> 'nurture'           -- NEW
        ), '[]'::jsonb),
        'suppressed', (select count(*) from cold_email.suppression_list)
    );
$$;

-- Only the service role may call these functions (same rule as every file).
revoke all on all functions in schema cold_email from public;
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        revoke all on all functions in schema cold_email from anon;
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        revoke all on all functions in schema cold_email from authenticated;
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant execute on all functions in schema cold_email to service_role;
        revoke execute on function cold_email._lead_details(uuid[]) from service_role;
        revoke execute on function cold_email.touch_updated_at() from service_role;
    end if;
end $$;

commit;

notify pgrst, 'reload schema';
