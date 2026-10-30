-- Roles: the administrator changes, everyone else who signs in only reads.
-- Context: the RadarAuditor app and the Agentic Marketing cockpit share this project; the scheduled
-- jobs and the worker use the service key and are not affected. | Input: none. |
-- Output: table app_admins, function is_admin(), read and write policies per table.

create table if not exists public.app_admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  added_at timestamptz not null default now()
);
alter table public.app_admins enable row level security;
drop policy if exists own_row on public.app_admins;
create policy own_row on public.app_admins for select to authenticated using (user_id = auth.uid());
revoke all on public.app_admins from anon, authenticated;
grant select on public.app_admins to authenticated;
grant all on public.app_admins to service_role;

-- Security definer, so the check works inside every policy without granting more than this answer.
create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select exists (select 1 from public.app_admins where user_id = auth.uid())
$$;
revoke all on function public.is_admin() from public;
grant execute on function public.is_admin() to authenticated, service_role;

do $$
declare t text;
begin
  foreach t in array array['customers', 'locations', 'contacts', 'competitors', 'keywords', 'audits',
                           'check_results', 'findings', 'rankings', 'jobs', 'payments', 'events', 'settings'] loop
    execute format('drop policy if exists authenticated_all on public.%I', t);
    execute format('drop policy if exists reader_select on public.%I', t);
    execute format('drop policy if exists admin_write on public.%I', t);
    execute format('create policy reader_select on public.%I for select to authenticated using (true)', t);
    execute format('create policy admin_write on public.%I for all to authenticated using (public.is_admin()) with check (public.is_admin())', t);
  end loop;
end $$;

-- Access data holds an encrypted WordPress password; readers do not see it at all.
drop policy if exists authenticated_all on public.customer_access;
drop policy if exists admin_all on public.customer_access;
create policy admin_all on public.customer_access for all to authenticated
  using (public.is_admin()) with check (public.is_admin());

drop policy if exists authenticated_insert on public.documents;
drop policy if exists admin_insert on public.documents;
create policy admin_insert on public.documents for insert to authenticated with check (public.is_admin());

insert into public.app_admins (user_id)
select id from auth.users where lower(email) = 'dhuette@gmx.net'
on conflict do nothing;
