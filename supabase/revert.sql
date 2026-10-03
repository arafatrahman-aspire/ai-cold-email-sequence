-- OUT-01 Cold Email Automation — REVERT
--
-- Puts the database back to how it was before migrations/0001_init.sql (and
-- 0002_seed.sql) ran, by removing the `cold_email` schema and everything in
-- it. The migrations never touched `public`, so nothing there needs undoing;
-- other projects' tables and data are not affected.
--
-- ALL COLD-EMAIL DATA IS DELETED: enrollments, drafted and sent emails,
-- replies, the suppression list, inboxes and settings. Export anything you
-- want to keep first.
--
-- BEFORE RUNNING: Supabase dashboard > Project Settings > Data API >
-- Exposed schemas > remove `cold_email` > Save. If the API is still told to
-- expose a schema that no longer exists, it can fail to reload for every
-- project using this database.
--
-- Safety: nothing is dropped with CASCADE. If any object outside cold_email
-- depends on it (say, another project's view over cold_email.enrollments),
-- this stops with an error naming it and, being one transaction, removes
-- nothing. Running it when cold_email does not exist is a no-op.
--
-- Kept outside migrations/ on purpose, so it is never run by accident as
-- part of applying the migrations in order.

begin;

do $$
declare
    r record;
    v_tables text;
begin
    if to_regnamespace('cold_email') is null then
        raise notice 'schema cold_email does not exist; nothing to revert';
        return;
    end if;

    -- Triggers first: they use cold_email functions.
    for r in
        select t.tgname, format('%I.%I', n.nspname, c.relname) as tbl
          from pg_trigger t
          join pg_class c on c.oid = t.tgrelid
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'cold_email' and not t.tgisinternal
    loop
        execute format('drop trigger %I on %s', r.tgname, r.tbl);
    end loop;

    -- Then functions: several return cold_email table/row types.
    for r in
        select p.oid::regprocedure as fn
          from pg_proc p
         where p.pronamespace = 'cold_email'::regnamespace
    loop
        execute format('drop function %s', r.fn);
    end loop;

    -- All tables in one statement, so foreign keys between them are fine.
    -- Their indexes, constraints and triggers go with them.
    select string_agg(format('%I.%I', schemaname, tablename), ', ')
      into v_tables
      from pg_tables
     where schemaname = 'cold_email';
    if v_tables is not null then
        execute 'drop table ' || v_tables;
    end if;

    -- Remaining standalone types (cold_email.lead_detail).
    for r in
        select format('%I.%I', n.nspname, t.typname) as typ
          from pg_type t
          join pg_namespace n on n.oid = t.typnamespace
         where n.nspname = 'cold_email'
           and t.typtype = 'c'
           and not exists (select 1 from pg_class c
                            where c.oid = t.typrelid and c.relkind <> 'c')
    loop
        execute format('drop type %s', r.typ);
    end loop;

    -- RESTRICT: fails, rolling everything back, if anything is left inside.
    drop schema cold_email restrict;
    raise notice 'schema cold_email removed';
end $$;

commit;

-- Tell the Supabase REST API to refresh. Harmless elsewhere.
notify pgrst, 'reload schema';
