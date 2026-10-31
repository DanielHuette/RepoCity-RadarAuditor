-- Customer search, review reply drafts for the customer and the monthly service.
-- Context: the app asks for the businesses of a trade in a city (request_prospect_search); the worker
-- claims the search, reads the list from the Places API, ranks it by signs of a neglected profile and
-- stores it (claim_prospect_search, finish_prospect_search, fail_prospect_search). Google's EEA terms
-- allow lasting storage only of place ids (Service Specific Terms A.3) and coordinates for 30 days
-- (15.4); every other field may only be used for the permitted uses and has no caching permission. So a
-- list is shown for prospect_cache_hours() and then emptied (purge_prospect_searches): only the place
-- id, the link to the CRM and the objection mark stay. A request of the same trade and city within that
-- time reuses the list and costs nothing. add_prospect_to_crm takes a business into the CRM with the
-- name, address and phone Daniel copied from the public profile himself, never with the stored Places
-- fields. Objections to advertising land in advertising_objections, which outlasts the deletion of a
-- customer; a business on it is marked in the list and refused by add_prospect_to_crm, found by name
-- and city (a name that starts with the other counts, an entry without city holds everywhere), by phone
-- number or by place id. The cron job radar-prospect-purge empties old lists every hour. The app only reads the two search tables; it writes through
-- the two security definer functions, which check the administrator themselves. Document kind
-- review_replies is the PDF of reply drafts to unanswered reviews; queue_monthly_service gives a
-- reported monthly audit an optimize job on the order of the customer's subscription once the first
-- optimisation of that order is finished (event optimization.finished). | Input: migrations 001-015 and
-- schema crm (customers; subscriptions, orders, deals, activities, crm.log and customers.no_follow_ups
-- from the CRM migrations, read when the functions run; the objection trigger only where the column
-- exists; pg_cron from CRM migration 003). | Output: enum value review_replies, tables prospect_searches, prospect_results and
-- advertising_objections, the functions above, the cron job radar-prospect-purge, realtime for the two search tables.
-- Every statement is guarded, so running the file twice changes nothing. The new enum value cannot be
-- used in the transaction that adds it; no statement here uses it.

alter type public.document_kind add value if not exists 'review_replies';

-- Customer search -----------------------------------------------------------------------------------

-- Hours a list is shown and reused before its Places fields are deleted.
create or replace function public.prospect_cache_hours() returns integer
language sql immutable parallel safe set search_path = pg_catalog, pg_temp as $$ select 24 $$;

-- Comparable form of a business or city name: lower case, umlauts written out, additions in brackets,
-- legal forms and every character other than letters and digits removed ("Bäckerei Korn GmbH & Co. KG"
-- is "baeckereikorn", "Münster (Westf.)" is "muenster").
create or replace function public.name_key(p_name text) returns text
language sql immutable parallel safe set search_path = pg_catalog, pg_temp as $$
  select regexp_replace(
    regexp_replace(
      replace(replace(replace(replace(
        translate(lower(translate(regexp_replace(coalesce(p_name, ''), '\([^)]*\)', ' ', 'g'), 'ÄÖÜ', 'äöü')),
                  'àáâèéêìíîòóôùúû', 'aaaeeeiiiooouuu'),
        'ä', 'ae'), 'ö', 'oe'), 'ü', 'ue'), 'ß', 'ss'),
      '(^|[^a-z0-9])(gmbh|gbr|ug|ohg|kg|ag|mbh|co|e\.? ?k|e\.? ?v|inh|inhaber)(?=[^a-z0-9]|$)', '\1', 'g'),
    '[^a-z0-9]', '', 'g')
$$;

