-- A lead that reached the CRM before the field audit is linked instead of being created a second time.
-- Context: the web form, an unknown sender taken in by the mail worker and the phone assistant create CRM
-- customers without a city (crm.submit_inquiry, crm.intake_contact, crm.intake_call). link_crm_customer
-- from 013 matched name and city only, so the field audit of such a lead created a second CRM customer
-- without the e-mail address, and storing the address there was refused as taken. | Input: migrations
-- 001-014 and schema crm (customers). | Output: link_crm_customer that, after an exact match of name and
-- city, takes the one CRM customer of the same name without a city and gives it the city.
-- Every statement is guarded, so running the file twice changes nothing.

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
    select min(cc.id::text)::uuid into v_crm_customer from crm.customers cc
    where lower(btrim(cc.name)) = lower(btrim(v_customer.name)) and btrim(cc.city) = ''
    having count(*) = 1;
    update crm.customers set city = btrim(v_customer.city) where id = v_crm_customer;
  end if;
  if v_crm_customer is null then
    insert into crm.customers (name, city, source)
    values (left(btrim(v_customer.name), 200), btrim(v_customer.city), 'radarauditor')
    returning id into v_crm_customer;
  end if;
  update public.customers set crm_customer_id = v_crm_customer where id = v_customer.id;
  return v_crm_customer;
end $$;

revoke all on function public.link_crm_customer(uuid) from public, anon;
grant execute on function public.link_crm_customer(uuid) to authenticated, service_role;
