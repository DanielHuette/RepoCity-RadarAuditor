"""Migrations 011, 012 and 014 against a real PostgreSQL.

Context: Text checks cannot prove behaviour, so the migrations run in a throwaway cluster with a
Supabase stand-in. Every start_field_audit test runs once on 001-011 and once on 001-012; the
removal of billing (014) runs on top of everything before it, including 013 once it exists. Skipped
when no PostgreSQL binaries are found (set RADAR_PG_BIN to their directory). |
Input: supabase/migrations. | Output: Assertions.
"""

import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
from db.postgres import (
    BIN_DIR_VARIABLE,
    Database,
    DatabaseError,
    PostgresCluster,
    find_bin_dir,
    pg_array,
    runs_as_root,
)
from db.supabase import (
    ADMIN,
    ANON,
    READER,
    build_project,
    migration,
    migrations_after,
    migrations_through,
    schema_fingerprint,
)

BIN_DIR = find_bin_dir()
TEMPLATES = {"011": "radar_through_011", "012": "radar_through_012", "014": "radar_through_014"}
PARTS_OF_012 = [
    "normalize_field_audit_selection(text[])",
    "save_field_audit_customer(text,text,text,text,text)",
    "place_field_audit_location(uuid,text,text,text,text,text,boolean)",
    "move_field_audit_keywords(uuid,text,text)",
    "follow_field_audit_correction(uuid,uuid,text,text,text)",
    "add_field_audit_keywords(uuid,text,text[])",
    "add_field_audit_competitors(uuid,jsonb)",
    "queue_field_audit(uuid,text[])",
]
START = "public.start_field_audit(text,text,text,text,text[],jsonb,text[])"

pytestmark = [
    pytest.mark.skipif(
        BIN_DIR is None, reason=f"No PostgreSQL binaries; set {BIN_DIR_VARIABLE} to their directory"
    ),
    pytest.mark.skipif(runs_as_root(), reason="PostgreSQL refuses to run as root"),
]


@pytest.fixture(scope="module")
def cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PostgresCluster]:
    assert BIN_DIR is not None
    with PostgresCluster(BIN_DIR, tmp_path_factory.mktemp("postgres")) as running:
        yield running


@pytest.fixture(scope="module")
def templates(cluster: PostgresCluster) -> dict[str, str]:
    """Databases through 011 and through 012, copied for every test."""
    build_project(cluster.create_database(TEMPLATES["011"]), migrations_through("011"))
    through_012 = cluster.create_database(TEMPLATES["012"], template=TEMPLATES["011"])
    through_012.run_file(migration("012"))
    through_014 = cluster.create_database(TEMPLATES["014"], template=TEMPLATES["012"])
    for path in migrations_after("012", "014"):
        through_014.run_file(path)
    return TEMPLATES


def _copy(cluster: PostgresCluster, template: str) -> Iterator[Database]:
    database = cluster.create_database(f"test_{uuid4().hex}", template=template)
    try:
        yield database
    finally:
        cluster.drop_database(database.name)


@pytest.fixture(params=["011", "012"])
def database(
    request: pytest.FixtureRequest, cluster: PostgresCluster, templates: dict[str, str]
) -> Iterator[Database]:
    """A fresh copy of the project after 011 and after 012."""
    yield from _copy(cluster, templates[request.param])


@pytest.fixture
def database_011(cluster: PostgresCluster, templates: dict[str, str]) -> Iterator[Database]:
    yield from _copy(cluster, templates["011"])


@pytest.fixture
def database_012(cluster: PostgresCluster, templates: dict[str, str]) -> Iterator[Database]:
    yield from _copy(cluster, templates["012"])


@pytest.fixture
def database_014(cluster: PostgresCluster, templates: dict[str, str]) -> Iterator[Database]:
    yield from _copy(cluster, templates["014"])


def _start(database: Database, website: str, **arguments: str) -> str:
    """Start an audit as the administrator; arguments are SQL expressions of named parameters."""
    names = "".join(f", {name} => {expression}" for name, expression in arguments.items())
    audit: str = database.value(
        f"select public.start_field_audit(:'website'{names})", ADMIN, website=website
    )
    return audit


def _audit(database: Database, audit_id: str) -> dict[str, object]:
    rows = database.rows(
        "select a.customer_id, a.kind, a.status, a.disabled_checks, "
        "(select json_agg(j.kind) from public.jobs j where j.audit_id = a.id) as jobs "
        "from public.audits a where a.id = :'audit'",
        audit=audit_id,
    )
    assert len(rows) == 1
    record: dict[str, object] = rows[0]
    return record


