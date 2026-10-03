-- OUT-01 Cold Email Automation — send now & upcoming
--
-- upcoming_emails: what is scheduled next, so the dashboard can show when.
-- claim_next_emails: for "send now", claim each lead's NEXT unsent email
-- regardless of its due time (the caller skips the business-hours wait; all
-- other send checks still apply). Only one email per lead, and never to a
-- lead emailed in the last p_min_gap_hours, so pressing "send now" twice
-- can't stack follow-ups back to back.
-- Only adds functions in the cold_email schema; touches nothing in public.
-- Safe to re-run. Run after 0003_review.sql.

begin;

create or replace function cold_email.upcoming_emails(p_limit int default 10)
returns table (
    id          uuid,
    lead_id     uuid,
    email       text,
    step_number int,
    subject     text,
    due_at      timestamptz
)
language sql stable security definer set search_path = ''
as $$
    select m.id, m.lead_id, e.email, m.step_number, m.subject, m.due_at
      from cold_email.emails m
      join cold_email.enrollments e on e.lead_id = m.lead_id
     where m.status = 'pending'
       and e.status in ('sequence_ready','sending')
     order by m.due_at
     limit least(greatest(p_limit, 1), 100);
$$;

-- Claims (status -> 'sending') the lowest-numbered pending email of each
-- sendable lead, or of one lead when p_lead_id is given. Skips a lead with an
-- email being sent right now or sent within the last p_min_gap_hours.
drop function if exists cold_email.claim_next_emails(uuid, int);
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

-- Same privilege model as 0001: service_role only.
revoke all on function cold_email.upcoming_emails(int) from public;
revoke all on function cold_email.claim_next_emails(uuid, int, int) from public;

do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function cold_email.upcoming_emails(int) from %I', r);
            execute format('revoke all on function cold_email.claim_next_emails(uuid, int, int) from %I', r);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant execute on function cold_email.upcoming_emails(int) to service_role;
        grant execute on function cold_email.claim_next_emails(uuid, int, int) to service_role;
    end if;
end $$;

commit;

notify pgrst, 'reload schema';
