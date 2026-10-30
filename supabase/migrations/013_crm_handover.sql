-- Hand-over to the CRM: monthly audits on request of the CRM, the event that ends an optimisation,
-- and CRM customers marked as created by the RadarAuditor.
-- Context: the CRM owns subscriptions, invoices and the monthly run (crm/supabase/migrations/
-- 004_flow.sql in Firma-RepoCity); it asks for a monthly audit through queue_monthly_audit and
-- completes an order when the event optimization.finished arrives. | Input: migrations 001-012 and
-- schema crm (customers, orders). | Output: column audits.purpose, functions queue_monthly_audit,
-- write_optimization_finished and link_crm_customer (source radarauditor), triggers on
-- optimization_changes and jobs, the unique index that keeps the event to one per order, and the
-- value radarauditor in the source rule of crm.customers.
-- Order in the shared project: this file, then CRM 004 (which states the full source rule itself);
-- the other way round works too, because this file only adds radarauditor when the rule lacks it.
-- Every statement is guarded, so running the file twice changes nothing.

alter table public.audits add column if not exists purpose text not null default 'sales';
alter table public.audits drop constraint if exists audits_purpose_check;
alter table public.audits add constraint audits_purpose_check check (purpose in ('sales', 'monthly'));

comment on column public.audits.purpose is
  'sales: audit before a quote; monthly: audit of a running subscription, reported as before and after.';

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'crm.customers'::regclass and conname = 'customers_source_check'
      and pg_get_constraintdef(oid) like '%radarauditor%'
  ) then
    alter table crm.customers drop constraint if exists customers_source_check;
    alter table crm.customers add constraint customers_source_check check (source in
      ('manual', 'website', 'phone', 'email', 'referral', 'agent', 'other', 'radarauditor'));
  end if;
end $$;

-- Same rules as 011; only the source of a new CRM customer changes.
create or replace function public.link_crm_customer(p_customer_id uuid) returns uuid
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_customer public.customers;
  v_crm_customer uuid;
begin
  perform public.require_admin();
  select * into v_customer from public.customers c where c.id = p_customer_id for update;
  if not found then
    raise exception 'Kunde nicht gefunden.' using errcode = 'P0002';
  end if;
  if v_customer.crm_customer_id is not null then
    return v_customer.crm_customer_id;
  end if;
  -- Neither a provisional name (the website host) nor an empty city goes to the CRM.
  if btrim(v_customer.city) = ''
     or lower(btrim(v_customer.name)) = coalesce(public.website_host(v_customer.website), '') then
    return null;
  end if;
  select min(cc.id::text)::uuid into v_crm_customer from crm.customers cc
  where lower(btrim(cc.name)) = lower(btrim(v_customer.name))
    and lower(btrim(cc.city)) = lower(btrim(v_customer.city))
  having count(*) = 1;
  if v_crm_customer is null then
    insert into crm.customers (name, city, source)
    values (left(btrim(v_customer.name), 200), btrim(v_customer.city), 'radarauditor')
    returning id into v_crm_customer;
  end if;
  update public.customers set crm_customer_id = v_crm_customer where id = v_customer.id;
  return v_crm_customer;
end $$;

-- A new audit of the customer for the monthly report, with the selection of the latest audit and the
-- Google access when it was granted; queued exactly like queue_field_audit.
create or replace function public.queue_monthly_audit(p_crm_customer uuid) returns uuid
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_customer uuid;
  v_kind public.audit_kind;
  v_disabled_checks text[];
  v_audit uuid;
begin
  perform public.require_admin();
  select c.id into v_customer from public.customers c
  where c.crm_customer_id = p_crm_customer order by c.created_at limit 1;
  if not found then
    raise exception 'Zu diesem Kunden gibt es noch keine Prüfung.' using errcode = 'P0002';
  end if;
  select case when coalesce(ca.google_business_profile_granted, false) then 'with_access'
              else 'remote' end::public.audit_kind
  into v_kind
  from (select v_customer as id) c left join public.customer_access ca on ca.customer_id = c.id;
  select a.disabled_checks into v_disabled_checks from public.audits a
  where a.customer_id = v_customer order by a.requested_at desc limit 1;
  insert into public.audits (customer_id, kind, disabled_checks, purpose)
  values (v_customer, v_kind, coalesce(v_disabled_checks, '{}'), 'monthly')
  returning id into v_audit;
  insert into public.jobs (kind, customer_id, audit_id) values ('collect', v_customer, v_audit);
  return v_audit;
end $$;

create unique index if not exists events_optimization_finished_once
  on public.events ((payload ->> 'order_id')) where kind = 'optimization.finished';

-- Writes optimization.finished when the order has a change or an optimize run that found nothing to
-- change, no change waits for a decision, an apply run or a hand or failed, and no optimize or apply
-- job is waiting or running.
create or replace function public.write_optimization_finished(p_order_id uuid) returns void
language plpgsql security invoker set search_path = public, pg_temp as $$
begin
  -- Waits for a concurrent transaction on the same order, so the checks below see its outcome.
  perform pg_advisory_xact_lock(hashtext('optimization_finished:' || p_order_id::text));
  if not (exists (select 1 from public.optimization_changes c where c.order_id = p_order_id)
          or exists (select 1 from public.jobs j
                     where j.order_id = p_order_id and j.kind::text = 'optimize' and j.status = 'done'))
     or exists (
       select 1 from public.optimization_changes c
       where c.order_id = p_order_id and c.status::text in ('draft', 'approved', 'manual', 'failed'))
     or exists (
       select 1 from public.jobs j
       where j.order_id = p_order_id and j.kind::text in ('optimize', 'apply')
         and j.status in ('queued', 'running')) then
    return;
  end if;
  insert into public.events (kind, payload)
  values ('optimization.finished', jsonb_build_object('order_id', p_order_id))
  on conflict ((payload ->> 'order_id')) where kind = 'optimization.finished' do nothing;
end $$;

create or replace function public.on_order_progress() returns trigger
language plpgsql security invoker set search_path = public, pg_temp as $$
begin
  if new.order_id is not null then
    perform public.write_optimization_finished(new.order_id);
  end if;
  return null;
end $$;

drop trigger if exists optimization_changes_finished on public.optimization_changes;
create trigger optimization_changes_finished
  after insert or update of status on public.optimization_changes
  for each row execute function public.on_order_progress();

drop trigger if exists jobs_order_finished on public.jobs;
create trigger jobs_order_finished
  after insert or update of status on public.jobs
  for each row when (new.order_id is not null)
  execute function public.on_order_progress();

revoke all on function public.link_crm_customer(uuid) from public, anon;
revoke all on function public.queue_monthly_audit(uuid) from public, anon;
revoke all on function public.write_optimization_finished(uuid) from public, anon;
revoke all on function public.on_order_progress() from public, anon, authenticated;
grant execute on function public.link_crm_customer(uuid) to authenticated, service_role;
grant execute on function public.queue_monthly_audit(uuid) to authenticated, service_role;
grant execute on function public.write_optimization_finished(uuid) to authenticated, service_role;