def test_only_the_address_creates_customer_location_audit_and_collect_job(
    database: Database,
) -> None:
    audit = _audit(database, _start(database, "Example-Baeckerei.de/kontakt"))

    assert audit["kind"] == "remote"
    assert audit["status"] == "requested"
    assert audit["disabled_checks"] == []
    assert audit["jobs"] == ["collect"]
    customers = database.rows(
        "select id, name, website, city, industry, crm_customer_id from public.customers"
    )
    assert customers == [
        {
            "id": audit["customer_id"],
            "name": "example-baeckerei.de",
            "website": "https://Example-Baeckerei.de/kontakt",
            "city": "",
            "industry": "",
            "crm_customer_id": None,
        }
    ]
    locations = database.rows("select name, street, postal_code, city, phone from public.locations")
    assert locations == [
        {"name": "example-baeckerei.de", "street": "", "postal_code": "", "city": "", "phone": ""}
    ]
    assert database.value("select count(*) from crm.customers") == 0


def test_a_second_start_fills_in_name_city_and_waiting_keywords(database: Database) -> None:
    first = _audit(database, _start(database, "example.de", p_keywords="array['Brot']"))
    second = _audit(
        database,
        _start(
            database,
            "https://www.example.de",
            p_name="'Baeckerei Korn'",
            p_city="'Muenster'",
            p_industry="'Baeckerei'",
            p_keywords="array['Brot', ' Kuchen ', '']",
        ),
    )

    assert second["customer_id"] == first["customer_id"]
    assert database.rows("select name, city, industry from public.customers") == [
        {"name": "Baeckerei Korn", "city": "Muenster", "industry": "Baeckerei"}
    ]
    assert database.rows("select name, city from public.locations") == [
        {"name": "Baeckerei Korn", "city": "Muenster"}
    ]
    assert database.rows(
        "select phrase, city, is_primary from public.keywords order by phrase"
    ) == [
        {"phrase": "Brot", "city": "Muenster", "is_primary": True},
        {"phrase": "Kuchen", "city": "Muenster", "is_primary": False},
    ]
    assert database.rows(
        "select cc.name, cc.city, cc.source from crm.customers cc "
        "join public.customers c on c.crm_customer_id = cc.id"
    ) == [{"name": "Baeckerei Korn", "city": "Muenster", "source": "manual"}]


def test_competitors_are_added_once_with_a_full_address(database: Database) -> None:
    competitors = [
        {"name": "Konkurrenz", "website": "konkurrenz.de", "google_place_id": " place-1 "},
        {"name": "KONKURRENZ"},
        {"name": "  "},
        {"name": "Andere", "website": "http://andere.de"},
    ]
    _start(database, "example.de", p_competitors=f"'{json.dumps(competitors)}'::jsonb")

    assert database.rows(
        "select name, website, google_place_id from public.competitors order by name"
    ) == [
        {"name": "Andere", "website": "http://andere.de", "google_place_id": None},
        {"name": "Konkurrenz", "website": "https://konkurrenz.de", "google_place_id": "place-1"},
    ]


def test_the_selection_is_stored_sorted_and_without_duplicates(database: Database) -> None:
    audit = database.value(
        "select public.start_field_audit(:'website', p_disabled_checks => :'checks'::text[])",
        ADMIN,
        website="example.de",
        checks=pg_array(["m4", "a2", "m4", "a10"]),
    )

    assert _audit(database, audit)["disabled_checks"] == ["a10", "a2", "m4"]


@pytest.mark.parametrize(
    ("checks", "shown"), [(["a1", "Z9", "q"], "Z9"), (["a1", "a"], "a"), (["a1", None], "leer")]
)
def test_an_unknown_check_is_rejected_and_nothing_is_stored(
    database: Database, checks: list[str | None], shown: str
) -> None:
    with pytest.raises(DatabaseError, match=f"^Unbekannte Prüfung in der Auswahl: {shown}$"):
        database.value(
            "select public.start_field_audit(:'website', p_disabled_checks => :'checks'::text[])",
            ADMIN,
            website="example.de",
            checks=pg_array(checks),
        )

    assert database.value("select count(*) from public.customers") == 0


_START_WITH_AREA_O = (
    "select public.start_field_audit(:'website', p_disabled_checks => array['o1', 'a1'])"
)


