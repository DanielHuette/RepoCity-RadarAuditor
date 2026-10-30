-- Deferred jobs: a job may wait until a point in time, used by serp_retry for its backoff.
-- Context: when Google blocks the server browser, the remaining searches are retried after 15 min,
-- 1 h, 4 h and 12 h; the phrases themselves stay in the raw data of the audit. | Input: migrations
-- 001-006. | Output: column jobs.run_after, claim_jobs skipping jobs that are not due yet.
-- Every statement is guarded, so running the file twice changes nothing.

alter table public.jobs add column if not exists run_after timestamptz;

create or replace function public.claim_jobs(p_kind job_kind, p_limit integer) returns setof public.jobs
language sql set search_path = public, pg_temp as $$
  update public.jobs set status = 'running', attempts = attempts + 1, updated_at = now()
  where id in (
    select id from public.jobs
    where kind = p_kind and status = 'queued' and (run_after is null or run_after <= now())
    order by created_at limit p_limit for update skip locked
  )
  returning *;
$$;