-- Comparable form of a German phone number: digits only, +49 and 0049 written as 0 (a "(0)" after the
-- country code dropped); '' below six digits.
create or replace function public.phone_key(p_phone text) returns text
language sql immutable parallel safe set search_path = pg_catalog, pg_temp as $$
  with digits as (select regexp_replace(coalesce(p_phone, ''), '\D', '', 'g') as d)
  select case
    when length(d) < 6 then ''
    when d like '0049%' then '0' || ltrim(substr(d, 5), '0')
    when btrim(coalesce(p_phone, '')) like '+49%' then '0' || ltrim(substr(d, 3), '0')
    else d end
  from digits
$$;

create table if not exists public.prospect_searches (
  id uuid primary key default gen_random_uuid(),
  trade text not null check (length(trade) between 2 and 80),
  city text not null check (length(city) between 2 and 80),
  -- Umlauts are folded by hand: lower() leaves them as they are under the C collation.
  search_key text generated always as (
    translate(lower(trade), 'ÄÖÜ', 'äöü') || '|' || translate(lower(city), 'ÄÖÜ', 'äöü')) stored,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'done', 'failed', 'expired')),
  error text,
  result_count integer check (result_count is null or result_count >= 0),
  requested_at timestamptz not null default now(),
  claimed_at timestamptz,
  fetched_at timestamptz
);
create unique index if not exists prospect_searches_key on public.prospect_searches (search_key);
create index if not exists prospect_searches_queue on public.prospect_searches (requested_at)
  where status in ('queued', 'running');

comment on table public.prospect_searches is
  'One customer search per trade and city; its list is shown for prospect_cache_hours() hours.';

create table if not exists public.prospect_results (
  id uuid primary key default gen_random_uuid(),
  search_id uuid not null references public.prospect_searches(id) on delete cascade,
  rank integer not null check (rank >= 1),
  place_id text not null,
  name text not null,
  address text not null default '',
  street text not null default '',
  postal_code text not null default '',
  city text not null default '',
  phone text,
  website text,
  maps_url text,
  rating numeric(2, 1),
  review_count integer not null default 0 check (review_count >= 0),
  photo_count integer not null default 0 check (photo_count >= 0),
  has_opening_hours boolean not null default false,
  score integer not null default 0 check (score >= 0),
  signals jsonb not null default '[]' check (jsonb_typeof(signals) = 'array'),
  reasons text not null,
  contact_ways jsonb not null default '[]' check (jsonb_typeof(contact_ways) = 'array'),
  crm_customer_id uuid references crm.customers(id) on delete set null,
  unique (search_id, place_id)
);
alter table public.prospect_results add column if not exists objected boolean not null default false;
create index if not exists prospect_results_rank on public.prospect_results (search_id, rank);
create index if not exists prospect_results_crm_customer on public.prospect_results (crm_customer_id)
  where crm_customer_id is not null;

comment on column public.prospect_results.objected is
  'The business objected to advertising (advertising_objections); the app offers no way of contact.';

-- Objections to advertising. No foreign key: an objection stays when the CRM customer is deleted.
create table if not exists public.advertising_objections (
  id uuid primary key default gen_random_uuid(),
  crm_customer_id uuid,
  name_key text not null default '',
  city_key text not null default '',
  phone_key text not null default '',
  place_ids text[] not null default '{}',
  created_at timestamptz not null default now()
);
create unique index if not exists advertising_objections_customer
  on public.advertising_objections (crm_customer_id) where crm_customer_id is not null;

alter table public.prospect_searches enable row level security;
alter table public.prospect_results enable row level security;
alter table public.advertising_objections enable row level security;

do $$
declare t text;
begin
  foreach t in array array['prospect_searches', 'prospect_results'] loop
    execute format('drop policy if exists reader_select on public.%I', t);
    execute format('drop policy if exists admin_write on public.%I', t);
    execute format('create policy reader_select on public.%I for select to authenticated using (true)', t);
  end loop;
  execute 'drop policy if exists admin_select on public.advertising_objections';
  execute 'create policy admin_select on public.advertising_objections for select to authenticated using (public.is_admin())';
end $$;