def test_area_o_is_rejected_before_012(database_011: Database) -> None:
    with pytest.raises(DatabaseError, match="^Unbekannte Prüfung in der Auswahl: o1$"):
        database_011.value(_START_WITH_AREA_O, ADMIN, website="example.de")
    customer = database_011.run(
        "insert into public.customers (name, industry, city) values ('x', '', '') returning id;"
    ).strip()
    with pytest.raises(DatabaseError, match="audits_disabled_checks_ids"):
        database_011.run(
            "insert into public.audits (customer_id, disabled_checks) "
            "values (:'customer', '{o1}');",
            customer=customer,
        )


def test_area_o_is_accepted_after_012(database_012: Database) -> None:
    audit = database_012.value(_START_WITH_AREA_O, ADMIN, website="example.de")

    assert _audit(database_012, audit)["disabled_checks"] == ["a1", "o1"]


_WRONG_IDENTIFICATION = """
insert into public.customers (id, name, website, industry, city)
values ('10000000-0000-4000-8000-000000000001', 'Falsche Firma', 'https://example.de', 'Handwerk',
        'Koeln');
insert into public.locations (customer_id, name, street, postal_code, city, phone, google_place_id,
                              latitude, longitude)
values ('10000000-0000-4000-8000-000000000001', 'Falsche Firma', 'Hauptstr. 1', '50667', 'Koeln',
        '0221 123', 'place-wrong', 50.9, 6.9);
insert into public.keywords (customer_id, phrase, city, is_primary) values
  ('10000000-0000-4000-8000-000000000001', 'Dachdecker', 'Koeln', true),
  ('10000000-0000-4000-8000-000000000001', 'Notdienst', 'Koeln', false),
  ('10000000-0000-4000-8000-000000000001', 'Notdienst', 'Muenster', false);
insert into crm.customers (id, name, city, source)
values ('20000000-0000-4000-8000-000000000002', 'Falsche Firma', 'Koeln', 'manual');
update public.customers set crm_customer_id = '20000000-0000-4000-8000-000000000002'
where id = '10000000-0000-4000-8000-000000000001';
"""


def test_a_correction_resets_the_location_and_moves_keywords_and_crm_customer(
    database: Database,
) -> None:
    database.run(_WRONG_IDENTIFICATION)

    _start(database, "example.de", p_name="'Dach Korn'", p_city="'Muenster'")

    assert database.rows(
        "select name, street, postal_code, city, phone, google_place_id, latitude, longitude "
        "from public.locations"
    ) == [
        {
            "name": "Dach Korn",
            "street": "",
            "postal_code": "",
            "city": "Muenster",
            "phone": "",
            "google_place_id": None,
            "latitude": None,
            "longitude": None,
        }
    ]
    assert database.rows("select phrase, city from public.keywords order by phrase, city") == [
        {"phrase": "Dachdecker", "city": "Muenster"},
        {"phrase": "Notdienst", "city": "Koeln"},
        {"phrase": "Notdienst", "city": "Muenster"},
    ]
    assert database.rows("select name, city from crm.customers") == [
        {"name": "Dach Korn", "city": "Muenster"}
    ]


def test_a_correction_leaves_a_crm_customer_someone_changed(database: Database) -> None:
    database.run(_WRONG_IDENTIFICATION)
    database.run(
        "update crm.customers set name = 'Von Hand' "
        "where id = '20000000-0000-4000-8000-000000000002'"
    )

    _start(database, "example.de", p_name="'Dach Korn'", p_city="'Muenster'")

    assert database.rows("select name, city from crm.customers") == [
        {"name": "Von Hand", "city": "Muenster"}
    ]


def test_a_name_only_correction_keeps_the_city_everywhere(database: Database) -> None:
    database.run(_WRONG_IDENTIFICATION)

    _start(database, "example.de", p_name="'Dach Korn'")

    assert database.rows("select name, city, street from public.locations") == [
        {"name": "Dach Korn", "city": "Koeln", "street": ""}
    ]
    assert database.value("select count(*) from public.keywords where city = 'Koeln'") == 2
    assert database.rows("select name, city from crm.customers") == [
        {"name": "Dach Korn", "city": "Koeln"}
    ]


def test_anon_may_not_start_an_audit(database: Database) -> None:
    with pytest.raises(DatabaseError, match="permission denied for function start_field_audit"):
        database.value("select public.start_field_audit('example.de')", ANON)


