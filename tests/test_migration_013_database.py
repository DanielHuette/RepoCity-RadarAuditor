"""Migration 013 (hand-over to the CRM) against a real PostgreSQL.

Context: The CRM runs the monthly audit, completes the order once the optimisation is finished and
recognises customers the RadarAuditor created; 013 provides the database side of that hand-over.
Skipped when no PostgreSQL binaries are found (set RADAR_PG_BIN to their directory). | Input:
supabase/migrations 001-013. | Output: Assertions.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from db.postgres import (
    BIN_DIR_VARIABLE,
    Database,
    DatabaseError,
    PostgresCluster,
    find_bin_dir,
    runs_as_root,
)
from db.supabase import (
    ADMIN,
    ANON,
    READER,
    build_project,
    migration,
    migrations_through,
    schema_fingerprint,
)

BIN_DIR = find_bin_dir()
TEMPLATE = "radar_through_013"
QUEUE = "public.queue_monthly_audit(uuid)"
FINISHED = "optimization.finished"

pytestmark = [
    pytest.mark.skipif(
        BIN_DIR is None, reason=f"No PostgreSQL binaries; set {BIN_DIR_VARIABLE} to their directory"
    ),
    pytest.mark.skipif(runs_as_root(), reason="PostgreSQL refuses to run as root"),
]

_AUDITED_CUSTOMER = """
insert into crm.customers (id, name, city)
values ('20000000-0000-4000-8000-000000000001', 'Korn', 'Muenster');
insert into public.customers (id, name, website, industry, city, crm_customer_id, created_at)
values ('10000000-0000-4000-8000-000000000001', 'Korn', 'https://korn.de', 'Baeckerei', 'Muenster',
        '20000000-0000-4000-8000-000000000001', now() - interval '2 days'),
       ('10000000-0000-4000-8000-000000000002', 'Korn Filiale', 'https://korn-filiale.de',
        'Baeckerei', 'Muenster', '20000000-0000-4000-8000-000000000001', now());
insert into public.audits (customer_id, kind, status, requested_at, disabled_checks)
values ('10000000-0000-4000-8000-000000000001', 'remote', 'reported', now() - interval '40 days',
        '{a1}'),
       ('10000000-0000-4000-8000-000000000001', 'remote', 'reported', now() - interval '10 days',
        '{b2,m4}');
"""
CRM_CUSTOMER = "20000000-0000-4000-8000-000000000001"
CUSTOMER = "10000000-0000-4000-8000-000000000001"

_ORDER = """
insert into crm.orders (id, number, customer_id)
values ('30000000-0000-4000-8000-000000000001', 'AU-2026-0001',
        '20000000-0000-4000-8000-000000000001');
