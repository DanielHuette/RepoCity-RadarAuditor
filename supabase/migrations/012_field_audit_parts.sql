-- start_field_audit split into short parts, and check ids of any area in the per-audit selection.
-- Context: 011 put the whole start of a field audit into one function; this migration keeps its
-- signature, rights and behaviour and hands each step to a part of its own: selection, customer,
-- location, correction, keywords, competitors, audit with collect job. The selection and the column
-- rule accept ids of every area a-z, so a new catalogue area needs no migration.
-- | Input: migrations 001-011 (011 for start_field_audit, link_crm_customer, audits.disabled_checks;
-- 006 for website_host, require_admin, schema crm). | Output: functions normalize_field_audit_selection,
-- save_field_audit_customer, place_field_audit_location, move_field_audit_keywords,
-- follow_field_audit_correction, add_field_audit_keywords, add_field_audit_competitors,
-- queue_field_audit, start_field_audit built from them, and the column rule audits_disabled_checks_ids.
-- Every statement is guarded, so running the file twice changes nothing. Every part runs as the
-- caller (row level security applies) and checks the administrator itself, because it can be called
-- on its own as well.

-- Only catalogue ids such as "a2" or "o13"; null elements are rejected as well.
alter table public.audits drop constraint if exists audits_disabled_checks_ids;
alter table public.audits add constraint audits_disabled_checks_ids
  check (cardinality(disabled_checks) = 0
         or array_to_string(disabled_checks, ',', '~') ~ '^[a-z]\d+(,[a-z]\d+)*$');

comment on column public.audits.disabled_checks is
  'Check ids switched off for this audit; empty means every check runs.';

-- Answers the selection sorted and without duplicates; rejects the first id that is no catalogue id.
create or replace function public.normalize_field_audit_selection(p_disabled_checks text[])
returns text[]
language plpgsql immutable security invoker set search_path = public, pg_temp as $$
declare
  v_invalid_check text;
begin
  select c.id into v_invalid_check
  from unnest(coalesce(p_disabled_checks, '{}')) with ordinality as c(id, n)
  where c.id is null or c.id !~ '^[a-z]\d+$'
  order by c.n
  limit 1;
  if found then
    raise exception 'Unbekannte Prüfung in der Auswahl: %', coalesce(v_invalid_check, 'leer')
      using errcode = '22023';
  end if;
  return coalesce(
    (select array_agg(distinct c.id order by c.id) from unnest(p_disabled_checks) as c(id)), '{}');
end $$;

-- Finds the customer of the website or creates one; given values (trimmed, '' when missing) override
-- stored ones. Answers the customer as stored now, its CRM link before this start, the name and city
-- before this start and whether the given values correct an identification.
create or replace function public.save_field_audit_customer(
  p_website text, p_host text, p_name text, p_city text, p_industry text,
  out customer_id uuid, out crm_customer_id uuid, out city text,
  out old_name text, out old_city text, out corrected boolean
)
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_customer public.customers;
begin
  perform public.require_admin();
  -- Serialises two starts for the same new website, which would otherwise both create a customer.
  perform pg_advisory_xact_lock(hashtext('field_audit:' || p_host));
  select * into v_customer from public.customers c
  where public.website_host(c.website) = p_host order by c.created_at limit 1;

  old_name := coalesce(btrim(v_customer.name), '');
  old_city := coalesce(btrim(v_customer.city), '');
  -- A correction from the Auswertung: a given name or city replaces a different one that was already
  -- identified (not the host placeholder or an empty city, which are a first fill).
  corrected := found and (
    (p_name <> '' and p_name <> old_name and lower(old_name) <> p_host)
    or (p_city <> '' and p_city <> old_city and old_city <> ''));
  if found then
    update public.customers c set
      name = coalesce(nullif(p_name, ''), c.name),
      city = coalesce(nullif(p_city, ''), c.city),
      industry = coalesce(nullif(p_industry, ''), c.industry)
    where c.id = v_customer.id
    returning c.* into v_customer;
  else
    -- Without a name the host stands in until the worker identifies the business.
    insert into public.customers as c (name, website, industry, city)
    values (
      coalesce(nullif(p_name, ''), p_host),
      case when btrim(p_website) ~* '^https?://' then btrim(p_website) else 'https://' || btrim(p_website) end,
      p_industry,
      p_city)
    returning c.* into v_customer;
  end if;
  customer_id := v_customer.id;
  crm_customer_id := v_customer.crm_customer_id;
  city := v_customer.city;
end $$;

