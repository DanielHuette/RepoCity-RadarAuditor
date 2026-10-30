-- Changes done by hand: an approved change without an automatic way becomes a to-do for Daniel.
-- Context: the apply step stores such a change as `manual` with an error starting "Manuell umsetzen:"
-- (what to do); the app lists it under "Von Hand umsetzen" and Daniel ticks it off as `manual_done`.
-- Before this migration the apply step stored these changes as `failed` with the same error, so the
-- function accepts those rows too. | Input: migrations 001-007 (006 for optimization_changes). |
-- Output: enum values manual and manual_done, function mark_manual_done.
-- Every statement is guarded, so running the file twice changes nothing. The new enum values cannot be
-- used in the transaction that adds them, so the function is plpgsql (resolved at first call).

alter type public.optimization_change_status add value if not exists 'manual';
alter type public.optimization_change_status add value if not exists 'manual_done';

-- Ticks a to-do off (p_done true) or opens it again (false); applied_at holds the time it was done.
create or replace function public.mark_manual_done(p_change_id uuid, p_done boolean default true)
returns public.optimization_changes
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_change public.optimization_changes;
begin
  perform public.require_admin();
  if p_done is null then
    raise exception 'Erledigt oder offen fehlt.' using errcode = '22023';
  end if;

  update public.optimization_changes
  set status = case when p_done then 'manual_done' else 'manual' end::public.optimization_change_status,
      applied_at = case when p_done then now() end
  where id = p_change_id
    and (status::text in ('manual', 'manual_done')
         or (status = 'failed' and error like 'Manuell umsetzen:%'))
  returning * into v_change;
  if not found then
    if exists (select 1 from public.optimization_changes where id = p_change_id) then
      raise exception 'Diese Änderung ist keine Aufgabe von Hand.' using errcode = '55000';
    end if;
    raise exception 'Änderung nicht gefunden.' using errcode = 'P0002';
  end if;
  return v_change;
end $$;

revoke all on function public.mark_manual_done(uuid, boolean) from public, anon;
grant execute on function public.mark_manual_done(uuid, boolean) to authenticated, service_role;
