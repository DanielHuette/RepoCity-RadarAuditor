-- The part of the CRM schema that the RadarAuditor migrations use, with the CRM's grants and policies.
-- Context: the CRM migrations live in their own repository and run in the shared project between
-- RadarAuditor migrations 005 and 006; migration tests need their tables, not their logic. | Input:
-- migrations 001-005 (public.is_admin). | Output: schema crm with customers, products and orders;
-- pg_cron's job table with schedule and unschedule, which CRM migration 003 brings in the project.

create schema if not exists crm;

create schema if not exists cron;
create table if not exists cron.job (
  jobid bigserial primary key,
  jobname text unique,
  schedule text not null,
  command text not null
);
create or replace function cron.schedule(p_name text, p_schedule text, p_command text) returns bigint
language sql as $$
  insert into cron.job (jobname, schedule, command) values (p_name, p_schedule, p_command)
  on conflict (jobname) do update set schedule = excluded.schedule, command = excluded.command
  returning jobid
$$;
create or replace function cron.unschedule(p_job bigint) returns boolean
language sql as $$ delete from cron.job where jobid = p_job returning true $$;

create table if not exists crm.customers (
  id uuid primary key default gen_random_uuid(),
  kind text not null default 'company' check (kind in ('company', 'person')),
  name text not null check (length(btrim(name)) between 1 and 200),
  contact_person text not null default '',
  email text,
  phone text not null default '',
  street text not null default '',
  postal_code text not null default '',
  city text not null default '',
  country_code text not null default 'DE',
  vat_id text not null default '',
  buyer_reference text not null default '',
  source text not null default 'manual'
    check (source in ('manual', 'website', 'phone', 'email', 'referral', 'agent', 'other')),
  marketing_consent boolean not null default false,
  notes text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  mail_backfilled boolean not null default false
);

create table if not exists crm.products (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(btrim(name)) between 1 and 200)
);

create table if not exists crm.orders (
  id uuid primary key default gen_random_uuid(),
  number text not null unique,
  customer_id uuid not null references crm.customers (id) on delete restrict,
  status text not null default 'open' check (status in ('open', 'in_progress', 'done', 'invoiced'))
);

do $$
declare t text;
begin
  foreach t in array array['customers', 'products', 'orders'] loop
    execute format('alter table crm.%I enable row level security', t);
    execute format('drop policy if exists admin_all on crm.%I', t);
    execute format('create policy admin_all on crm.%I for all to authenticated using (public.is_admin()) with check (public.is_admin())', t);
  end loop;
end $$;

grant usage on schema crm to anon, authenticated, service_role;
grant select, insert, update, delete on all tables in schema crm to authenticated, service_role;
