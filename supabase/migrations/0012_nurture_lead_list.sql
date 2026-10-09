-- Email Nurture: every lead on the Leads tab
--
-- One function: the shared leads with their nurture state, so the console
-- can show all of them (not only those already in nurture) and enroll the
-- ones you tick. Reads only; changes no table. Safe to run more than once.
-- Run after 0010.

begin;

-- p_view: all | can_join | in_nurture | finished. "total" is the view's size.
create or replace function cold_email.nurture_browse(
    p_search text, p_view text, p_cooldown_days int, p_blocked_statuses text[], p_skip_external boolean,
    p_limit int default 50, p_offset int default 0
)
returns table (lead_id uuid, email text, first_name text, last_name text, job_title text, company text,
               tier text, lead_status text, enrollment_id uuid, status text, persona text,
               temperature text, step int, next_send_at timestamptz, needs_review boolean,
               exit_reason text, why_not text, total bigint)
language sql stable security definer set search_path = ''
as $$
    with leads as (
        select l.id, x, n.*
          from public.leads l
          cross join lateral cold_email.nurture_lead(l.id) x
          left join lateral (select e.id as enrollment_id, e.status, e.persona, e.temperature, e.step,
                                    e.next_send_at, e.needs_review, e.exit_reason
                               from cold_email.nurture_enrollments e
                              where e.lead_id = l.id
                              order by e.enrolled_at desc limit 1) n on true
         where nullif(trim(p_search), '') is null
            or concat_ws(' ', x->>'email', x->>'first_name', x->>'last_name', x->>'job_title', x->>'company')
               ilike '%' || trim(p_search) || '%'
    ), judged as (
        select d.*,
               case when d.status in ('active','paused','held') then null
                    when coalesce(d.x->>'tier', '') not in ('Warm','Cold')
                        then 'not_warm_or_cold (' || coalesce(d.x->>'tier', 'no score') || ')'
                    else cold_email.nurture_why_not(d.id, p_cooldown_days, p_blocked_statuses, p_skip_external)
               end as reason
          from leads d
    ), viewed as (
        select j.* from judged j
         where case coalesce(p_view, 'all')
                   when 'can_join'   then coalesce(j.status, '') not in ('active','paused','held') and j.reason is null
                   when 'in_nurture' then j.status in ('active','paused','held')
                   when 'finished'   then j.status in ('handed_off','completed','exited')
                   else true
               end
    )
    select v.id, v.x->>'email', v.x->>'first_name', v.x->>'last_name', v.x->>'job_title', v.x->>'company',
           v.x->>'tier', v.x->>'lead_status', v.enrollment_id, v.status, v.persona, v.temperature, v.step,
           v.next_send_at, v.needs_review, v.exit_reason, v.reason, count(*) over ()
      from viewed v
     order by (v.status in ('active','paused','held')) desc nulls last, v.x->>'first_name' nulls last, v.id
     limit least(greatest(p_limit, 1), 200) offset greatest(p_offset, 0);
$$;

revoke all on function cold_email.nurture_browse(text, text, int, text[], boolean, int, int) from public;
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        revoke all on function cold_email.nurture_browse(text, text, int, text[], boolean, int, int) from anon;
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        revoke all on function cold_email.nurture_browse(text, text, int, text[], boolean, int, int) from authenticated;
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant execute on function cold_email.nurture_browse(text, text, int, text[], boolean, int, int) to service_role;
    end if;
end $$;

commit;

notify pgrst, 'reload schema';
