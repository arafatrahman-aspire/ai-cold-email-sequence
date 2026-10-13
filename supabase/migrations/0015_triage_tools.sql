-- Reply triage: act on a reply from its card, and learn from people's edits
--
--   draft_edits        NEW table: the AI's original text of each answer a
--                      person edited (the final text stays in reply_drafts)
--   edit_draft         edit an answer, keeping the AI's original the first time
--   draft_examples     recent answers people edited and then sent, per kind:
--                      the AI copies their style next time
--   inbox_event        one reply, for booking a time from its card
--   set_lead_timezone  correct a lead's timezone (and nothing else)
--
-- Adds one table and four functions; changes no existing table.
-- Safe to run more than once. Run after 0006.

begin;

create table if not exists cold_email.draft_edits (
    draft_id    uuid primary key references cold_email.reply_drafts(id) on delete cascade,
    kind        text        not null,
    ai_subject  text        not null,
    ai_body     text        not null,
    edited_by   text,
    edited_at   timestamptz not null default now()
);
create index if not exists draft_edits_kind_idx on cold_email.draft_edits (kind, edited_at desc);
alter table cold_email.draft_edits enable row level security;

-- Same rule as update_draft (only waiting or failed answers), plus the AI's
-- original is kept on the first edit.
create or replace function cold_email.edit_draft(p_id uuid, p_subject text, p_body text, p_user text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
    v_old cold_email.reply_drafts%rowtype;
begin
    select * into v_old from cold_email.reply_drafts
     where id = p_id and status in ('pending','failed')
     for update;
    if not found then
        return false;
    end if;
    insert into cold_email.draft_edits (draft_id, kind, ai_subject, ai_body, edited_by)
    values (p_id, v_old.kind, v_old.subject, v_old.body, left(p_user, 200))
    on conflict (draft_id) do update set edited_by = excluded.edited_by, edited_at = now();
    update cold_email.reply_drafts set subject = p_subject, body = p_body where id = p_id;
    return true;
end;
$$;

create or replace function cold_email.draft_examples(p_kind text, p_limit int default 3)
returns table (subject text, body text)
language sql stable security definer set search_path = ''
as $$
    select d.subject, d.body
      from cold_email.draft_edits de
      join cold_email.reply_drafts d on d.id = de.draft_id
     where de.kind = p_kind and d.status = 'sent'
       and (d.body <> de.ai_body or d.subject <> de.ai_subject)
     order by d.sent_at desc nulls last
     limit least(greatest(p_limit, 0), 5);
$$;

create or replace function cold_email.inbox_event(p_id uuid)
returns table (id uuid, inbox_id uuid, lead_id uuid, event_type text, from_email text, subject text,
               snippet text, message_id text, in_reply_to text, received_at timestamptz,
               extracted jsonb, category text)
language sql stable security definer set search_path = ''
as $$
    select ev.id, ev.inbox_id, ev.lead_id, ev.event_type, ev.from_email, ev.subject, ev.snippet,
           ev.message_id, ev.in_reply_to, ev.received_at, ev.extracted,
           coalesce(ev.human_category, ev.category)
      from cold_email.inbox_events ev
     where ev.id = p_id;
$$;

create or replace function cold_email.set_lead_timezone(p_lead_id uuid, p_timezone text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
    update cold_email.enrollments
       set timezone = nullif(trim(p_timezone), '')
     where lead_id = p_lead_id;
    return found;
end;
$$;

do $$
declare
    f text;
    r text;
begin
    -- The new table is reached only through the functions, like every other.
    revoke all on cold_email.draft_edits from public;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on cold_email.draft_edits from %I', r);
        end if;
    end loop;
    foreach f in array array[
        'cold_email.edit_draft(uuid, text, text, text)',
        'cold_email.draft_examples(text, int)',
        'cold_email.inbox_event(uuid)',
        'cold_email.set_lead_timezone(uuid, text)'
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
