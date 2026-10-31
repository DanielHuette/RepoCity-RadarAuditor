"""Migration 016 (customer search, review reply PDFs, monthly service) against a real PostgreSQL.

Context: The app asks for a search, the worker claims it with the service key and stores the ranked
list; a second request within prospect_cache_hours() must reuse the list. The CRM side of 016
(add_prospect_to_crm, queue_monthly_service) needs the CRM migrations and is tested in
integration/. Skipped when no PostgreSQL binaries are found (set RADAR_PG_BIN to their directory).
| Input: supabase/migrations 001-016. | Output: Assertions.
"""

import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from db.postgres import (
    BIN_DIR_VARIABLE,
    Caller,
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
TEMPLATE = "radar_through_016"
SERVICE = Caller("service_role")

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
def template(cluster: PostgresCluster) -> str:
    build_project(cluster.create_database(TEMPLATE), migrations_through("016"))
    return TEMPLATE


@pytest.fixture
def database(cluster: PostgresCluster, template: str) -> Iterator[Database]:
    """A fresh copy of the project after 016."""
    database = cluster.create_database(f"test_{uuid4().hex}", template=template)
    try:
        yield database
    finally:
        cluster.drop_database(database.name)


def _request(database: Database, trade: str = "Dachdecker", city: str = "Münster") -> str:
    search: str = database.value(
        "select public.request_prospect_search(:'trade', :'city')", ADMIN, trade=trade, city=city
    )
    return search


def _claim(database: Database) -> list[Any]:
    return database.rows("select id, status from public.claim_prospect_search()", SERVICE)


def _result(place_id: str, rank: int, **changes: object) -> dict[str, object]:
    return {
        "rank": rank,
        "place_id": place_id,
        "name": f"Betrieb {place_id}",
        "address": "Hiltruper Str. 210, 48165 Münster, Deutschland",
        "street": "Hiltruper Str. 210",
        "postal_code": "48165",
        "city": "Münster",
        "phone": None,
        "website": None,
        "maps_url": "https://maps.google.com/?cid=1",
        "rating": 4.5,
        "review_count": 3,
        "photo_count": 0,
        "has_opening_hours": False,
        "score": 7,
        "signals": ["no_website", "no_photos"],
        "reasons": "Keine Webseite im Profil und keine Fotos.",
        "contact_ways": [{"channel": "letter", "label": "Brief", "status": "allowed", "note": "x"}],
        **changes,
    }


def _finish(database: Database, search: str, results: list[dict[str, object]]) -> int:
    count: int = database.value(
        "select public.finish_prospect_search(:'search', :'results'::jsonb)",
        SERVICE,
        search=search,
        results=json.dumps(results),
    )
    return count


def test_a_request_queues_one_search_per_trade_and_city(database: Database) -> None:
    first = _request(database, " Dachdecker ", "Münster")

    assert _request(database, "dachdecker", "MÜNSTER") == first
    assert database.rows("select trade, city, status from public.prospect_searches") == [
        {"trade": "Dachdecker", "city": "Münster", "status": "queued"}
    ]


@pytest.mark.parametrize(
    ("trade", "city", "message"),
    [
        ("D", "Münster", "Bitte ein Gewerk mit 2 bis 80 Zeichen eingeben."),
        ("Dachdecker", " ", "Bitte eine Stadt mit 2 bis 80 Zeichen eingeben."),
    ],
)
def test_a_request_without_trade_or_city_is_refused(
    database: Database, trade: str, city: str, message: str
) -> None:
    with pytest.raises(DatabaseError, match=message):
        _request(database, trade, city)


def test_only_the_administrator_may_request_a_search(database: Database) -> None:
    with pytest.raises(DatabaseError, match="Nur der Administrator darf das."):
        database.run("select public.request_prospect_search('Maler', 'Münster')", READER)
    with pytest.raises(DatabaseError, match="permission denied"):
        database.run("select public.request_prospect_search('Maler', 'Münster')", ANON)


def test_the_app_only_reads_the_search_tables(database: Database) -> None:
    _request(database)

    assert database.value("select count(*) from public.prospect_searches", READER) == 1
    for statement in (
        "insert into public.prospect_searches (trade, city) values ('Maler', 'Münster')",
        "update public.prospect_searches set status = 'queued'",
        "delete from public.prospect_results",
    ):
        with pytest.raises(DatabaseError, match="permission denied"):
            database.run(statement, ADMIN)
    with pytest.raises(DatabaseError, match="permission denied"):
        database.run("select count(*) from public.prospect_searches", ANON)


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Bäckerei Korn GmbH & Co. KG", "baeckereikorn"),
        ("BÄCKEREI KORN e.K.", "baeckereikorn"),
        ("Maler Kortmann Inh. Frank", "malerkortmannfrank"),
        ("Coburger Agentur", "coburgeragentur"),
        ("Münster (Westf.)", "muenster"),
        ("Bäckerei Korn (Filiale Hiltrup) GmbH", "baeckereikorn"),
    ],
)
def test_names_are_compared_without_legal_form_and_spelling(
    database: Database, name: str, key: str
) -> None:
    assert database.value("select public.name_key(:'name')", name=name) == key


