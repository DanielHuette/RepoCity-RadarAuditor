-- Audits started with only the website address, each with its own selection of checks.
-- Context: the field app may send just the website; name, city and industry are then identified by
-- the worker at the start of the collect job (JSON-LD, Impressum, footer, Google profile) and written
-- back to customers, locations and keywords. A customer created this way carries the website host as
-- provisional name and an empty city and industry until then; the columns stay NOT NULL, an empty
-- text is the marker the worker looks for. Each audit stores the checks switched off for it; the
-- worker uses only that selection, so an empty one runs every check.
-- | Input: migrations 001-010 (006 for start_field_audit, website_host, require_admin, schema crm). |
-- Output: column audits.disabled_checks with its check, functions link_crm_customer and the new
-- start_field_audit (seven arguments; the six-argument version of 006 is dropped).
-- Every statement is guarded, so running the file twice changes nothing.

alter table public.audits add column if not exists disabled_checks text[] not null default '{}';

-- Only catalogue ids such as "a2" or "n13"; null elements are rejected as well.
alter table public.audits drop constraint if exists audits_disabled_checks_ids;
alter table public.audits add constraint audits_disabled_checks_ids
  check (cardinality(disabled_checks) = 0
         or array_to_string(disabled_checks, ',', '~') ~ '^[a-n]\d+(,[a-n]\d+)*$');

comment on column public.audits.disabled_checks is
  'Check ids switched off for this audit; empty means every check runs.';

-- Links a customer to the CRM once name and city are known: an existing CRM customer when name and
-- city identify exactly one, else a new one. Answers the CRM customer id, null while the city is empty
-- or the name is still the website host.
-- Called by start_field_audit and by the worker after it identified the city.
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
    values (left(btrim(v_customer.name), 200), btrim(v_customer.city), 'manual')
    returning id into v_crm_customer;
  end if;
  update public.customers set crm_customer_id = v_crm_customer where id = v_customer.id;
  return v_crm_customer;
end $$;