def test_a_reader_gets_the_administrator_message(database: Database) -> None:
    with pytest.raises(DatabaseError, match="^Nur der Administrator darf das.$"):
        database.value("select public.start_field_audit('example.de')", READER)


def test_start_and_parts_are_invoker_functions_closed_to_anon_and_public(
    database_012: Database,
) -> None:
    functions = [START, *(f"public.{part}" for part in PARTS_OF_012)]
    rows = database_012.rows(
        "select p.oid::regprocedure::text as name, p.prosecdef, p.proconfig, "
        "has_function_privilege('anon', p.oid, 'execute') as anon, "
        "has_function_privilege('authenticated', p.oid, 'execute') as authenticated, "
        "has_function_privilege('service_role', p.oid, 'execute') as service_role, "
        "coalesce(array_to_string(p.proacl, ','), '') ~ '(^|,)=' as public "
        "from unnest(:'functions'::text[]) as f(name) "
        "join pg_proc p on p.oid = f.name::regprocedure",
        functions=pg_array(functions),
    )

    assert len(rows) == len(functions)
    for row in rows:
        assert row["prosecdef"] is False, row["name"]
        assert row["proconfig"] == ["search_path=public, pg_temp"], row["name"]
        assert (row["anon"], row["public"]) == (False, False), row["name"]
        assert (row["authenticated"], row["service_role"]) == (True, True), row["name"]


def test_parts_check_the_administrator_themselves(database_012: Database) -> None:
    with pytest.raises(DatabaseError, match="^Nur der Administrator darf das.$"):
        database_012.value("select public.queue_field_audit(gen_random_uuid(), '{}')", READER)


def test_running_012_again_changes_nothing(database_012: Database) -> None:
    _start(database_012, "example.de", p_name="'Korn'", p_city="'Muenster'")
    before = schema_fingerprint(database_012)

    database_012.run_file(migration("012"))
    assert schema_fingerprint(database_012) == before

    database_012.run_file(migration("011"))
    database_012.run_file(migration("012"))
    assert schema_fingerprint(database_012) == before


def test_running_011_again_changes_nothing(
    cluster: PostgresCluster, templates: dict[str, str]
) -> None:
    for database in _copy(cluster, templates["011"]):
        before = schema_fingerprint(database)
        database.run_file(migration("011"))
        assert schema_fingerprint(database) == before


def _enum_labels(database: Database, name: str) -> list[str]:
    labels: list[str] = database.value(
        "select coalesce(array_agg(e.enumlabel order by e.enumsortorder), '{}') "
        "from pg_enum e join pg_type t on t.oid = e.enumtypid "
        "where t.typname = :'name' and t.typnamespace = 'public'::regnamespace",
        name=name,
    )
    return labels


def _exists(database: Database, query: str) -> bool:
    found: bool = database.value(f"select exists ({query})")
    return found


def _run_014(database: Database) -> None:
    for path in migrations_after("012", "014"):
        database.run_file(path)


def test_014_removes_every_billing_object(database_014: Database) -> None:
    gone = {
        "payments": "select 1 from pg_class where oid = to_regclass('public.payments')",
        "invoice_counters": "select 1 from pg_class "
        "where oid = to_regclass('public.invoice_counters')",
        "next_invoice_number": "select 1 from pg_proc where proname = 'next_invoice_number'",
        "forbid_invoice_update": "select 1 from pg_proc where proname = 'forbid_invoice_update'",
        "documents_immutable": "select 1 from pg_trigger where tgname = 'documents_immutable'",
        "customers.stage": "select 1 from information_schema.columns "
        "where table_name = 'customers' and table_schema = 'public' and column_name = 'stage'",
        "documents.cancels_document_id": "select 1 from information_schema.columns "
        "where table_name = 'documents' and column_name = 'cancels_document_id'",
        "customer_stage": "select 1 from pg_type where typname = 'customer_stage'",
        "old enum types": "select 1 from pg_type where typname like '%before_014'",
    }

    assert [name for name, query in gone.items() if _exists(database_014, query)] == []


def test_014_keeps_the_other_kinds_in_their_order(database_014: Database) -> None:
    assert _enum_labels(database_014, "job_kind") == [
        "collect",
        "score",
        "analyse",
        "report",
        "serp_retry",
        "optimize",
        "apply",
    ]
    assert _enum_labels(database_014, "document_kind") == [
        "quote",
        "service_description",
        "contract",
        "proof_of_service",
        "audit_quote",
    ]


