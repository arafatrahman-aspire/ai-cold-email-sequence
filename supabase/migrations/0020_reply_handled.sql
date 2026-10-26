-- OUT-05 -- 0020: a reply that needed a person stops needing one once handled
--
-- A reply the AI was unsure about (or that failed) is marked "needs_human".
-- Once a person deals with it -- approves or sends its answer, or presses
-- "Mark as handled" -- it is done. Before this, "Needs you" stayed on for good.
--
-- Safe to run: one transaction, no data changed, running it again does nothing.

begin;

-- By the reply's id, or by one of its answers (p_draft_id). Returns whether
-- anything changed.
create or replace function cold_email.mark_event_handled(p_event_id uuid default null, p_draft_id uuid default null)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
    v_event uuid := p_event_id;
begin
    if v_event is null and p_draft_id is not null then
        select d.event_id into v_event from cold_email.reply_drafts d where d.id = p_draft_id;
    end if;
    update cold_email.inbox_events
       set triage_status = 'done', triage_error = null, locked_at = null
     where id = v_event and triage_status in ('needs_human', 'failed');
    return found;
end;
$$;

do $$
begin
    revoke all on function cold_email.mark_event_handled(uuid, uuid) from public;
    if exists (select 1 from pg_roles where rolname = 'anon') then
        revoke all on function cold_email.mark_event_handled(uuid, uuid) from anon;
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        revoke all on function cold_email.mark_event_handled(uuid, uuid) from authenticated;
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant execute on function cold_email.mark_event_handled(uuid, uuid) to service_role;
    end if;
end $$;

commit;

notify pgrst, 'reload schema';
