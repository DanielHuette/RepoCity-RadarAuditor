-- Field app: audits started from the phone, link to the CRM, optimisation drafts, daily source caps.
-- Context: runs after the CRM migrations (schema crm) in the same project; the worker uses the service
-- key and is not bound by row level security. | Input: migrations 001-005 and schema crm. |
-- Output: enum values, columns, tables optimization_changes and source_usage, functions
-- start_field_audit, start_optimization, decide_change, consume_source_quota, realtime publication.
-- Every statement is guarded, so running the file twice changes nothing.
-- New enum values cannot be used in the transaction that adds them, so constraints compare kind::text
-- and every function that names them is plpgsql (resolved at first call, not at creation).

do $$
begin
  if to_regclass('crm.customers') is null or to_regclass('crm.orders') is null
     or to_regclass('crm.products') is null then
    raise exception 'Schema crm (customers, orders, products) is missing; apply the CRM migrations first.';
  end if;
end $$;

alter type public.check_outcome add value if not exists 'skipped';
alter type public.job_kind add value if not exists 'serp_retry';
alter type public.job_kind add value if not exists 'optimize';
alter type public.job_kind add value if not exists 'apply';
alter type public.document_kind add value if not exists 'audit_quote';

do $$
begin
  create type public.optimization_change_status as enum ('draft', 'approved', 'rejected', 'applied', 'failed');
exception when duplicate_object then null;
end $$;

-- Potential and uplift per check result -------------------------------------------------------------

alter table public.check_results add column if not exists potential_points numeric;
alter table public.check_results add column if not exists uplift_low numeric;
alter table public.check_results add column if not exists uplift_high numeric;

alter table public.check_results drop constraint if exists check_results_potential_not_negative;
alter table public.check_results add constraint check_results_potential_not_negative
  check (potential_points is null or potential_points >= 0);
alter table public.check_results drop constraint if exists check_results_uplift_range;
alter table public.check_results add constraint check_results_uplift_range
  check (uplift_low is null or uplift_high is null or (uplift_low >= 0 and uplift_low <= uplift_high));

-- Link to the CRM ------------------------------------------------------------------------------------

alter table public.customers add column if not exists crm_customer_id uuid
  references crm.customers(id) on delete set null;
create index if not exists customers_crm_customer on public.customers (crm_customer_id);

-- Catalogue area ids a product covers; the quote proposes products whose areas have failed checks.
alter table crm.products add column if not exists radar_areas text[] not null default '{}';

alter table public.jobs add column if not exists order_id uuid references crm.orders(id) on delete cascade;
create index if not exists jobs_order on public.jobs (order_id) where order_id is not null;
alter table public.jobs drop constraint if exists jobs_order_required;
alter table public.jobs add constraint jobs_order_required
  check (kind::text not in ('optimize', 'apply') or order_id is not null);

-- Host of a website without scheme, "www.", port, path, query and fragment; the key to reuse a customer.
create or replace function public.website_host(p_website text) returns text
language sql immutable parallel safe set search_path = pg_catalog, pg_temp as $$
  select nullif(
    regexp_replace(
      regexp_replace(
        regexp_replace(lower(btrim(coalesce(p_website, ''))), '^[a-z][a-z0-9+.-]*://', ''),
        '[/?#:].*$', ''),
      '^www\.', ''),
    '')
$$;
create index if not exists customers_website_host on public.customers (public.website_host(website));

-- Optimisation drafts ------------------------------------------------------------------------------

create table if not exists public.optimization_changes (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references crm.orders(id) on delete cascade,
  audit_id uuid references public.audits(id) on delete set null,
  check_id text,
  target text not null,
  before text,
  after text not null,
  status public.optimization_change_status not null default 'draft',
  error text,
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  applied_at timestamptz
);
create index if not exists optimization_changes_order on public.optimization_changes (order_id, status);

-- Daily usage per source; the day is the calendar day in Europe/Berlin.
create table if not exists public.source_usage (
  day date not null,
  source text not null,
  count integer not null default 0 check (count >= 0),
  primary key (day, source)
);

alter table public.optimization_changes enable row level security;
alter table public.source_usage enable row level security;

do $$
declare t text;
begin
  foreach t in array array['optimization_changes', 'source_usage'] loop
    execute format('drop policy if exists reader_select on public.%I', t);
    execute format('drop policy if exists admin_write on public.%I', t);
    execute format('create policy reader_select on public.%I for select to authenticated using (true)', t);
    execute format('create policy admin_write on public.%I for all to authenticated using (public.is_admin()) with check (public.is_admin())', t);
  end loop;
end $$;

-- Functions ----------------------------------------------------------------------------------------
-- Security invoker: the row level security of public and crm still applies; the explicit check gives
-- readers a clear message instead of a bare policy violation halfway through.

