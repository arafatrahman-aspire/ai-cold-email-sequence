-- OUT-01 / OUT-05 / Nurture -- 0018: deleting an enrollment deletes everything of that lead
--
-- Deleting a row in cold_email.enrollments (Supabase Table Editor, SQL, or
-- anywhere else) now also removes, in the same statement:
--
--   already did          emails, reply_drafts (and their draft_edits),
--                        campaign_leads
--   changed here         inbox_events and meetings of the lead (they used to
--                        be kept with lead_id set to null)
--   new (trigger)        the lead's nurture enrollments with their messages
--                        and events, replies to nurture emails, and the
--                        contacts row of a referred lead
--
-- NOT touched, on purpose:
--   * cold_email.suppression_list: an unsubscribed or bounced address must
--     stay blocked even after its lead is deleted.
--   * everything in the shared `public` schema (leads, scores, ...).
--   * inboxes, settings and campaigns themselves.
--
-- The app never deletes enrollments (removing a lead from the sequence only
-- sets its status to "stopped"), so this only changes what a manual delete
-- does. A delete cannot be undone: export what you need first.
--
-- Safe to run: one transaction (any error changes nothing), no data is
-- deleted or rewritten, and running it again does nothing. It waits at most
-- 10 seconds for table locks and then fails cleanly; run it again then.

begin;

set local lock_timeout = '10s';

-- ---------------------------------------------------------------------------
-- 1. Every foreign key to enrollments(lead_id) becomes "on delete cascade".
--    Found in the catalog rather than by name, so it also covers a key that
--    was created differently in your database. Keys that already cascade are
--    skipped.
-- ---------------------------------------------------------------------------
do $$
declare
    fk record;
begin
    for fk in
        select c.conrelid::regclass as tbl, c.conname, a.attname as col
          from pg_constraint c
          join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
         where c.contype = 'f'
           and c.confrelid = 'cold_email.enrollments'::regclass
           and array_length(c.conkey, 1) = 1
           and c.confdeltype <> 'c'
    loop
        execute format(
            'alter table %s drop constraint %I, add constraint %I foreign key (%I) '
            'references cold_email.enrollments(lead_id) on delete cascade',
            fk.tbl, fk.conname, fk.conname, fk.col);
        raise notice 'now cascades: %.% (%)', fk.tbl, fk.col, fk.conname;
    end loop;
end
$$;

-- ---------------------------------------------------------------------------
-- 2. What has no foreign key to enrollments: nurture (it is keyed by the same
--    lead_id but also exists for leads that were never in the cold sequence,
--    so it cannot have one) and the contacts row of a referred lead (its id
--    is the enrollment's lead_id). A trigger removes them.
--    Deleting the nurture enrollment cascades to its messages and events.
-- ---------------------------------------------------------------------------
create or replace function cold_email.enrollment_deleted()
returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
    -- Replies to this lead's nurture emails (their reply drafts cascade too).
    delete from cold_email.inbox_events ev
     where ev.nurture_enrollment_id in (
               select n.id from cold_email.nurture_enrollments n where n.lead_id = old.lead_id);
    delete from cold_email.nurture_enrollments where lead_id = old.lead_id;
    delete from cold_email.contacts where id = old.lead_id;
    return null;
end;
$$;

drop trigger if exists enrollment_deleted on cold_email.enrollments;
create trigger enrollment_deleted
    after delete on cold_email.enrollments
    for each row execute function cold_email.enrollment_deleted();

revoke all on function cold_email.enrollment_deleted() from public;

commit;

notify pgrst, 'reload schema';