-- The app reads; every write goes through the functions below.
revoke all on table public.prospect_searches, public.prospect_results, public.advertising_objections from anon;
revoke insert, update, delete, truncate on table public.prospect_searches, public.prospect_results,
  public.advertising_objections from authenticated;
grant select on table public.prospect_searches, public.prospect_results, public.advertising_objections
  to authenticated;
grant all on table public.prospect_searches, public.prospect_results, public.advertising_objections
  to service_role;

-- Whether two keys of name_key name the same thing: equal, or the shorter one, at least p_min_length
-- characters long, is the start of the longer one ("baeckereikorn" and "baeckereikornpetermueller",
-- "muenster" and "muensterhiltrup"). In doubt a business counts as the one that objected.
create or replace function public.keys_match(p_a text, p_b text, p_min_length integer) returns boolean
language sql immutable parallel safe set search_path = pg_catalog, pg_temp as $$
  select p_a <> '' and p_b <> ''
     and (p_a = p_b
          or (least(length(p_a), length(p_b)) >= p_min_length
              and (left(p_a, length(p_b)) = p_b or left(p_b, length(p_a)) = p_a)))
$$;

-- Whether a business objected to advertising: the same name (keys_match, six characters at least) in
-- the same city, or anywhere when the entry or the business has no city; the same phone number; or the
-- same place id as an entry of advertising_objections.
create or replace function public.prospect_objected(p_name text, p_city text, p_phone text, p_place_id text)
returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select exists (
    select 1 from public.advertising_objections o
    where (public.keys_match(o.name_key, public.name_key(p_name), 6)
           and (o.city_key = '' or public.name_key(p_city) = ''
                or public.keys_match(o.city_key, public.name_key(p_city), 4)))
       or (o.phone_key <> '' and o.phone_key = public.phone_key(p_phone))
       or (p_place_id is not null and p_place_id = any(o.place_ids)))
$$;

-- Records the objection of a CRM customer with the place ids known for it and marks the listed
-- businesses that match.
create or replace function public.record_advertising_objection(p_customer uuid) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_name text;
  v_city text;
  v_phone text;
  v_places text[];
begin
  select c.name, c.city, c.phone into v_name, v_city, v_phone from crm.customers c where c.id = p_customer;
  if not found then
    return;
  end if;
  select coalesce(array_agg(distinct p.place_id), '{}') into v_places
  from (
    select r.place_id from public.prospect_results r where r.crm_customer_id = p_customer
    union
    select l.google_place_id from public.locations l join public.customers pc on pc.id = l.customer_id
    where pc.crm_customer_id = p_customer and l.google_place_id is not null
  ) p;
  insert into public.advertising_objections (crm_customer_id, name_key, city_key, phone_key, place_ids)
  values (p_customer, public.name_key(v_name), public.name_key(v_city), public.phone_key(v_phone), v_places)
  on conflict (crm_customer_id) where crm_customer_id is not null do update
  set name_key = excluded.name_key, city_key = excluded.city_key, phone_key = excluded.phone_key,
      place_ids = (select array(select distinct unnest(public.advertising_objections.place_ids || excluded.place_ids)));
  update public.prospect_results r set objected = true
  from public.prospect_searches s
  where s.id = r.search_id and not r.objected
    and public.prospect_objected(r.name, coalesce(nullif(r.city, ''), s.city), r.phone, r.place_id);
end $$;

create or replace function public.on_crm_customer_objection() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform public.record_advertising_objection(new.id);
  return null;
end $$;

-- The trigger and the objections already given need customers.no_follow_ups (CRM migration 010).
do $$
begin
  if exists (select 1 from information_schema.columns
             where table_schema = 'crm' and table_name = 'customers' and column_name = 'no_follow_ups') then
    execute 'drop trigger if exists radar_advertising_objection on crm.customers';
    execute 'create trigger radar_advertising_objection after insert or update of no_follow_ups on crm.customers '
            'for each row when (new.no_follow_ups) execute function public.on_crm_customer_objection()';
    perform public.record_advertising_objection(c.id) from crm.customers c where c.no_follow_ups;
  end if;
