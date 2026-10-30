-- Invoice numbering and immutability. Context: § 14 UStG requires gap-free, unique numbers; issued invoices never change.

create or replace function next_invoice_number(p_year integer) returns text
language plpgsql as $$
declare n integer;
begin
  insert into invoice_counters (year, last_number) values (p_year, 1)
  on conflict (year) do update set last_number = invoice_counters.last_number + 1
  returning last_number into n;
  return format('RA-%s-%s', p_year, lpad(n::text, 4, '0'));
end $$;

create or replace function forbid_invoice_update() returns trigger
language plpgsql as $$
begin
  if old.kind in ('invoice','credit_note') and old.number is not null then
    raise exception 'issued documents are immutable; create a credit note instead';
  end if;
  return new;
end $$;

create trigger documents_immutable before update or delete on documents
for each row execute function forbid_invoice_update();

create or replace function claim_jobs(p_kind job_kind, p_limit integer) returns setof jobs
language sql as $$
  update jobs set status = 'running', attempts = attempts + 1, updated_at = now()
  where id in (
    select id from jobs where kind = p_kind and status = 'queued'
    order by created_at limit p_limit for update skip locked
  )
  returning *;
$$;