-- Gives a new customer its location, resets a wrongly identified one on a correction, and otherwise
-- fills in a name or city that was still unknown. Name and city are the given values, trimmed.
create or replace function public.place_field_audit_location(
  p_customer_id uuid,
  p_host text,
  p_name text,
  p_city text,
  p_old_name text,
  p_old_city text,
  p_corrected boolean
) returns void
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_customer public.customers;
begin
  perform public.require_admin();
  select * into v_customer from public.customers c where c.id = p_customer_id;
  if not exists (select 1 from public.locations l where l.customer_id = p_customer_id) then
    insert into public.locations (customer_id, name, street, postal_code, city, phone)
    values (p_customer_id, v_customer.name, '', '', v_customer.city, '');
  elsif p_corrected then
    -- The wrongly identified location takes the corrected name and city and forgets the address,
    -- phone, Google place and position of the wrong business, so the next run looks it up anew.
    update public.locations l set
      name = coalesce(nullif(p_name, ''), l.name),
      city = coalesce(nullif(p_city, ''), l.city),
      street = '',
      postal_code = '',
      phone = '',
      google_place_id = null,
      latitude = null,
      longitude = null
    where l.customer_id = p_customer_id
      and (lower(btrim(l.name)) = lower(p_old_name) or btrim(l.city) = p_old_city);
  else
    update public.locations l set
      name = case when p_name <> '' and lower(btrim(l.name)) = p_host then p_name else l.name end,
      city = case when btrim(l.city) = '' then v_customer.city else l.city end
    where l.customer_id = p_customer_id
      and ((p_name <> '' and lower(btrim(l.name)) = p_host) or btrim(l.city) = '');
  end if;
end $$;

-- Moves the customer's keywords of one city (compared trimmed) to another, unless the phrase exists
-- there already. Moving to an unknown city does nothing.
create or replace function public.move_field_audit_keywords(
  p_customer_id uuid,
  p_from_city text,
  p_to_city text
) returns void
language plpgsql security invoker set search_path = public, pg_temp as $$
begin
  perform public.require_admin();
  if btrim(coalesce(p_to_city, '')) = '' then
    return;
  end if;
  update public.keywords k set city = p_to_city
  where k.customer_id = p_customer_id and btrim(k.city) = p_from_city
    and not exists (
      select 1 from public.keywords o
      where o.customer_id = k.customer_id and o.phrase = k.phrase and o.city = p_to_city);
end $$;

-- After a correction: a corrected city takes the keywords of the old city along, and the CRM customer
-- created from the wrong identification follows where nobody changed it. p_crm_customer_id is the
-- link before this start; a CRM customer linked just now already carries the corrected values.
create or replace function public.follow_field_audit_correction(
  p_customer_id uuid,
  p_crm_customer_id uuid,
  p_city text,
  p_old_name text,
  p_old_city text
) returns void
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_customer public.customers;
  v_city_changed boolean := p_city <> '' and p_city <> p_old_city;
begin
  perform public.require_admin();
  select * into v_customer from public.customers c where c.id = p_customer_id;
  if v_city_changed then
    perform public.move_field_audit_keywords(p_customer_id, p_old_city, p_city);
  end if;
  if p_crm_customer_id is null then
    return;
  end if;
  update crm.customers cc set
    name = case when btrim(cc.name) = p_old_name then left(btrim(v_customer.name), 200) else cc.name end,
    city = case when v_city_changed and btrim(cc.city) = p_old_city then btrim(v_customer.city)
                else cc.city end
  where cc.id = p_crm_customer_id;
end $$;

-- Adds the given phrases (trimmed, empty ones skipped) for the city; the first new one becomes the
-- primary keyword when the customer has none yet.
create or replace function public.add_field_audit_keywords(
  p_customer_id uuid,
  p_city text,
  p_keywords text[]
) returns void
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_phrase text;
  v_has_primary boolean;
begin
  perform public.require_admin();
  select exists (select 1 from public.keywords k where k.customer_id = p_customer_id and k.is_primary)
  into v_has_primary;
  foreach v_phrase in array coalesce(p_keywords, '{}') loop
    v_phrase := btrim(v_phrase);
    continue when v_phrase is null or v_phrase = '';
    insert into public.keywords (customer_id, phrase, city, is_primary)
    values (p_customer_id, v_phrase, p_city, not v_has_primary)
    on conflict (customer_id, phrase, city) do nothing;
    v_has_primary := v_has_primary or found;
  end loop;
end $$;

-- Adds the competitors of a JSON list of {name, website, google_place_id} that the customer does not
-- have yet (name compared without case); a website without scheme gets https.
create or replace function public.add_field_audit_competitors(
  p_customer_id uuid,
  p_competitors jsonb
) returns void
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_competitor jsonb;
  v_name text;
  v_site text;
