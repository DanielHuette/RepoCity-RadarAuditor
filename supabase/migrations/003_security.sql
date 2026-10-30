-- Row level security: one operator account; the app uses the anon key with a signed-in user, the worker the service key.
-- The service key bypasses row level security, so the worker needs no policies; uploads into storage happen only through it.
-- Every policy grants all authenticated users, so sign-ups must be disabled in the Supabase dashboard (Authentication, Sign In / Providers, "Allow new users to sign up" off).

alter table customers enable row level security;
alter table locations enable row level security;
alter table contacts enable row level security;
alter table competitors enable row level security;
alter table keywords enable row level security;
alter table customer_access enable row level security;
alter table audits enable row level security;
alter table check_results enable row level security;
alter table findings enable row level security;
alter table rankings enable row level security;
alter table jobs enable row level security;
alter table documents enable row level security;
alter table payments enable row level security;
alter table events enable row level security;
alter table settings enable row level security;
-- Without a policy only the worker can draw invoice numbers; gap-free numbering must not depend on the app.
alter table invoice_counters enable row level security;

create policy authenticated_all on customers for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on locations for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on contacts for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on competitors for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on keywords for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on customer_access for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on audits for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on check_results for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on findings for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on rankings for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on jobs for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on payments for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on events for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy authenticated_all on settings for all to authenticated
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

-- Documents are requested by the app and completed by the worker; issued documents never change.
create policy authenticated_select on documents for select to authenticated
  using (auth.role() = 'authenticated');
create policy authenticated_insert on documents for insert to authenticated
  with check (auth.role() = 'authenticated');

insert into storage.buckets (id, name, public) values
  ('raw', 'raw', false), ('reports', 'reports', false), ('documents', 'documents', false), ('exports', 'exports', false);

-- Reading is enough for the app: signed links and the export listing; the worker uploads with the service key.
create policy authenticated_read_radar_buckets on storage.objects for select to authenticated
  using (auth.role() = 'authenticated' and bucket_id in ('raw', 'reports', 'documents', 'exports'));
