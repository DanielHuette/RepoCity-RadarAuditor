-- Identifiers the worker needs to read the customer's own Google data with the operator's authorisation.
-- google_location_name: Business Profile location as "locations/<id>"; analytics_property_id: numeric GA4 property id.

alter table customer_access add column if not exists google_location_name text;
alter table customer_access add column if not exists analytics_property_id text;

alter table customer_access drop constraint if exists customer_access_google_location_name_format;
alter table customer_access add constraint customer_access_google_location_name_format
  check (google_location_name is null or google_location_name ~ '^locations/[0-9]+$');

alter table customer_access drop constraint if exists customer_access_analytics_property_id_format;
alter table customer_access add constraint customer_access_analytics_property_id_format
  check (analytics_property_id is null or analytics_property_id ~ '^[0-9]+$');