drop function if exists public.start_field_audit(text, text, text, text, text[], jsonb);

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
  v_industry text := btrim(coalesce(p_industry, ''));
  v_invalid_check text;
  v_disabled_checks text[];
  v_customer public.customers;
  v_old_name text := '';
  v_old_city text := '';
  v_corrected boolean := false;
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
  if p_competitors is not null and jsonb_typeof(p_competitors) <> 'array' then
    raise exception 'Wettbewerber müssen eine Liste sein.' using errcode = '22023';
  end if;
  select c.id into v_invalid_check
  from unnest(coalesce(p_disabled_checks, '{}')) with ordinality as c(id, n)
  where c.id is null or c.id !~ '^[a-n]\d+$'
  order by c.n
  limit 1;
  if found then
    raise exception 'Unbekannte Prüfung in der Auswahl: %', coalesce(v_invalid_check, 'leer')
      using errcode = '22023';
  end if;
  v_disabled_checks := coalesce(
    (select array_agg(distinct c.id order by c.id) from unnest(p_disabled_checks) as c(id)), '{}');

  -- Serialises two starts for the same new website, which would otherwise both create a customer.
  perform pg_advisory_xact_lock(hashtext('field_audit:' || v_host));

  select * into v_customer from public.customers c
  where public.website_host(c.website) = v_host
  order by c.created_at
  limit 1;

  if found then
    v_old_name := btrim(v_customer.name);
    v_old_city := btrim(v_customer.city);
    -- A correction from the Auswertung: a given name or city replaces a different one that was
    -- already identified (not the host placeholder or an empty city, which are a first fill).
    v_corrected :=
      (v_name <> '' and v_name <> v_old_name and lower(v_old_name) <> v_host)
      or (v_city <> '' and v_city <> v_old_city and v_old_city <> '');
    -- Given values override stored ones; missing ones keep what is stored.
    update public.customers set
      name = coalesce(nullif(v_name, ''), name),
      city = coalesce(nullif(v_city, ''), city),
      industry = coalesce(nullif(v_industry, ''), industry)
    where id = v_customer.id
    returning * into v_customer;
  else
    -- Without a name the host stands in until the worker identifies the business.
    insert into public.customers (name, website, industry, city)
    values (
      coalesce(nullif(v_name, ''), v_host),
      case when btrim(p_website) ~* '^https?://' then btrim(p_website) else 'https://' || btrim(p_website) end,
      v_industry,
      v_city)
    returning * into v_customer;
  end if;

  perform public.link_crm_customer(v_customer.id);

  if not exists (select 1 from public.locations l where l.customer_id = v_customer.id) then
    insert into public.locations (customer_id, name, street, postal_code, city, phone)
    values (v_customer.id, v_customer.name, '', '', v_customer.city, '');
  elsif v_corrected then
    -- The wrongly identified location takes the corrected name and city and forgets the address,
    -- phone, Google place and position of the wrong business, so the next run looks it up anew.
    update public.locations l set
      name = coalesce(nullif(v_name, ''), l.name),
      city = coalesce(nullif(v_city, ''), l.city),
      street = '',
      postal_code = '',
      phone = '',
      google_place_id = null,
      latitude = null,
      longitude = null
    where l.customer_id = v_customer.id
      and (lower(btrim(l.name)) = lower(v_old_name) or btrim(l.city) = v_old_city);
  else
    update public.locations l set
      name = case when v_name <> '' and lower(btrim(l.name)) = v_host then v_name else l.name end,
      city = case when btrim(l.city) = '' then v_customer.city else l.city end
    where l.customer_id = v_customer.id
      and ((v_name <> '' and lower(btrim(l.name)) = v_host) or btrim(l.city) = '');
  end if;

  -- A corrected city moves the keywords of the old city along, unless the phrase exists there.
  if v_corrected and v_city <> '' and v_city <> v_old_city then
    update public.keywords k set city = v_city
    where k.customer_id = v_customer.id and btrim(k.city) = v_old_city
      and not exists (
        select 1 from public.keywords o
        where o.customer_id = k.customer_id and o.phrase = k.phrase and o.city = v_city);
    -- The CRM customer created from the wrong identification follows, where nobody changed it.
    if v_customer.crm_customer_id is not null then
      update crm.customers cc set
        name = case when btrim(cc.name) = v_old_name then left(btrim(v_customer.name), 200) else cc.name end,
        city = case when btrim(cc.city) = v_old_city then btrim(v_customer.city) else cc.city end
      where cc.id = v_customer.crm_customer_id;
    end if;
  elsif v_corrected and v_customer.crm_customer_id is not null then
    update crm.customers cc set
      name = case when btrim(cc.name) = v_old_name then left(btrim(v_customer.name), 200) else cc.name end
    where cc.id = v_customer.crm_customer_id;
  end if;

  -- Keywords stored while the city was unknown get it as soon as it is known.
  if btrim(v_customer.city) <> '' then
    update public.keywords k set city = v_customer.city
    where k.customer_id = v_customer.id and btrim(k.city) = ''
      and not exists (
        select 1 from public.keywords o
        where o.customer_id = k.customer_id and o.phrase = k.phrase and o.city = v_customer.city);
  end if;

  select exists (select 1 from public.keywords k where k.customer_id = v_customer.id and k.is_primary)
  into v_has_primary;
  foreach v_phrase in array coalesce(p_keywords, '{}') loop
    v_phrase := btrim(v_phrase);
    continue when v_phrase is null or v_phrase = '';
    insert into public.keywords (customer_id, phrase, city, is_primary)
    values (v_customer.id, v_phrase, v_customer.city, not v_has_primary)
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

  insert into public.audits (customer_id, kind, disabled_checks)
  values (v_customer.id, 'remote', v_disabled_checks)
  returning id into v_audit;
  insert into public.jobs (kind, customer_id, audit_id) values ('collect', v_customer.id, v_audit);
  return v_audit;
end $$;

revoke all on function public.link_crm_customer(uuid) from public, anon;
revoke all on function public.start_field_audit(text, text, text, text, text[], jsonb, text[]) from public, anon;
grant execute on function public.link_crm_customer(uuid) to authenticated, service_role;
grant execute on function public.start_field_audit(text, text, text, text, text[], jsonb, text[]) to authenticated, service_role;