begin
  perform public.require_admin();
  for v_competitor in select value from jsonb_array_elements(coalesce(p_competitors, '[]')) loop
    v_name := btrim(coalesce(v_competitor ->> 'name', ''));
    continue when v_name = '';
    v_site := nullif(btrim(coalesce(v_competitor ->> 'website', '')), '');
    if v_site is not null and v_site !~* '^https?://' then
      v_site := 'https://' || v_site;
    end if;
    if not exists (
      select 1 from public.competitors c
      where c.customer_id = p_customer_id and lower(c.name) = lower(v_name)
    ) then
      insert into public.competitors (customer_id, name, website, google_place_id)
      values (p_customer_id, v_name, v_site,
              nullif(btrim(coalesce(v_competitor ->> 'google_place_id', '')), ''));
    end if;
  end loop;
end $$;

-- Creates the remote audit with its selection and the collect job that starts it.
create or replace function public.queue_field_audit(p_customer_id uuid, p_disabled_checks text[])
returns uuid
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_audit uuid;
begin
  perform public.require_admin();
  insert into public.audits (customer_id, kind, disabled_checks)
  values (p_customer_id, 'remote', p_disabled_checks)
  returning id into v_audit;
  insert into public.jobs (kind, customer_id, audit_id) values ('collect', p_customer_id, v_audit);
  return v_audit;
end $$;

create or replace function public.start_field_audit(
  p_website text,
  p_name text default null,
  p_city text default null,
  p_industry text default null,
  p_keywords text[] default '{}',
  p_competitors jsonb default '[]',
  p_disabled_checks text[] default '{}'
) returns uuid
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  v_host text := public.website_host(p_website);
  v_name text := btrim(coalesce(p_name, ''));
  v_city text := btrim(coalesce(p_city, ''));
  v_disabled_checks text[];
  v_saved record;
begin
  perform public.require_admin();
  if v_host is null then
    raise exception 'Website fehlt.' using errcode = '22023';
  end if;
  if p_competitors is not null and jsonb_typeof(p_competitors) <> 'array' then
    raise exception 'Wettbewerber müssen eine Liste sein.' using errcode = '22023';
  end if;
  v_disabled_checks := public.normalize_field_audit_selection(p_disabled_checks);

  select * into v_saved from public.save_field_audit_customer(
    p_website, v_host, v_name, v_city, btrim(coalesce(p_industry, '')));
  perform public.link_crm_customer(v_saved.customer_id);
  perform public.place_field_audit_location(
    v_saved.customer_id, v_host, v_name, v_city, v_saved.old_name, v_saved.old_city, v_saved.corrected);
  if v_saved.corrected then
    perform public.follow_field_audit_correction(
      v_saved.customer_id, v_saved.crm_customer_id, v_city, v_saved.old_name, v_saved.old_city);
  end if;
  -- Keywords stored while the city was unknown get it as soon as it is known.
  perform public.move_field_audit_keywords(v_saved.customer_id, '', v_saved.city);
  perform public.add_field_audit_keywords(v_saved.customer_id, v_saved.city, p_keywords);
  perform public.add_field_audit_competitors(v_saved.customer_id, p_competitors);
  return public.queue_field_audit(v_saved.customer_id, v_disabled_checks);
end $$;

revoke all on function public.normalize_field_audit_selection(text[]) from public, anon;
revoke all on function public.save_field_audit_customer(text, text, text, text, text) from public, anon;
revoke all on function public.place_field_audit_location(uuid, text, text, text, text, text, boolean) from public, anon;
revoke all on function public.move_field_audit_keywords(uuid, text, text) from public, anon;
revoke all on function public.follow_field_audit_correction(uuid, uuid, text, text, text) from public, anon;
revoke all on function public.add_field_audit_keywords(uuid, text, text[]) from public, anon;
revoke all on function public.add_field_audit_competitors(uuid, jsonb) from public, anon;
revoke all on function public.queue_field_audit(uuid, text[]) from public, anon;
revoke all on function public.start_field_audit(text, text, text, text, text[], jsonb, text[]) from public, anon;
grant execute on function public.normalize_field_audit_selection(text[]) to authenticated, service_role;
grant execute on function public.save_field_audit_customer(text, text, text, text, text) to authenticated, service_role;
grant execute on function public.place_field_audit_location(uuid, text, text, text, text, text, boolean) to authenticated, service_role;
grant execute on function public.move_field_audit_keywords(uuid, text, text) to authenticated, service_role;
grant execute on function public.follow_field_audit_correction(uuid, uuid, text, text, text) to authenticated, service_role;
grant execute on function public.add_field_audit_keywords(uuid, text, text[]) to authenticated, service_role;
grant execute on function public.add_field_audit_competitors(uuid, jsonb) to authenticated, service_role;
grant execute on function public.queue_field_audit(uuid, text[]) to authenticated, service_role;
grant execute on function public.start_field_audit(text, text, text, text, text[], jsonb, text[]) to authenticated, service_role;
