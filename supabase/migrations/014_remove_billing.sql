-- Billing leaves the RadarAuditor: invoices, credit notes, payments, invoice numbers, the monthly run and the customer phase.
-- Context: decided on 24 October 2026, the CRM owns customer status, invoices, payments, the tax adviser package and the
-- monthly audit dates; the RadarAuditor only audits, evaluates and writes quotes. | Input: migrations 001-013. |
-- Output: no tables payments and invoice_counters, no functions next_invoice_number and forbid_invoice_update, no column
-- customers.stage, documents.cancels_document_id or type customer_stage, job_kind without monthly, document_kind without
-- invoice and credit_note, claim_jobs for the worker only, no settings monthly_last_run and tax_export_request, no
-- storage bucket exports of the tax export and no read right on it.
-- Every statement is guarded, so running the file twice changes nothing. Issued invoices are never deleted: while one is
-- stored the migration stops with a message instead.

do $$
declare
  v_invoices bigint;
begin
  if to_regtype('public.document_kind') is null
     or not exists (select 1 from pg_enum where enumtypid = 'public.document_kind'::regtype and enumlabel = 'invoice') then
    return;
  end if;
  select count(*) into v_invoices from public.documents where kind::text in ('invoice', 'credit_note');
  if v_invoices > 0 then
    raise exception 'Migration 014 abgebrochen: % Rechnungen oder Gutschriften liegen noch in public.documents. Bitte erst ins CRM übernehmen.', v_invoices;
  end if;
end $$;

drop trigger if exists documents_immutable on public.documents;
drop function if exists public.forbid_invoice_update();
drop function if exists public.next_invoice_number(integer);
drop table if exists public.payments;
drop table if exists public.invoice_counters;
alter table public.documents drop column if exists cancels_document_id;
alter table public.customers drop column if exists stage;
drop type if exists public.customer_stage;
delete from public.settings where key in ('monthly_last_run', 'tax_export_request');

-- PostgreSQL cannot drop a value from an enum, so the type is built anew without it. Triggers with a WHEN condition keep
-- the column type fixed, so they are taken off for the switch and put back unchanged.
create or replace function pg_temp.drop_enum_values(p_type text, p_table regclass, p_column text, p_removed text[])
returns void language plpgsql as $$
declare
  v_old text := p_type || '_before_014';
  v_labels text;
  v_triggers text[];
  v_trigger text;
begin
  select string_agg(quote_literal(e.enumlabel), ', ' order by e.enumsortorder) into v_labels
  from pg_enum e where e.enumtypid = format('public.%I', p_type)::regtype and e.enumlabel <> all (p_removed);
  select coalesce(array_agg(pg_get_triggerdef(t.oid)), '{}') into v_triggers
  from pg_trigger t where t.tgrelid = p_table and not t.tgisinternal and t.tgqual is not null;
  for v_trigger in select t.tgname from pg_trigger t where t.tgrelid = p_table and not t.tgisinternal and t.tgqual is not null loop
    execute format('drop trigger %I on %s', v_trigger, p_table);
  end loop;
  execute format('alter type public.%I rename to %I', p_type, v_old);
  execute format('create type public.%I as enum (%s)', p_type, v_labels);
  execute format('alter table %s alter column %I type public.%I using %I::text::public.%I', p_table, p_column, p_type, p_column, p_type);
  execute format('drop type public.%I', v_old);
  foreach v_trigger in array v_triggers loop
    execute v_trigger;
  end loop;
end $$;

do $$
begin
  if exists (select 1 from pg_enum where enumtypid = 'public.document_kind'::regtype and enumlabel in ('invoice', 'credit_note')) then
    perform pg_temp.drop_enum_values('document_kind', 'public.documents', 'kind', array['invoice', 'credit_note']);
  end if;
  if exists (select 1 from pg_enum where enumtypid = 'public.job_kind'::regtype and enumlabel = 'monthly') then
    delete from public.jobs where kind::text = 'monthly';
    drop function if exists public.claim_jobs(public.job_kind, integer);
    perform pg_temp.drop_enum_values('job_kind', 'public.jobs', 'kind', array['monthly']);
  end if;
end $$;

create or replace function public.claim_jobs(p_kind public.job_kind, p_limit integer) returns setof public.jobs
language sql set search_path = public, pg_temp as $$
  update public.jobs set status = 'running', attempts = attempts + 1, updated_at = now()
  where id in (
    select id from public.jobs
    where kind = p_kind and status = 'queued' and (run_after is null or run_after <= now())
    order by created_at limit p_limit for update skip locked
  )
  returning *;
$$;
revoke all on function public.claim_jobs(public.job_kind, integer) from public, anon, authenticated;
grant execute on function public.claim_jobs(public.job_kind, integer) to service_role;

-- The tax export's bucket goes once it is empty; files in it are never deleted here.
drop policy if exists authenticated_read_radar_buckets on storage.objects;
create policy authenticated_read_radar_buckets on storage.objects for select to authenticated
  using (auth.role() = 'authenticated' and bucket_id in ('raw', 'reports', 'documents'));

do $$
begin
  if exists (select 1 from storage.objects where bucket_id = 'exports') then
    raise notice 'Die Ablage exports enthält noch Dateien und bleibt; leer sie im Supabase-Dashboard und lösch sie dort.';
    return;
  end if;
  delete from storage.buckets where id = 'exports';
exception
  -- Supabase may refuse deleting buckets by SQL; the bucket then stays for the dashboard.
  when others then
    raise notice 'Die Ablage exports ließ sich nicht per SQL löschen (%); lösch sie im Supabase-Dashboard.', sqlerrm;
end $$;