def test_014_keeps_jobs_and_documents_and_drops_monthly_jobs(database_012: Database) -> None:
    audit = _audit(database_012, _start(database_012, "example.de"))
    database_012.run(
        "insert into public.jobs (kind, customer_id) values ('monthly', :'customer');"
        "insert into public.documents (customer_id, kind, data) "
        "values (:'customer', 'audit_quote', '{}');",
        customer=str(audit["customer_id"]),
    )

    _run_014(database_012)

    assert database_012.rows("select kind from public.jobs order by created_at") == [
        {"kind": "collect"}
    ]
    assert database_012.rows("select kind from public.documents") == [{"kind": "audit_quote"}]


def test_014_claim_jobs_still_claims_and_only_for_the_worker(database_014: Database) -> None:
    _start(database_014, "example.de")

    claimed = database_014.rows("select kind, status from public.claim_jobs('collect', 5)")

    assert claimed == [{"kind": "collect", "status": "running"}]
    rights = database_014.rows(
        "select has_function_privilege('anon', p.oid, 'execute') as anon, "
        "has_function_privilege('authenticated', p.oid, 'execute') as authenticated, "
        "has_function_privilege('service_role', p.oid, 'execute') as service_role "
        "from pg_proc p where p.oid = 'public.claim_jobs(public.job_kind, integer)'::regprocedure"
    )
    assert rights == [{"anon": False, "authenticated": False, "service_role": True}]


def test_014_leaves_issued_invoices_alone_and_stops(database_012: Database) -> None:
    audit = _audit(database_012, _start(database_012, "example.de"))
    database_012.run(
        "insert into public.documents (customer_id, kind, number, data) "
        "values (:'customer', 'invoice', 'RA-2026-0001', '{}');",
        customer=str(audit["customer_id"]),
    )

    with pytest.raises(DatabaseError, match="Migration 014 abgebrochen: 1 Rechnungen"):
        _run_014(database_012)

    assert database_012.value("select count(*) from public.documents") == 1
    assert "invoice" in _enum_labels(database_012, "document_kind")


def test_014_keeps_triggers_that_look_at_the_job_kind(database_012: Database) -> None:
    database_012.run(
        "create function public.note_retry() returns trigger language plpgsql as $$ "
        "begin insert into public.settings (key, value) values ('retry_seen', 'true') "
        "on conflict (key) do nothing; return new; end $$; "
        "create trigger jobs_retry_seen after insert on public.jobs for each row "
        "when (new.kind = 'serp_retry') execute function public.note_retry();"
    )

    _run_014(database_012)
    audit = _audit(database_012, _start(database_012, "example.de"))
    database_012.run(
        "insert into public.jobs (kind, customer_id) values ('serp_retry', :'customer');",
        customer=str(audit["customer_id"]),
    )

    assert database_012.value("select count(*) from public.settings where key = 'retry_seen'") == 1


def test_014_removes_the_billing_settings(database_012: Database) -> None:
    database_012.run(
        "insert into public.settings (key, value) values "
        "('monthly_last_run', '\"2026-10\"'), ('tax_export_request', '{}'), ('sources', '{}');"
    )

    _run_014(database_012)

    assert database_012.rows("select key from public.settings order by key") == [{"key": "sources"}]


def test_running_014_again_changes_nothing(database_014: Database) -> None:
    _start(database_014, "example.de", p_name="'Korn'", p_city="'Muenster'")
    before = schema_fingerprint(database_014)

    database_014.run_file(migration("014"))

    assert schema_fingerprint(database_014) == before


def _read_policy_buckets(database: Database) -> str:
    qual: str = database.value(
        "select qual from pg_policies where schemaname = 'storage' "
        "and policyname = 'authenticated_read_radar_buckets'"
    )
    return qual


def test_014_removes_the_empty_export_bucket_and_its_read_right(database_014: Database) -> None:
    assert database_014.value("select count(*) from storage.buckets where id = 'exports'") == 0
    qual = _read_policy_buckets(database_014)
    assert "exports" not in qual
    for bucket in ("raw", "reports", "documents"):
        assert bucket in qual


def test_014_keeps_an_export_bucket_that_still_holds_files(database_012: Database) -> None:
    database_012.run(
        "insert into storage.objects (bucket_id, name) values ('exports', '2026/Steuer_2026.zip');"
    )

    _run_014(database_012)
    _run_014(database_012)

    assert database_012.value("select count(*) from storage.buckets where id = 'exports'") == 1
    assert "exports" not in _read_policy_buckets(database_012)