@pytest.mark.parametrize(
    ("phone", "key"),
    [
        ("+49 251 4711", "02514711"),
        ("0049 (0)251-4711", "02514711"),
        ("+49 (0) 251 4711", "02514711"),
        ("0251/4711", "02514711"),
        ("110", ""),
    ],
)
def test_phone_numbers_are_compared_by_their_digits(
    database: Database, phone: str, key: str
) -> None:
    assert database.value("select public.phone_key(:'phone')", phone=phone) == key


def test_the_worker_claims_stores_and_a_second_request_reuses_the_list(database: Database) -> None:
    search = _request(database)

    assert _claim(database) == [{"id": search, "status": "running"}]
    assert _claim(database) == []
    assert _finish(database, search, [_result("ChIJ-a", 1), _result("ChIJ-b", 2)]) == 2

    assert _request(database) == search
    assert database.value("select status from public.prospect_searches") == "done"
    assert _claim(database) == []
    assert database.rows(
        "select place_id, rank from public.prospect_results order by rank", READER
    ) == [{"place_id": "ChIJ-a", "rank": 1}, {"place_id": "ChIJ-b", "rank": 2}]


def test_a_refresh_keeps_the_crm_link_and_drops_businesses_no_longer_listed(
    database: Database,
) -> None:
    search = _request(database)
    _claim(database)
    _finish(database, search, [_result("ChIJ-a", 1), _result("ChIJ-b", 2)])
    database.run("insert into crm.customers (id, name, city) values (gen_random_uuid(), 'A', 'M')")
    database.run(
        "update public.prospect_results set crm_customer_id = (select id from crm.customers) "
        "where place_id = 'ChIJ-a'"
    )
    database.run("update public.prospect_searches set fetched_at = now() - interval '25 hours'")
    database.run("update public.prospect_searches set status = 'failed'")

    assert _request(database) == search
    _claim(database)
    _finish(database, search, [_result("ChIJ-c", 1), _result("ChIJ-a", 2, name="Neu")])

    rows = database.rows(
        "select place_id, rank, name, crm_customer_id is not null as linked "
        "from public.prospect_results order by rank"
    )
    assert rows == [
        {"place_id": "ChIJ-c", "rank": 1, "name": "Betrieb ChIJ-c", "linked": False},
        {"place_id": "ChIJ-a", "rank": 2, "name": "Neu", "linked": True},
    ]


def test_lists_older_than_the_cache_hours_lose_their_places_fields(database: Database) -> None:
    search = _request(database)
    _claim(database)
    _finish(database, search, [_result("ChIJ-a", 1), _result("ChIJ-b", 2)])
    database.run("insert into crm.customers (id, name, city) values (gen_random_uuid(), 'A', 'M')")
    database.run(
        "update public.prospect_results set crm_customer_id = (select id from crm.customers) "
        "where place_id = 'ChIJ-a'"
    )
    database.run("update public.prospect_searches set fetched_at = now() - interval '25 hours'")

    assert _claim(database) == []

    assert database.value("select status from public.prospect_searches") == "expired"
    assert database.rows(
        "select place_id, name, address, phone, reasons, review_count, crm_customer_id is not null "
        "as linked from public.prospect_results"
    ) == [
        {
            "place_id": "ChIJ-a",
            "name": "",
            "address": "",
            "phone": None,
            "reasons": "",
            "review_count": 0,
            "linked": True,
        }
    ]
    assert _request(database) == search
    assert database.value("select status from public.prospect_searches") == "queued"


def test_a_failed_search_keeps_its_reason_and_can_be_asked_again(database: Database) -> None:
    search = _request(database)
    _claim(database)
    database.run(
        "select public.fail_prospect_search(:'search', 'Tageslimit erreicht.')",
        SERVICE,
        search=search,
    )

    assert database.rows("select status, error from public.prospect_searches") == [
        {"status": "failed", "error": "Tageslimit erreicht."}
    ]
    assert _request(database) == search
    assert database.value("select status from public.prospect_searches") == "queued"


