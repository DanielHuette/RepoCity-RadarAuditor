"""Migration 015 (link CRM leads) against a real PostgreSQL.

Context: A business that wrote through the web form, mailed or called reaches the CRM before the
field audit, without a city; the field audit must link it instead of creating a second CRM customer.
Skipped when no PostgreSQL binaries are found (set RADAR_PG_BIN to their directory). | Input:
supabase/migrations 001-015. | Output: Assertions.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from db.postgres import BIN_DIR_VARIABLE, Database, PostgresCluster, find_bin_dir, runs_as_root
from db.supabase import ADMIN, build_project, migration, migrations_through, schema_fingerprint

BIN_DIR = find_bin_dir()
TEMPLATE = "radar_through_015"

pytestmark = [
    pytest.mark.skipif(
        BIN_DIR is None, reason=f"No PostgreSQL binaries; set {BIN_DIR_VARIABLE} to their directory"
    ),
    pytest.mark.skipif(runs_as_root(), reason="PostgreSQL refuses to run as root"),
]

_START = (
    "select public.start_field_audit('baeckerei-korn.de', 'Bäckerei Korn', 'Münster', 'Bäckerei');"
)


@pytest.fixture(scope="module")
def cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PostgresCluster]:
    assert BIN_DIR is not None
    with PostgresCluster(BIN_DIR, tmp_path_factory.mktemp("postgres")) as running:
        yield running


@pytest.fixture(scope="module")
def template(cluster: PostgresCluster) -> str:
    build_project(cluster.create_database(TEMPLATE), migrations_through("015"))
    return TEMPLATE


@pytest.fixture
def database(cluster: PostgresCluster, template: str) -> Iterator[Database]:
    """A fresh copy of the project after 015."""
    database = cluster.create_database(f"test_{uuid4().hex}", template=template)
    try:
        yield database
    finally:
        cluster.drop_database(database.name)


def _field_audit(database: Database) -> str:
    database.run(_START, ADMIN)
    linked: str = database.value("select crm_customer_id from public.customers")
    return linked


def _lead(database: Database, name: str, city: str = "") -> str:
    database.run(
        "insert into crm.customers (name, city, email, source) values (:'name', :'city', "
        "'info@baeckerei-korn.de', 'website')",
        name=name,
        city=city,
    )
    lead: str = database.value("select id from crm.customers where source = 'website'")
    return lead


def test_a_lead_without_city_is_linked_and_gets_the_city(database: Database) -> None:
    lead = _lead(database, "bäckerei korn")

    linked = _field_audit(database)

    assert linked == lead
    assert database.rows("select id, city, source from crm.customers") == [
        {"id": lead, "city": "Münster", "source": "website"}
    ]


def test_a_customer_with_name_and_city_comes_before_a_lead(database: Database) -> None:
    _lead(database, "Bäckerei Korn")
    database.run("insert into crm.customers (name, city) values ('Bäckerei Korn', 'Münster')")
    known: str = database.value("select id from crm.customers where city = 'Münster'")

    assert _field_audit(database) == known


def test_two_leads_of_the_same_name_are_not_guessed(database: Database) -> None:
    _lead(database, "Bäckerei Korn")
    database.run("insert into crm.customers (name) values ('Bäckerei Korn')")

    linked = _field_audit(database)

    assert (
        database.value("select source from crm.customers where id = :'id'", id=str(linked))
        == "radarauditor"
    )
    assert database.value("select count(*) from crm.customers where city = ''") == 2


def test_running_015_again_changes_nothing(database: Database) -> None:
    before = schema_fingerprint(database)
    database.run_file(migration("015"))
    assert schema_fingerprint(database) == before
