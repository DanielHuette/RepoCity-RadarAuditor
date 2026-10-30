"""Build the RadarAuditor database the way the Supabase project has it.

Context: A Supabase stand-in, the RadarAuditor migrations in order and the CRM tables between 005
and 006, plus one administrator and one reader. | Input: supabase/migrations and the stubs next to
this module. | Output: Callers, migration lists, project builder and a schema fingerprint.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from db.postgres import Caller, Database

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_HERE = Path(__file__).resolve().parent
_SUPABASE_STUB = _HERE / "supabase_stub.sql"
_CRM_STUB = _HERE / "crm_stub.sql"
_FIRST_MIGRATION_ON_CRM = "006"

ADMIN = Caller("authenticated", UUID("00000000-0000-4000-8000-00000000000a"))
READER = Caller("authenticated", UUID("00000000-0000-4000-8000-00000000000b"))
ANON = Caller("anon")

_USERS_SQL = """
insert into auth.users (id, email)
values (:'admin', 'admin@example.test'), (:'reader', 'reader@example.test');
insert into public.app_admins (user_id) values (:'admin');
"""

_FINGERPRINT_SQL = """
select json_build_object(
  'functions', (
    select json_agg(
      p.oid::regprocedure::text || pg_get_functiondef(p.oid) || coalesce(p.proacl::text, '')
      order by p.oid::regprocedure::text)
    from pg_proc p where p.pronamespace in ('public'::regnamespace, 'crm'::regnamespace)),
  'constraints', (
    select json_agg(c.conrelid::regclass::text || c.conname || pg_get_constraintdef(c.oid)
                    order by c.conrelid::regclass::text, c.conname)
    from pg_constraint c where c.connamespace in ('public'::regnamespace, 'crm'::regnamespace)),
  'columns', (
    select json_agg(concat_ws(' ', table_schema, table_name, column_name, data_type, is_nullable,
                              column_default, col_description(
                                (table_schema || '.' || table_name)::regclass, ordinal_position))
                    order by table_schema, table_name, ordinal_position)
    from information_schema.columns where table_schema in ('public', 'crm')),
  'policies', (
    select json_agg(concat_ws(' ', schemaname, tablename, policyname, cmd, qual, with_check)
                    order by schemaname, tablename, policyname)
    from pg_policies),
  'grants', (
    select json_agg(c.oid::regclass::text || coalesce(c.relacl::text, '')
                    order by c.oid::regclass::text)
    from pg_class c where c.relnamespace in ('public'::regnamespace, 'crm'::regnamespace)
      and c.relkind = 'r'),
  'rows', (
    select json_build_object(
      'customers', (select count(*) from public.customers),
      'audits', (select count(*) from public.audits),
      'jobs', (select count(*) from public.jobs),
      'crm_customers', (select count(*) from crm.customers)))
)
"""


def migrations_through(last: str) -> list[Path]:
    """RadarAuditor migrations in order, up to and including the one numbered last (e.g. "011")."""
    return sorted(path for path in MIGRATIONS.glob("*.sql") if path.name[:3] <= last)


def migrations_after(first_excluded: str, last: str) -> list[Path]:
    """RadarAuditor migrations numbered above first_excluded up to and including last, in order."""
    return [path for path in migrations_through(last) if path.name[:3] > first_excluded]


def migration(number: str) -> Path:
    """The migration file numbered number.

    Raises:
        FileNotFoundError: If there is none.
    """
    found = sorted(MIGRATIONS.glob(f"{number}_*.sql"))
    if len(found) != 1:
        raise FileNotFoundError(f"No single migration {number} in {MIGRATIONS}")
    return found[0]


def build_project(database: Database, migrations: Sequence[Path]) -> None:
    """Set up the Supabase stand-in, apply the migrations with the CRM in between, add the users.

    Args:
        database: Empty database.
        migrations: RadarAuditor migrations in the order to apply them.
    """
    database.run_file(_SUPABASE_STUB)
    crm_applied = False
    for path in migrations:
        if not crm_applied and path.name[:3] >= _FIRST_MIGRATION_ON_CRM:
            database.run_file(_CRM_STUB)
            crm_applied = True
        database.run_file(path)
    database.run(_USERS_SQL, admin=str(ADMIN.user_id), reader=str(READER.user_id))


def schema_fingerprint(database: Database) -> Any:
    """Everything a migration may change: functions with rights, rules, columns, policies, rows."""
    return database.value(_FINGERPRINT_SQL)