def test_an_incomplete_list_keeps_its_note(database: Database) -> None:
    search = _request(database)
    _claim(database)
    database.value(
        "select public.finish_prospect_search(:'search', :'results'::jsonb, 'Nur 20 Betriebe.')",
        SERVICE,
        search=search,
        results=json.dumps([_result("ChIJ-a", 1)]),
    )

    assert database.rows("select status, error, result_count from public.prospect_searches") == [
        {"status": "done", "error": "Nur 20 Betriebe.", "result_count": 1}
    ]


def test_results_of_a_search_that_is_not_running_are_refused(database: Database) -> None:
    search = _request(database)

    with pytest.raises(DatabaseError, match="Diese Suche ist nicht in Arbeit."):
        _finish(database, search, [_result("ChIJ-a", 1)])


def test_a_search_left_running_by_a_stopped_worker_is_claimed_again(database: Database) -> None:
    search = _request(database)
    _claim(database)
    database.run("update public.prospect_searches set claimed_at = now() - interval '11 minutes'")

    assert _claim(database) == [{"id": search, "status": "running"}]


@pytest.mark.parametrize(
    "function",
    [
        "claim_prospect_search()",
        "finish_prospect_search(uuid, jsonb, text)",
        "fail_prospect_search(uuid, text)",
        "purge_prospect_searches()",
        "queue_monthly_service(uuid)",
        "prospect_objected(text, text, text, text)",
        "record_advertising_objection(uuid)",
    ],
)
def test_worker_functions_are_for_the_service_key_only(database: Database, function: str) -> None:
    rights = database.rows(
        "select has_function_privilege('anon', :'name', 'execute') as anon, "
        "has_function_privilege('authenticated', :'name', 'execute') as authenticated, "
        "has_function_privilege('service_role', :'name', 'execute') as service_role",
        name=f"public.{function}",
    )
    assert rights == [{"anon": False, "authenticated": False, "service_role": True}]


def test_review_replies_is_a_document_kind(database: Database) -> None:
    kinds = database.value("select enum_range(null::public.document_kind)::text")
    assert "review_replies" in kinds


def test_running_016_again_changes_nothing(database: Database) -> None:
    before = schema_fingerprint(database)
    database.run_file(migration("016"))
    assert schema_fingerprint(database) == before


@pytest.mark.parametrize(
    ("objection", "name", "city", "objected"),
    [
        (("Bäckerei Korn", "Münster"), "Bäckerei Korn", "Münster", True),
        (("Bäckerei Korn", ""), "Bäckerei Korn", "Telgte", True),
        (("Bäckerei Korn", "Münster (Westf.)"), "Bäckerei Korn", "Münster", True),
        (("Bäckerei Korn", "Münster"), "Bäckerei Korn", "Münster-Hiltrup", True),
        (("Bäckerei Korn", "Münster"), "Bäckerei Korn", "", True),
        (("Bäckerei Korn", "Münster"), "Bäckerei Korn Inh. Peter Müller", "Münster", True),
        (("Bäckerei Korn Inh. Peter Korn", "Münster"), "Bäckerei Korn", "Münster", True),
        (("Bäckerei Korn", "Münster"), "Bäckerei Korn", "Telgte", False),
        (("Bäckerei Korn", "Münster"), "Bäckerei Kornmann", "Münster", True),
        (("Korn", "Münster"), "Kornhaus Weber", "Münster", False),
        (("Bäckerei Korn", "Münster"), "Fleischerei Korn", "Münster", False),
    ],
)
def test_an_objection_matches_by_name_and_the_city_when_one_is_known(
    database: Database, objection: tuple[str, str], name: str, city: str, objected: bool
) -> None:
    database.run(
        "insert into public.advertising_objections (name_key, city_key) "
        "values (public.name_key(:'name'), public.name_key(:'city'))",
        name=objection[0],
        city=objection[1],
    )
    found = database.value(
        "select public.prospect_objected(:'name', :'city', null, null)", name=name, city=city
    )
    assert found is objected


def test_the_database_deletes_the_places_fields_every_hour_without_the_worker(
    database: Database,
) -> None:
    database.run_file(migration("016"))
    jobs = database.rows(
        "select schedule, command from cron.job where jobname = 'radar-prospect-purge'"
    )
    assert [job["command"].strip() for job in jobs] == ["select public.purge_prospect_searches();"]
    minute, hour, *_ = jobs[0]["schedule"].split()
    assert minute.isdigit() and int(minute) not in (0, 30)
    assert hour == "*"
