-- RadarAuditor schema. Context: all tables used by app, worker and hand-off.
-- Input: none. Output: tables, enums, storage buckets.

create type customer_stage as enum ('prospect','offered','customer','paused','ended');
create type audit_kind as enum ('remote','with_access');
create type audit_status as enum ('requested','collecting','collected','scored','awaiting_analysis','analysed','reported','failed');
create type check_outcome as enum ('passed','partial','failed','not_measurable');
create type job_kind as enum ('collect','score','analyse','report','monthly');
create type job_status as enum ('queued','running','done','failed');
create type document_kind as enum ('quote','service_description','contract','invoice','credit_note','proof_of_service');

create table customers (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  website text,
  industry text not null,
  city text not null,
  stage customer_stage not null default 'prospect',
  order_value_eur numeric(10,2),
  created_at timestamptz not null default now()
);

create table locations (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references customers(id) on delete cascade,
  name text not null,
  street text not null,
  postal_code text not null,
  city text not null,
  phone text not null,
  google_place_id text,
  latitude double precision,
  longitude double precision
);

create table contacts (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references customers(id) on delete cascade,
  name text not null,
  role text not null default '',
  email text,
  phone text
);

create table competitors (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references customers(id) on delete cascade,
  name text not null,
  google_place_id text,
  website text
);

create table keywords (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references customers(id) on delete cascade,
  phrase text not null,
  city text not null,
  monthly_volume integer,
  is_primary boolean not null default false,
  unique (customer_id, phrase, city)
);

create table customer_access (
  customer_id uuid primary key references customers(id) on delete cascade,
  google_business_profile_granted boolean not null default false,
  search_console_property text,
  wordpress_url text,
  wordpress_user text,
  wordpress_app_password_encrypted text,
  granted_at timestamptz
);

create table audits (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references customers(id) on delete cascade,
  kind audit_kind not null default 'remote',
  status audit_status not null default 'requested',
  requested_at timestamptz not null default now(),
  finished_at timestamptz,
  total_score numeric(5,2),
  raw_data_path text,
  report_path text,
  error text
);

create table check_results (
  id uuid primary key default gen_random_uuid(),
  audit_id uuid not null references audits(id) on delete cascade,
  check_id text not null,
  area text not null,
  outcome check_outcome not null,
  measured jsonb not null default '{}',
  weight numeric(6,3) not null,
  unique (audit_id, check_id)
);

create table findings (
  id uuid primary key default gen_random_uuid(),
  audit_id uuid not null references audits(id) on delete cascade,
  check_id text not null,
  severity smallint not null check (severity between 1 and 3),
  title text not null,
  explanation text not null,
  customer_impact text not null,
  fix text not null,
  fix_payload jsonb,
  unique (audit_id, check_id)
);

create table rankings (
  id uuid primary key default gen_random_uuid(),
  audit_id uuid not null references audits(id) on delete cascade,
  keyword_id uuid not null references keywords(id) on delete cascade,
  subject text not null,
  organic_position smallint,
  local_pack_position smallint,
  grid_point text,
  measured_at timestamptz not null default now()
);

create table jobs (
  id uuid primary key default gen_random_uuid(),
  kind job_kind not null,
  status job_status not null default 'queued',
  audit_id uuid references audits(id) on delete cascade,
  customer_id uuid not null references customers(id) on delete cascade,
  attempts smallint not null default 0,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index jobs_queue on jobs (kind, status, created_at);

create table documents (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references customers(id),
  kind document_kind not null,
  number text unique,
  issued_on date,
  net_amount numeric(10,2),
  vat_rate numeric(4,2),
  gross_amount numeric(10,2),
  data jsonb not null,
  file_path text,
  cancels_document_id uuid references documents(id),
  created_at timestamptz not null default now()
);

create table payments (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references documents(id),
  received_on date not null,
  amount numeric(10,2) not null,
  note text
);

create table invoice_counters (
  year integer primary key,
  last_number integer not null default 0
);

create table events (
  id uuid primary key default gen_random_uuid(),
  kind text not null,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  delivered_at timestamptz
);

create table settings (
  key text primary key,
  value jsonb not null
);
