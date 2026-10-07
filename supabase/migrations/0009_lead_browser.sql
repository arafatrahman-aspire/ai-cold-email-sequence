-- OUT-01 Enroll page — browse the shared leads, enroll or remove in one click
--
--   browse_leads          a page of public.leads with their cold-sequence state
--                         and, for leads not enrolled, why they cannot be
--   remove_from_sequence  take a lead out: unsent emails and replies are
--                         cancelled, history is kept, the lead can be
--                         enrolled again later
--
-- Reads public tables only; writes nothing outside the cold_email schema.
-- Safe to re-run. Run after 0008.

begin;

-- p_view: all | not_enrolled | available (not enrolled and can be) |
--         in_sequence | finished. "total" is the row count of the view.
create or replace function cold_email.browse_leads(
    p_search      text,
    p_view        text,
    p_source      text,
    p_contactable text[],
    p_skip_nurture boolean,
    p_limit       int default 50,
    p_offset      int default 0
)
returns table (
    id uuid, lead_ref text, first_name text, last_name text, email text,
    job_title text, company text, timezone text, source text, lead_status text,
    created_at timestamptz, enrollment_status text, enrollment_note text,
    enrolled_at timestamptz, emails_sent int, next_email_at timestamptz,
    blocked text, total bigint
)
language sql stable security definer set search_path = ''
as $$
    with base as (
        select l.id, l.lead_id as lead_ref,
               coalesce(l.first_name, lp.first_name) as first_name,
               coalesce(l.last_name, lp.last_name) as last_name,
               lower(trim(coalesce(nullif(trim(l.email), ''), lp.email, ''))) as email,
               coalesce(e.job_title, p.title) as job_title,
               coalesce(e.company, lp.company, cp.name, p.company_name) as company,
               coalesce(e.timezone, l.timezone) as timezone,
               l.source,
               coalesce(l.status, 'New') as lead_status,
               l.created_at,
               e.status as enrollment_status,
               e.last_error as enrollment_note,
               e.enrolled_at,
               exists (select 1 from public.email_nurture_state n
                        where n.lead_id = l.lead_id
                          and n.email_journey_started_at is not null) as in_nurture
          from public.leads l
          left join public.lead_profiles lp    on lp.lead_id = l.lead_id
          left join public.company_profiles cp on cp.id = lp.company_profile_id
          left join lateral (
                select pr.title, pr.company_name
                  from public.prospects pr
                 where pr.lead_id = l.lead_id
                 order by pr.updated_at desc nulls last
                 limit 1
          ) p on true
          left join cold_email.enrollments e on e.lead_id = l.id
         where (nullif(trim(p_source), '') is null or l.source = p_source)
           and (nullif(trim(p_search), '') is null
                or concat_ws(' ', l.lead_id, l.first_name, l.last_name, l.email, lp.email,
                             lp.first_name, lp.last_name, lp.company, cp.name,
                             p.company_name, p.title, e.job_title, e.company)
                   ilike '%' || trim(p_search) || '%')
    ), judged as (
        -- The same checks enroll_lead makes, so the button matches the outcome.
        select b.*,
               case when b.enrollment_status is not null then null
                    when b.email = '' then 'no_email'
                    when not (b.lead_status = any(p_contactable))
                        then 'not_contactable (' || b.lead_status || ')'
                    when p_skip_nurture and b.in_nurture then 'in_email_nurture'
                    when cold_email.is_suppressed(b.email) then 'suppressed'
                    when exists (select 1 from cold_email.enrollments e2
                                  where lower(e2.email) = b.email) then 'email_already_enrolled'
               end as blocked
          from base b
    ), viewed as (
        select j.* from judged j
         where case coalesce(p_view, 'all')
                   when 'not_enrolled' then j.enrollment_status is null
                   when 'available'    then j.enrollment_status is null and j.blocked is null
                   when 'in_sequence'  then j.enrollment_status in
                        ('ready_for_outreach','processing','sequence_ready','sending',
                         'manual_review','failed')
                   when 'finished'     then j.enrollment_status in
                        ('sent','replied','meeting_booked','snoozed','bounced',
                         'unsubscribed','stopped')
                   else true
               end
    )
    select v.id, v.lead_ref, v.first_name, v.last_name, nullif(v.email, ''),
           v.job_title, v.company, v.timezone, v.source, v.lead_status, v.created_at,
           v.enrollment_status, v.enrollment_note, v.enrolled_at,
           (select count(*) from cold_email.emails m
             where m.lead_id = v.id and m.status = 'sent')::int,
           (select min(m.due_at) from cold_email.emails m
             where m.lead_id = v.id and m.status in ('pending','sending')),
           v.blocked,
           count(*) over ()
      from viewed v
     order by v.created_at desc nulls last, v.id
     limit least(greatest(p_limit, 1), 200)
    offset greatest(p_offset, 0);
$$;

-- Outcome: removed | not_enrolled | busy (its sequence is being drafted right
-- now; try again in a moment) | not_active (already out of the sequence:
-- replied, booked, unsubscribed, bounced or stopped).
create or replace function cold_email.remove_from_sequence(p_lead_id uuid, p_reason text)
returns text
language plpgsql security definer set search_path = ''
as $$
declare
    v_status text;
begin
    -- Locks the row, so intake cannot claim it between the check and the update.
    select e.status into v_status from cold_email.enrollments e
     where e.lead_id = p_lead_id
       for update;
    if not found then
        return 'not_enrolled';
    end if;
    if v_status = 'processing' then
        return 'busy';
    end if;
    if v_status not in ('ready_for_outreach','sequence_ready','sending','sent',
                        'manual_review','failed','snoozed') then
        return 'not_active';
    end if;

    perform cold_email.cancel_sequence(p_lead_id, 'stopped', p_reason);
    update cold_email.enrollments set snoozed_until = null where lead_id = p_lead_id;
    update cold_email.reply_drafts
       set status = 'cancelled', last_error = left(p_reason, 500)
     where lead_id = p_lead_id and status in ('pending','approved');
    return 'removed';
end;
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