end $$;

-- A stored list younger than prospect_cache_hours() and a search already waiting are reused; a failed,
-- expired or older one is queued again under the same id.
create or replace function public.request_prospect_search(p_trade text, p_city text) returns uuid
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_trade text := regexp_replace(btrim(coalesce(p_trade, '')), '\s+', ' ', 'g');
  v_city text := regexp_replace(btrim(coalesce(p_city, '')), '\s+', ' ', 'g');
  v_key text;
  v_search public.prospect_searches;
begin
  if not public.is_admin() then
    raise exception 'Nur der Administrator darf das.' using errcode = '42501';
  end if;
  if length(v_trade) not between 2 and 80 then
    raise exception 'Bitte ein Gewerk mit 2 bis 80 Zeichen eingeben.' using errcode = '22023';
  end if;
  if length(v_city) not between 2 and 80 then
    raise exception 'Bitte eine Stadt mit 2 bis 80 Zeichen eingeben.' using errcode = '22023';
  end if;
  v_key := translate(lower(v_trade), 'ÄÖÜ', 'äöü') || '|' || translate(lower(v_city), 'ÄÖÜ', 'äöü');
  -- Serialises two requests for the same trade and city, which would otherwise both insert.
  perform pg_advisory_xact_lock(hashtext('prospect_search:' || v_key));
  select * into v_search from public.prospect_searches s where s.search_key = v_key;
  if not found then
    insert into public.prospect_searches (trade, city) values (v_trade, v_city)
    returning * into v_search;
    return v_search.id;
  end if;
  if v_search.status in ('queued', 'running')
     or (v_search.status = 'done'
         and v_search.fetched_at >= now() - make_interval(hours => public.prospect_cache_hours())) then
    return v_search.id;
  end if;
  update public.prospect_searches
  set status = 'queued', error = null, trade = v_trade, city = v_city, requested_at = now(),
      claimed_at = null
  where id = v_search.id;
  return v_search.id;
end $$;

-- Lists older than prospect_cache_hours() lose their Places fields: a business taken into the CRM or
-- marked as objected keeps its row with place id and mark only, every other row is deleted.
create or replace function public.purge_prospect_searches() returns integer
language plpgsql security invoker set search_path = public, pg_temp as $$
declare v_count integer;
begin
  update public.prospect_searches
  set status = 'expired', result_count = null
  where status = 'done' and fetched_at < now() - make_interval(hours => public.prospect_cache_hours());
  delete from public.prospect_results r using public.prospect_searches s
  where s.id = r.search_id and s.status = 'expired' and r.crm_customer_id is null and not r.objected;
  get diagnostics v_count = row_count;
  update public.prospect_results r
  set name = '', address = '', street = '', postal_code = '', city = '', phone = null, website = null,
      maps_url = null, rating = null, review_count = 0, photo_count = 0, has_opening_hours = false,
      score = 0, signals = '[]', reasons = '', contact_ways = '[]'
  from public.prospect_searches s
  where s.id = r.search_id and s.status = 'expired' and (r.name <> '' or r.reasons <> '');
  return v_count;
end $$;

-- The oldest waiting search, or one a stopped worker left running for ten minutes; purges first.
create or replace function public.claim_prospect_search() returns setof public.prospect_searches
language plpgsql security invoker set search_path = public, pg_temp as $$
begin
  perform public.purge_prospect_searches();
  return query
  update public.prospect_searches s
  set status = 'running', claimed_at = now()
  where s.id = (
    select q.id from public.prospect_searches q
    where q.status = 'queued' or (q.status = 'running' and q.claimed_at < now() - interval '10 minutes')
    order by q.requested_at
    limit 1
    for update skip locked)
  returning s.*;
end $$;

drop function if exists public.finish_prospect_search(uuid, jsonb);