create or replace function public.require_admin() returns void
language plpgsql stable set search_path = public, pg_temp as $$
begin
  if current_user not in ('postgres', 'service_role') and not public.is_admin() then
    raise exception 'Nur der Administrator darf das.' using errcode = '42501';
  end if;
end $$;

create or replace function public.start_field_audit(
  p_website text,
  p_name text,
  p_city text,
  p_industry text,
  p_keywords text[] default '{}',
  p_competitors jsonb default '[]'
) returns uuid
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_host text := public.website_host(p_website);
  v_name text := btrim(coalesce(p_name, ''));
  v_city text := btrim(coalesce(p_city, ''));
  v_industry text := btrim(coalesce(p_industry, ''));
  v_customer public.customers;
  v_crm_customer uuid;
  v_audit uuid;
  v_phrase text;
  v_has_primary boolean;
  v_competitor jsonb;
  v_competitor_name text;
  v_competitor_site text;
begin
  perform public.require_admin();
  if v_host is null then
    raise exception 'Website fehlt.' using errcode = '22023';
  end if;
  if v_name = '' or v_city = '' or v_industry = '' then
    raise exception 'Name, Ort und Branche sind Pflicht.' using errcode = '22023';
  end if;
  if p_competitors is not null and jsonb_typeof(p_competitors) <> 'array' then
    raise exception 'Wettbewerber müssen eine Liste sein.' using errcode = '22023';
  end if;

  -- Serialises two starts for the same new website, which would otherwise both create a customer.
  perform pg_advisory_xact_lock(hashtext('field_audit:' || v_host));

  select * into v_customer from public.customers c
  where public.website_host(c.website) = v_host
  order by c.created_at
  limit 1;

  if not found then
    insert into public.customers (name, website, industry, city)
    values (
      v_name,
      case when btrim(p_website) ~* '^https?://' then btrim(p_website) else 'https://' || btrim(p_website) end,
      v_industry,
      v_city)
    returning * into v_customer;
  end if;

  v_crm_customer := v_customer.crm_customer_id;
  if v_crm_customer is null then
    -- Link an existing CRM customer only when name and city identify exactly one.
    select min(cc.id::text)::uuid into v_crm_customer from crm.customers cc
    where lower(btrim(cc.name)) = lower(v_customer.name) and lower(btrim(cc.city)) = lower(v_customer.city)
    having count(*) = 1;
    if v_crm_customer is null then
      insert into crm.customers (name, city, source)
      values (left(v_customer.name, 200), v_customer.city, 'manual')
      returning id into v_crm_customer;
    end if;
    update public.customers set crm_customer_id = v_crm_customer where id = v_customer.id;
  end if;

  if not exists (select 1 from public.locations l where l.customer_id = v_customer.id) then
    insert into public.locations (customer_id, name, street, postal_code, city, phone)
    values (v_customer.id, v_customer.name, '', '', v_city, '');
  end if;

  select exists (select 1 from public.keywords k where k.customer_id = v_customer.id and k.is_primary)
  into v_has_primary;
  foreach v_phrase in array coalesce(p_keywords, '{}') loop
    v_phrase := btrim(v_phrase);
    continue when v_phrase is null or v_phrase = '';
    insert into public.keywords (customer_id, phrase, city, is_primary)
    values (v_customer.id, v_phrase, v_city, not v_has_primary)
    on conflict (customer_id, phrase, city) do nothing;
    v_has_primary := v_has_primary or found;
  end loop;

  for v_competitor in select value from jsonb_array_elements(coalesce(p_competitors, '[]')) loop
    v_competitor_name := btrim(coalesce(v_competitor ->> 'name', ''));
    continue when v_competitor_name = '';
    v_competitor_site := nullif(btrim(coalesce(v_competitor ->> 'website', '')), '');
    if v_competitor_site is not null and v_competitor_site !~* '^https?://' then
      v_competitor_site := 'https://' || v_competitor_site;
    end if;
    if not exists (
      select 1 from public.competitors c
      where c.customer_id = v_customer.id and lower(c.name) = lower(v_competitor_name)
    ) then
      insert into public.competitors (customer_id, name, website, google_place_id)
      values (v_customer.id, v_competitor_name, v_competitor_site,
              nullif(btrim(coalesce(v_competitor ->> 'google_place_id', '')), ''));
    end if;
  end loop;

  insert into public.audits (customer_id, kind) values (v_customer.id, 'remote')
  returning id into v_audit;
  insert into public.jobs (kind, customer_id, audit_id) values ('collect', v_customer.id, v_audit);
  return v_audit;
end $$;

create or replace function public.start_optimization(p_order_id uuid) returns uuid
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_crm_customer uuid;
  v_customer uuid;
  v_audit uuid;
  v_job uuid;
