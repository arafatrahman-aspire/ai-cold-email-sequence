-- OUT-01 Cold Email Automation — review & retry
--
-- Lets the service list enrollments (e.g. the ones in manual_review with the
-- reason) and send one back to intake for another drafting attempt.
-- Only adds functions in the cold_email schema; touches nothing in public.
-- Safe to re-run. Run after 0001_init.sql.

begin;

-- Enrollments, newest activity first, optionally filtered by status.
create or replace function cold_email.list_enrollments(
    p_statuses text[] default null,
    p_limit    int    default 100
)
returns table (
    lead_id          uuid,
    email            text,
    status           text,
    persona          text,
    job_title        text,
    company          text,
    timezone         text,
    last_error       text,
    sequence_version int,
    enrolled_at      timestamptz,
    updated_at       timestamptz
)
language sql stable security definer set search_path = ''
as $$
    select e.lead_id, e.email, e.status, e.persona, e.job_title, e.company,
           e.timezone, e.last_error, e.sequence_version, e.enrolled_at, e.updated_at
      from cold_email.enrollments e
     where p_statuses is null or e.status = any(p_statuses)
     order by e.updated_at desc
     limit least(greatest(p_limit, 1), 500);
$$;

-- Send a stuck enrollment back to intake so its sequence is drafted again.
-- Optional overrides replace the enrollment's job title / company / timezone
-- (null leaves them as they are). Only manual_review, failed and stopped
-- enrollments can be retried; returns false otherwise.
create or replace function cold_email.retry_enrollment(
    p_lead_id   uuid,
    p_job_title text default null,
    p_company   text default null,
    p_timezone  text default null
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
    update cold_email.enrollments
       set status     = 'ready_for_outreach',
           last_error = null,
           job_title  = coalesce(nullif(trim(p_job_title), ''), job_title),
           company    = coalesce(nullif(trim(p_company), ''), company),
           timezone   = coalesce(nullif(trim(p_timezone), ''), timezone)
     where lead_id = p_lead_id
       and status in ('manual_review', 'failed', 'stopped');
    return found;
end;
$$;

-- Same privilege model as 0001: service_role only.
revoke all on function cold_email.list_enrollments(text[], int) from public;
revoke all on function cold_email.retry_enrollment(uuid, text, text, text) from public;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function cold_email.list_enrollments(text[], int) from %I', r);
            execute format('revoke all on function cold_email.retry_enrollment(uuid, text, text, text) from %I', r);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant execute on function cold_email.list_enrollments(text[], int) to service_role;
        grant execute on function cold_email.retry_enrollment(uuid, text, text, text) to service_role;
    end if;
end $$;

commit;

notify pgrst, 'reload schema';