-- Stores the ranked list of a claimed search in one transaction. A business already taken into the CRM
-- keeps that link; businesses no longer listed are removed; a business that objected is marked.
-- p_note says why the list is incomplete, for example when the daily cap ran out after the first page.
create or replace function public.finish_prospect_search(p_search uuid, p_results jsonb, p_note text default null)
returns integer
language plpgsql security invoker set search_path = public, pg_temp as $$
declare v_count integer;
begin
  if p_results is null or jsonb_typeof(p_results) <> 'array' then
    raise exception 'Ergebnisse müssen eine Liste sein.' using errcode = '22023';
  end if;
  perform 1 from public.prospect_searches where id = p_search and status = 'running' for update;
  if not found then
    raise exception 'Diese Suche ist nicht in Arbeit.' using errcode = '55000';
  end if;
  delete from public.prospect_results r
  where r.search_id = p_search
    and r.place_id not in (select e ->> 'place_id' from jsonb_array_elements(p_results) e);
  insert into public.prospect_results as r (
    search_id, rank, place_id, name, address, street, postal_code, city, phone, website, maps_url,
    rating, review_count, photo_count, has_opening_hours, score, signals, reasons, contact_ways, objected)
  select p_search, x.rank, x.place_id, x.name, coalesce(x.address, ''), coalesce(x.street, ''),
         coalesce(x.postal_code, ''), coalesce(x.city, ''), x.phone, x.website, x.maps_url, x.rating,
         x.review_count, x.photo_count, x.has_opening_hours, x.score, coalesce(x.signals, '[]'),
         x.reasons, coalesce(x.contact_ways, '[]'),
         public.prospect_objected(x.name, coalesce(nullif(x.city, ''), s.city), x.phone, x.place_id)
  from jsonb_to_recordset(p_results) as x(
    rank integer, place_id text, name text, address text, street text, postal_code text, city text,
    phone text, website text, maps_url text, rating numeric, review_count integer,
    photo_count integer, has_opening_hours boolean, score integer, signals jsonb, reasons text,
    contact_ways jsonb)
  cross join public.prospect_searches s
  where s.id = p_search
  on conflict (search_id, place_id) do update
  set rank = excluded.rank, name = excluded.name, address = excluded.address,
      street = excluded.street, postal_code = excluded.postal_code, city = excluded.city,
      phone = excluded.phone, website = excluded.website, maps_url = excluded.maps_url,
      rating = excluded.rating, review_count = excluded.review_count,
      photo_count = excluded.photo_count, has_opening_hours = excluded.has_opening_hours,
      score = excluded.score, signals = excluded.signals, reasons = excluded.reasons,
      contact_ways = excluded.contact_ways, objected = r.objected or excluded.objected;
  select count(*) into v_count from public.prospect_results where search_id = p_search;
  update public.prospect_searches
  set status = 'done', error = nullif(btrim(coalesce(p_note, '')), ''), fetched_at = now(),
      result_count = v_count
  where id = p_search;
  return v_count;
end $$;

create or replace function public.fail_prospect_search(p_search uuid, p_error text) returns void
language plpgsql security invoker set search_path = public, pg_temp as $$
begin
  update public.prospect_searches
  set status = 'failed', error = left(coalesce(nullif(btrim(p_error), ''), 'Die Suche ist gescheitert.'), 500)
  where id = p_search and status = 'running';
end $$;

drop function if exists public.add_prospect_to_crm(uuid);

-- Takes a result into the CRM with what Daniel copied from the public profile himself: the one CRM
-- customer of the same name in the same city, else a new one with source radarauditor, plus an open deal
-- unless one exists and a line in the customer file. A business that objected to advertising (same name
-- and city, phone number or place id) is refused before anything is written. Repeated calls return the
-- same id.
create or replace function public.add_prospect_to_crm(
  p_result uuid, p_name text, p_street text, p_postal_code text, p_city text, p_phone text
) returns uuid
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_result public.prospect_results;
  v_search public.prospect_searches;
  v_name text := regexp_replace(btrim(coalesce(p_name, '')), '\s+', ' ', 'g');
  v_city text;
  v_crm uuid;
  v_label text;