"""
ORDER = "30000000-0000-4000-8000-000000000001"


@pytest.fixture(scope="module")
def cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PostgresCluster]:
    assert BIN_DIR is not None
    with PostgresCluster(BIN_DIR, tmp_path_factory.mktemp("postgres")) as running:
        yield running


@pytest.fixture(scope="module")
def template(cluster: PostgresCluster) -> str:
    build_project(cluster.create_database(TEMPLATE), migrations_through("013"))
    return TEMPLATE


@pytest.fixture
def database(cluster: PostgresCluster, template: str) -> Iterator[Database]:
    """A fresh copy of the project after 013."""
    database = cluster.create_database(f"test_{uuid4().hex}", template=template)
    try:
        yield database
    finally:
        cluster.drop_database(database.name)


@pytest.fixture
def audited(database: Database) -> Database:
    database.run(_AUDITED_CUSTOMER)
    return database


@pytest.fixture
def ordered(audited: Database) -> Database:
    audited.run(_ORDER)
    return audited


def _queue(database: Database) -> str:
    audit: str = database.value(
        "select public.queue_monthly_audit(:'crm')", ADMIN, crm=CRM_CUSTOMER
    )
    return audit


def test_audits_are_sales_audits_unless_marked_monthly(audited: Database) -> None:
    assert audited.rows("select distinct purpose from public.audits") == [{"purpose": "sales"}]
    with pytest.raises(DatabaseError, match="audits_purpose_check"):
        audited.run("update public.audits set purpose = 'other'")


def test_monthly_audit_repeats_the_selection_of_the_latest_audit(audited: Database) -> None:
    audit = _queue(audited)

    assert audited.rows(
        "select customer_id, kind, status, purpose, disabled_checks from public.audits "
        "where id = :'audit'",
        audit=audit,
    ) == [
        {
            "customer_id": CUSTOMER,
            "kind": "remote",
            "status": "requested",
            "purpose": "monthly",
            "disabled_checks": ["b2", "m4"],
        }
    ]
    assert audited.rows(
        "select kind, status, customer_id from public.jobs where audit_id = :'audit'", audit=audit
    ) == [{"kind": "collect", "status": "queued", "customer_id": CUSTOMER}]


def test_monthly_audit_uses_the_google_access_when_granted(audited: Database) -> None:
    audited.run(
        "insert into public.customer_access (customer_id, google_business_profile_granted) "
        "values (:'customer', true)",
        customer=CUSTOMER,
    )

    audit = _queue(audited)

    assert audited.value("select kind from public.audits where id = :'audit'", audit=audit) == (
        "with_access"
    )


def test_monthly_audit_without_audited_customer_is_refused(database: Database) -> None:
    with pytest.raises(DatabaseError, match="^Zu diesem Kunden gibt es noch keine Prüfung.$"):
        database.value("select public.queue_monthly_audit(gen_random_uuid())", ADMIN)


def test_a_missing_customer_raises_no_data_found(database: Database) -> None:
    caught = database.run(
        "do $$ begin perform public.queue_monthly_audit(gen_random_uuid()); "
        "exception when no_data_found then perform set_config('test.caught', 'yes', true); end $$;"
        "select current_setting('test.caught', true);"
    )

    assert caught.strip() == "yes"


def test_monthly_audit_is_for_the_administrator_only(audited: Database) -> None:
    with pytest.raises(DatabaseError, match="permission denied for function queue_monthly_audit"):
        audited.value("select public.queue_monthly_audit(:'crm')", ANON, crm=CRM_CUSTOMER)
    with pytest.raises(DatabaseError, match="^Nur der Administrator darf das.$"):
        audited.value("select public.queue_monthly_audit(:'crm')", READER, crm=CRM_CUSTOMER)


def test_queue_is_an_invoker_function_closed_to_anon_and_public(database: Database) -> None:
    assert database.rows(
        "select p.prosecdef, p.proconfig, "
        "has_function_privilege('anon', p.oid, 'execute') as anon, "
        "has_function_privilege('authenticated', p.oid, 'execute') as authenticated, "
        "has_function_privilege('service_role', p.oid, 'execute') as service_role, "
        "coalesce(array_to_string(p.proacl, ','), '') ~ '(^|,)=' as public "
        "from pg_proc p where p.oid = :'name'::regprocedure",
        name=QUEUE,
    ) == [
        {
            "prosecdef": False,
            "proconfig": ["search_path=public, pg_temp"],
            "anon": False,
            "authenticated": True,
            "service_role": True,
            "public": False,
        }
    ]


def _finished(database: Database) -> list[object]:
    return database.rows(
        "select payload from public.events where kind = :'kind' order by created_at", kind=FINISHED
    )


def _job(database: Database, kind: str, status: str) -> str:
    return database.run(
        "insert into public.jobs (kind, status, customer_id, order_id) "
        "values (:'kind', :'status', :'customer', :'order') returning id;",
        kind=kind,
        status=status,
        customer=CUSTOMER,
        order=ORDER,
    ).strip()


def _change(database: Database, status: str) -> str:
    return database.run(
        "insert into public.optimization_changes (order_id, target, after, status) "
        "values (:'order', 'website', 'neu', :'status') returning id;",
        order=ORDER,
        status=status,
    ).strip()


def test_finished_is_written_once_the_last_job_is_done(ordered: Database) -> None:
    job = _job(ordered, "apply", "running")
    _change(ordered, "applied")
    _change(ordered, "rejected")
    assert _finished(ordered) == []

    ordered.run("update public.jobs set status = 'done' where id = :'job'", job=job)

    assert _finished(ordered) == [{"payload": {"order_id": ORDER}}]


@pytest.mark.parametrize("open_status", ["draft", "approved", "manual", "failed"])
def test_an_open_change_holds_the_event_back(ordered: Database, open_status: str) -> None:
    change = _change(ordered, open_status)
    _change(ordered, "applied")
    assert _finished(ordered) == []

    ordered.run(
        "update public.optimization_changes set status = 'manual_done' where id = :'change'",
        change=change,
    )

    assert _finished(ordered) == [{"payload": {"order_id": ORDER}}]


@pytest.mark.parametrize(("kind", "status"), [("optimize", "queued"), ("apply", "running")])
def test_a_waiting_job_holds_the_event_back(ordered: Database, kind: str, status: str) -> None:
    _job(ordered, kind, status)

    _change(ordered, "applied")

    assert _finished(ordered) == []


def test_an_optimize_run_that_found_nothing_to_change_finishes_the_order(
    ordered: Database,
) -> None:
    job = _job(ordered, "optimize", "running")

    ordered.run("update public.jobs set status = 'done' where id = :'job'", job=job)

    assert _finished(ordered) == [{"payload": {"order_id": ORDER}}]


def test_a_failed_optimize_run_without_changes_gets_no_event(ordered: Database) -> None:
    job = _job(ordered, "optimize", "running")

    ordered.run("update public.jobs set status = 'failed' where id = :'job'", job=job)

    assert _finished(ordered) == []


def test_an_optimize_run_without_changes_waits_for_an_open_apply_job(ordered: Database) -> None:
    _job(ordered, "apply", "queued")
    job = _job(ordered, "optimize", "running")

    ordered.run("update public.jobs set status = 'done' where id = :'job'", job=job)

    assert _finished(ordered) == []


def test_the_event_is_written_only_once_per_order(ordered: Database) -> None:
    change = _change(ordered, "applied")
    _change(ordered, "rejected")
    ordered.run(
        "update public.optimization_changes set error = 'nochmals' where id = :'change'",
        change=change,
    )
    job = _job(ordered, "apply", "queued")
    ordered.run("update public.jobs set status = 'done' where id = :'job'", job=job)

    assert len(_finished(ordered)) == 1


def test_crm_customers_created_by_the_radarauditor_carry_its_source(database: Database) -> None:
    database.value(
        "select public.start_field_audit('korn.de', p_name => 'Korn', p_city => 'Muenster')", ADMIN
    )

    assert database.rows("select name, city, source from crm.customers") == [
        {"name": "Korn", "city": "Muenster", "source": "radarauditor"}
    ]


def test_013_keeps_a_source_rule_the_crm_already_extended(database: Database) -> None:
    database.run(
        "alter table crm.customers drop constraint customers_source_check; "
        "alter table crm.customers add constraint customers_source_check check (source in "
        "('manual', 'website', 'phone', 'email', 'referral', 'agent', 'other', 'radarauditor', "
        "'later'))"
    )
    before = schema_fingerprint(database)

    database.run_file(migration("013"))

    assert schema_fingerprint(database) == before


def test_running_013_again_changes_nothing(ordered: Database) -> None:
    _change(ordered, "applied")
    before = schema_fingerprint(ordered)

    ordered.run_file(migration("013"))

    assert schema_fingerprint(ordered) == before
    assert len(_finished(ordered)) == 1