begin
  perform public.require_admin();
  select o.customer_id into v_crm_customer from crm.orders o where o.id = p_order_id;
  if not found then
    raise exception 'Auftrag nicht gefunden.' using errcode = 'P0002';
  end if;
  select c.id into v_customer from public.customers c
  where c.crm_customer_id = v_crm_customer order by c.created_at limit 1;
  if not found then
    raise exception 'Zum Auftrag gibt es keinen geprüften Kunden.' using errcode = 'P0002';
  end if;

  perform pg_advisory_xact_lock(hashtext('optimize:' || p_order_id::text));
  select j.id into v_job from public.jobs j
  where j.order_id = p_order_id and j.kind = 'optimize' and j.status in ('queued', 'running')
  limit 1;
  if found then
    return v_job;
  end if;

  select a.id into v_audit from public.audits a
  where a.customer_id = v_customer order by a.requested_at desc limit 1;
  insert into public.jobs (kind, customer_id, audit_id, order_id)
  values ('optimize', v_customer, v_audit, p_order_id)
  returning id into v_job;
  return v_job;
end $$;

create or replace function public.decide_change(p_change_id uuid, p_approve boolean)
returns public.optimization_changes
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_change public.optimization_changes;
  v_customer uuid;
begin
  perform public.require_admin();
  if p_approve is null then
    raise exception 'Entscheidung fehlt.' using errcode = '22023';
  end if;

  update public.optimization_changes
  set status = case when p_approve then 'approved' else 'rejected' end::public.optimization_change_status,
      decided_at = now()
  where id = p_change_id and status = 'draft'
  returning * into v_change;
  if not found then
    if exists (select 1 from public.optimization_changes where id = p_change_id) then
      raise exception 'Diese Änderung ist schon entschieden.' using errcode = '55000';
    end if;
    raise exception 'Änderung nicht gefunden.' using errcode = 'P0002';
  end if;

  if p_approve then
    -- One queued apply job per order picks up every approved draft of that order.
    perform pg_advisory_xact_lock(hashtext('apply:' || v_change.order_id::text));
    if not exists (
      select 1 from public.jobs j
      where j.order_id = v_change.order_id and j.kind = 'apply' and j.status = 'queued'
    ) then
      select c.id into v_customer from public.customers c
      join crm.orders o on o.customer_id = c.crm_customer_id
      where o.id = v_change.order_id order by c.created_at limit 1;
      if v_customer is null then
        raise exception 'Zum Auftrag gibt es keinen geprüften Kunden.' using errcode = 'P0002';
      end if;
      insert into public.jobs (kind, customer_id, audit_id, order_id)
      values ('apply', v_customer, v_change.audit_id, v_change.order_id);
    end if;
  end if;
  return v_change;
end $$;

-- Counts one use of a source and answers whether it was still within the daily cap; atomic, so
-- parallel jobs cannot overrun the cap.
create or replace function public.consume_source_quota(p_source text, p_cap integer) returns boolean
language plpgsql set search_path = public, pg_temp as $$
declare
  v_day date := (now() at time zone 'Europe/Berlin')::date;
  v_count integer;
begin
  if p_cap is null or p_cap <= 0 then
    return false;
  end if;
  insert into public.source_usage as u (day, source, count) values (v_day, p_source, 1)
  on conflict (day, source) do update set count = u.count + 1 where u.count < p_cap
  returning u.count into v_count;
  return v_count is not null;
end $$;

revoke all on function public.require_admin() from public, anon;
revoke all on function public.start_field_audit(text, text, text, text, text[], jsonb) from public, anon;
revoke all on function public.start_optimization(uuid) from public, anon;
revoke all on function public.decide_change(uuid, boolean) from public, anon;
revoke all on function public.consume_source_quota(text, integer) from public, anon, authenticated;
grant execute on function public.require_admin() to authenticated, service_role;
grant execute on function public.start_field_audit(text, text, text, text, text[], jsonb) to authenticated, service_role;
grant execute on function public.start_optimization(uuid) to authenticated, service_role;
grant execute on function public.decide_change(uuid, boolean) to authenticated, service_role;
grant execute on function public.consume_source_quota(text, integer) to service_role;

-- Realtime: the app follows jobs, audits, documents and drafts live ----------------------------------

do $$
declare
  v_publication oid;
  v_name text;
  v_table regclass;
begin
  select oid into v_publication from pg_publication where pubname = 'supabase_realtime';
  if v_publication is null then
    return;
  end if;
  foreach v_name in array array['public.jobs', 'public.audits', 'public.documents',
                                'public.optimization_changes', 'crm.documents', 'crm.orders',
                                'crm.invoices'] loop
    v_table := to_regclass(v_name);
    continue when v_table is null;
    if not exists (select 1 from pg_publication_rel where prpubid = v_publication and prrelid = v_table) then
      execute format('alter publication supabase_realtime add table %s', v_table);
    end if;
  end loop;
end $$;