begin
  if not public.is_admin() then
    raise exception 'Nur der Administrator darf das.' using errcode = '42501';
  end if;
  select * into v_result from public.prospect_results where id = p_result for update;
  if not found then
    raise exception 'Diesen Treffer gibt es nicht mehr.' using errcode = 'P0002';
  end if;
  if v_result.crm_customer_id is not null then
    return v_result.crm_customer_id;
  end if;
  select * into v_search from public.prospect_searches where id = v_result.search_id;
  v_city := coalesce(nullif(btrim(coalesce(p_city, '')), ''), v_search.city);
  if length(v_name) not between 2 and 200 then
    raise exception 'Bitte den Namen des Betriebs eintragen.' using errcode = '22023';
  end if;
  -- Checked with what Daniel typed and with what the list stored, so another spelling slips through neither.
  if v_result.objected or public.prospect_objected(v_name, v_city, p_phone, v_result.place_id)
     or public.prospect_objected(v_result.name, coalesce(nullif(v_result.city, ''), v_search.city),
                                 v_result.phone, v_result.place_id) then
    update public.prospect_results set objected = true where id = p_result;
    raise exception 'Dieser Betrieb hat Werbung widersprochen; bitte nicht ansprechen.' using errcode = '55000';
  end if;
  v_label := '„' || v_search.trade || ' in ' || v_search.city || '“';
  select min(cc.id::text)::uuid into v_crm from crm.customers cc
  where public.name_key(cc.name) = public.name_key(v_name) and public.name_key(cc.city) = public.name_key(v_city)
  having count(*) = 1;
  if v_crm is null then
    -- Daniel took the business in himself; the Cockpit needs no "Neuer Kunde" notice for it.
    perform set_config('repocity.quiet_customer_notice', 'on', true);
    insert into crm.customers (name, phone, street, postal_code, city, source, notes)
    values (left(v_name, 200), btrim(coalesce(p_phone, '')), btrim(coalesce(p_street, '')),
            btrim(coalesce(p_postal_code, '')), v_city, 'radarauditor', 'Aus der Kundensuche ' || v_label || '.')
    returning id into v_crm;
    perform set_config('repocity.quiet_customer_notice', '', true);
  end if;
  if not exists (select 1 from crm.deals d where d.customer_id = v_crm and d.stage not in ('won', 'lost')) then
    insert into crm.deals (customer_id, title, stage, next_step)
    values (v_crm, left('Interessent aus der Kundensuche ' || v_label, 200), 'new',
            'Erstkontakt per Brief oder Besuch; keine Werbe-Mail, kein Anruf ohne konkreten Anlass.');
  end if;
  perform crm.log(v_crm, 'Als Interessent aus der Kundensuche ' || v_label || ' übernommen.');
  update public.prospect_results set crm_customer_id = v_crm where id = p_result;
  return v_crm;
end $$;

-- Monthly service -----------------------------------------------------------------------------------

-- One optimize job per reported monthly audit, on the order of the customer's active subscription;
-- null when the customer has no CRM link, no subscription that came from an accepted quote, or when
-- the first optimisation of that order is not finished yet, so its open drafts and its invoice are
-- never touched by a monthly run.
create or replace function public.queue_monthly_service(p_audit uuid) returns uuid
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_audit public.audits;
  v_crm uuid;
  v_order uuid;
  v_job uuid;
