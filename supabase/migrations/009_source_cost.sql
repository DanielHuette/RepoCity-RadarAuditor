-- Spend per paid source: what each DataForSEO request cost, and the month's total for its cost cap.
-- Context: DataForSEO is on by default with a monthly cost cap in the app settings
-- (sources.dataforseo.monthly_cost_cap_eur); the worker adds the cost of every request and stops at
-- the cap, after which the affected checks are not measurable with reason "Monatslimit erreicht".
-- | Input: migrations 001-007 (table source_usage from 006). | Output: column source_usage.cost,
-- functions add_source_cost and source_month_cost (service role only).
-- Months and days are calendar months and days in Europe/Berlin, like consume_source_quota.
-- Every statement is guarded, so running the file twice changes nothing.

alter table public.source_usage add column if not exists cost numeric not null default 0;

do $$
begin
  alter table public.source_usage add constraint source_usage_cost_not_negative check (cost >= 0);
exception when duplicate_object then null;
end $$;

comment on column public.source_usage.cost is 'Cost in euros of the requests of this day and source.';

-- Spend of a source in the current calendar month.
create or replace function public.source_month_cost(p_source text) returns numeric
language sql stable set search_path = public, pg_temp as $$
  select coalesce(sum(cost), 0)
  from public.source_usage
  where source = p_source
    and day >= date_trunc('month', now() at time zone 'Europe/Berlin')::date;
$$;

-- Adds the cost of one request to today's row of the source in one statement, so parallel jobs
-- cannot lose an addition, and answers the month's spend after it.
create or replace function public.add_source_cost(p_source text, p_cost numeric) returns numeric
language plpgsql set search_path = public, pg_temp as $$
declare
  v_day date := (now() at time zone 'Europe/Berlin')::date;
begin
  if p_cost is null or p_cost < 0 then
    raise exception 'cost must be zero or positive, got %', p_cost using errcode = '22023';
  end if;
  insert into public.source_usage as u (day, source, count, cost) values (v_day, p_source, 0, p_cost)
  on conflict (day, source) do update set cost = u.cost + excluded.cost;
  return public.source_month_cost(p_source);
end $$;

revoke all on function public.source_month_cost(text) from public, anon, authenticated;
revoke all on function public.add_source_cost(text, numeric) from public, anon, authenticated;
grant execute on function public.source_month_cost(text) to service_role;
grant execute on function public.add_source_cost(text, numeric) to service_role;