begin
  perform public.require_admin();
  select * into v_audit from public.audits where id = p_audit;
  if not found then
    raise exception 'Prüfung nicht gefunden.' using errcode = 'P0002';
  end if;
  if v_audit.purpose <> 'monthly' then
    raise exception 'Nur eine Monatsprüfung bekommt den Monatsservice.' using errcode = '22023';
  end if;
  select c.crm_customer_id into v_crm from public.customers c where c.id = v_audit.customer_id;
  if v_crm is null then
    return null;
  end if;
  select o.id into v_order
  from crm.subscriptions s join crm.orders o on o.quote_id = s.quote_id
  where s.customer_id = v_crm and s.active
  order by o.created_at desc
  limit 1;
  if v_order is null
     or not exists (select 1 from public.events e
                    where e.kind = 'optimization.finished' and e.payload ->> 'order_id' = v_order::text) then
    return null;
  end if;
  perform pg_advisory_xact_lock(hashtext('monthly_service:' || p_audit::text));
  select j.id into v_job from public.jobs j
  where j.audit_id = p_audit and j.order_id = v_order and j.kind::text = 'optimize'
  limit 1;
  if found then
    return v_job;
  end if;
  insert into public.jobs (kind, customer_id, audit_id, order_id)
  values ('optimize', v_audit.customer_id, p_audit, v_order)
  returning id into v_job;
  return v_job;
end $$;

drop function if exists public.prospect_cache_days();

revoke all on function public.prospect_cache_hours() from public, anon;
revoke all on function public.name_key(text) from public, anon;
revoke all on function public.keys_match(text, text, integer) from public, anon, authenticated;
revoke all on function public.phone_key(text) from public, anon;
revoke all on function public.prospect_objected(text, text, text, text) from public, anon, authenticated;
revoke all on function public.record_advertising_objection(uuid) from public, anon, authenticated;
revoke all on function public.on_crm_customer_objection() from public, anon, authenticated;
revoke all on function public.request_prospect_search(text, text) from public, anon;
revoke all on function public.purge_prospect_searches() from public, anon, authenticated;
revoke all on function public.claim_prospect_search() from public, anon, authenticated;
revoke all on function public.finish_prospect_search(uuid, jsonb, text) from public, anon, authenticated;
revoke all on function public.fail_prospect_search(uuid, text) from public, anon, authenticated;
revoke all on function public.add_prospect_to_crm(uuid, text, text, text, text, text) from public, anon;
revoke all on function public.queue_monthly_service(uuid) from public, anon, authenticated;
grant execute on function public.prospect_cache_hours() to authenticated, service_role;
grant execute on function public.name_key(text) to authenticated, service_role;
grant execute on function public.keys_match(text, text, integer) to service_role;
grant execute on function public.phone_key(text) to authenticated, service_role;
grant execute on function public.prospect_objected(text, text, text, text) to service_role;
grant execute on function public.record_advertising_objection(uuid) to service_role;
grant execute on function public.request_prospect_search(text, text) to authenticated;
grant execute on function public.purge_prospect_searches() to service_role;
grant execute on function public.claim_prospect_search() to service_role;
grant execute on function public.finish_prospect_search(uuid, jsonb, text) to service_role;
grant execute on function public.fail_prospect_search(uuid, text) to service_role;
grant execute on function public.add_prospect_to_crm(uuid, text, text, text, text, text) to authenticated;
grant execute on function public.queue_monthly_service(uuid) to service_role;

-- The database itself empties old lists every hour at minute 53, so the Places fields go after 24 hours
-- even while the worker is stopped (the worker purges as well before every claim). pg_cron comes with
-- CRM migration 003; the job is replaced on every run.
select cron.unschedule(jobid) from cron.job where jobname = 'radar-prospect-purge';
select cron.schedule('radar-prospect-purge', '53 * * * *', $job$ select public.purge_prospect_searches(); $job$);

-- Realtime: the app follows a running search and its results live -------------------------------------

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
  foreach v_name in array array['public.prospect_searches', 'public.prospect_results'] loop
    v_table := to_regclass(v_name);
    continue when v_table is null;
    if not exists (select 1 from pg_publication_rel where prpubid = v_publication and prrelid = v_table) then
      execute format('alter publication supabase_realtime add table %s', v_table);
    end if;
  end loop;
end $$;
