"""Tests for the DataForSEO volumes, links and keyword data collector.

Context: Unit tests with respx for every DataForSEO endpoint. | Input: Fixtures. |
Output: Assertions.
"""

import json
from datetime import date
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from radarauditor.collectors import paid_data, paid_rankings
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import GapKeyword
from radarauditor.config import Settings

pytestmark = pytest.mark.usefixtures("fast_retries")

BASE = paid_rankings.API_BASE_URL
CUSTOMER = "dachdeckerei-muster.de"
RIVAL = "dach-schmidt.de"
TODAY = date(2026, 10, 16)


@pytest.fixture
def client(collector_settings: Settings, open_gate: SourceGate) -> paid_rankings.DataForSeoClient:
    """Client with paid data switched on."""
    settings = collector_settings.model_copy(
        update={
            "dataforseo_login": "login@example.de",
            "dataforseo_password": SecretStr("secret"),
        }
    )
    return paid_rankings.DataForSeoClient(settings, open_gate)


def _envelope(result: list[dict[str, Any]] | None, status: int = 20000) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status_code": 20000,
            "status_message": "Ok.",
            "tasks": [{"status_code": status, "status_message": "x", "result": result}],
        },
    )


def _task(request: httpx.Request) -> dict[str, Any]:
    body = json.loads(request.content)
    assert isinstance(body, list)
    task = body[0]
    assert isinstance(task, dict)
    return task


SUMMARIES = {
    CUSTOMER: {"target": CUSTOMER, "referring_domains": 48, "backlinks_spam_score": 3},
    RIVAL: {"target": RIVAL, "referring_domains": 52, "backlinks_spam_score": 9},
}
REFERRING = {
    CUSTOMER: [
        {"domain": "hwk-muenster.de", "backlinks_spam_score": 0},
        {"domain": "muenster-handwerk.com", "backlinks_spam_score": 2},
        {"domain": "dach-blog.org", "backlinks_spam_score": 70},
        {"domain": "links4you.biz", "backlinks_spam_score": None},
    ],
    RIVAL: [],
}
RANKED = {CUSTOMER: 140, RIVAL: 150}


def _by_target(table: dict[str, Any], wrap: str | None = None) -> Any:
    def answer(request: httpx.Request) -> httpx.Response:
        value = table[_task(request)["target"]]
        if wrap == "items":
            return _envelope([{"items": value}])
        if wrap == "total":
            return _envelope([{"total_count": value, "items": []}])
        return _envelope([value])

    return answer


GAP_ITEMS = [
    {
        "keyword_data": {"keyword": "dachrinne münster", "keyword_info": {"search_volume": 90}},
        "first_domain_serp_element": {"rank_group": 3},
        "second_domain_serp_element": None,
    },
    {
        "keyword_data": {"keyword": "gaube münster", "keyword_info": {"search_volume": None}},
        "first_domain_serp_element": None,
    },
]
HISTORY_ITEMS = [
    {
        "year": 2025 if month > 9 else 2026,
        "month": month,
        "metrics": {"organic": {"pos_1": 2, "pos_2_3": 3, "pos_4_10": month}},
    }
    for month in [10, 11, 12, *range(1, 10)]
] + [{"year": 2025, "month": 9, "metrics": {"organic": {"pos_1": 1}}}]


def _mock_all() -> dict[str, respx.Route]:
    return {
        "volumes": respx.post(f"{BASE}{paid_rankings.VOLUME_PATH}").mock(
            return_value=_envelope([{"keyword": "dachdecker münster", "search_volume": 880}])
        ),
        "summary": respx.post(f"{BASE}{paid_data.SUMMARY_PATH}").mock(
            side_effect=_by_target(SUMMARIES)
        ),
        "referring": respx.post(f"{BASE}{paid_data.REFERRING_DOMAINS_PATH}").mock(
            side_effect=_by_target(REFERRING, "items")
        ),
        "ranked": respx.post(f"{BASE}{paid_data.RANKED_KEYWORDS_PATH}").mock(
            side_effect=_by_target(RANKED, "total")
        ),
        "gap": respx.post(f"{BASE}{paid_data.INTERSECTION_PATH}").mock(
            return_value=_envelope([{"items": GAP_ITEMS}])
        ),
        "history": respx.post(f"{BASE}{paid_data.HISTORY_PATH}").mock(
            return_value=_envelope([{"items": HISTORY_ITEMS}])
        ),
    }


def _request(**overrides: Any) -> paid_data.PaidRequest:
    values: dict[str, Any] = {
        "customer_domain": CUSTOMER,
        "competitor_domains": [RIVAL, CUSTOMER],
        "phrases": ["Dachdecker Münster"],
        "primary_phrases": ["Dachdecker Münster"],
        "city": "Münster",
    }
    values.update(overrides)
    return paid_data.PaidRequest(**values)


def test_local_domains_by_tld_or_city_name() -> None:
    assert paid_data.is_local_domain("hwk-muenster.de", "Münster")
    assert paid_data.is_local_domain("muenster-handwerk.com", "Münster")
    assert paid_data.is_local_domain("munster.com", "Münster")
    assert paid_data.is_local_domain("badoeynhausen.info", "Bad Oeynhausen")
    assert not paid_data.is_local_domain("dach-blog.org", "Münster")
    assert paid_data.city_spellings("Münster") == {"münster", "muenster", "munster"}


def test_domain_of_strips_scheme_path_port_and_www() -> None:
    assert paid_data.domain_of("https://www.Dach-Schmidt.de:443/kontakt") == "dach-schmidt.de"
    assert paid_data.domain_of("dach.de") == "dach.de"
    assert paid_data.domain_of(None) is None
    assert paid_data.domain_of("") is None


@respx.mock
async def test_backlink_summary_computes_local_and_spam_share(
    client: paid_rankings.DataForSeoClient,
) -> None:
    routes = _mock_all()

    summary = await paid_data.backlink_summary(client, CUSTOMER, "Münster")

    assert summary.referring_domains == 48
    assert summary.spam_score == 3
    assert summary.referring_domains_local_share == 0.5
    assert summary.referring_domains_spam_share == 0.25
    task = _task(routes["referring"].calls[0].request)
    assert task["limit"] == 100
    empty = await paid_data.backlink_summary(client, RIVAL, "Münster")
    assert empty.referring_domains_local_share == 0.0
    assert empty.referring_domains_spam_share == 0.0


@respx.mock
async def test_ranked_keyword_count_reads_total_count(
    client: paid_rankings.DataForSeoClient,
) -> None:
    routes = _mock_all()
    assert await paid_data.ranked_keyword_count(client, RIVAL) == 150
    task = _task(routes["ranked"].calls[0].request)
    assert (task["location_code"], task["language_code"], task["limit"]) == (2276, "de", 1)


@respx.mock
async def test_keyword_gap_asks_for_keywords_without_the_customer(
    client: paid_rankings.DataForSeoClient,
) -> None:
    routes = _mock_all()

    gap = await paid_data.keyword_gap(client, RIVAL, CUSTOMER)

    assert gap == [
        GapKeyword(
            phrase="dachrinne münster", volume=90, competitor_domain=RIVAL, competitor_position=3
        ),
        GapKeyword(
            phrase="gaube münster", volume=0, competitor_domain=RIVAL, competitor_position=None
        ),
    ]
    task = _task(routes["gap"].calls[0].request)
    assert (task["target1"], task["target2"], task["intersections"]) == (RIVAL, CUSTOMER, False)
    assert task["limit"] == 50


def test_merge_gaps_keeps_the_best_competitor_per_phrase() -> None:
    first = GapKeyword(phrase="Gaube", volume=40, competitor_domain="a.de", competitor_position=8)
    better = first.model_copy(update={"competitor_domain": "b.de", "competitor_position": 2})
    other = GapKeyword(phrase="Dach", volume=90, competitor_domain="a.de", competitor_position=None)
    assert paid_data.merge_gaps([[first, other], [better]]) == [other, better]


@respx.mock
async def test_historic_visibility_keeps_the_last_twelve_months(
    client: paid_rankings.DataForSeoClient,
) -> None:
    routes = _mock_all()

    history = await paid_data.historic_visibility(client, CUSTOMER, TODAY)

    assert len(history) == 12
    assert history[0] == ("2025-10", 15.0)
    assert history[-1] == ("2026-09", 14.0)
    assert _task(routes["history"].calls[0].request)["date_from"] == "2025-10-16"


@respx.mock
async def test_collect_paid_measures_customer_and_competitors(
    client: paid_rankings.DataForSeoClient,
) -> None:
    _mock_all()
    notes: list[str] = []

    snapshot = await paid_data.collect_paid(client, _request(), notes, TODAY)

    assert notes == []
    assert snapshot.customer_domain == CUSTOMER
    assert snapshot.volumes == {"Dachdecker Münster": 880}
    assert snapshot.primary_phrases == ["Dachdecker Münster"]
    assert set(snapshot.backlinks) == {CUSTOMER, RIVAL}
    assert snapshot.ranked_keywords == RANKED
    assert snapshot.keyword_gap is not None
    assert [gap.phrase for gap in snapshot.keyword_gap] == ["dachrinne münster", "gaube münster"]
    assert len(snapshot.historic_visibility) == 12


@respx.mock
async def test_collect_paid_turns_failed_parts_into_notes(
    client: paid_rankings.DataForSeoClient,
) -> None:
    routes = _mock_all()
    routes["volumes"].mock(return_value=_envelope(None, status=40501))
    routes["gap"].mock(return_value=_envelope(None, status=40400))
    routes["history"].mock(return_value=httpx.Response(500))
    routes["summary"].mock(return_value=_envelope([{"referring_domains": "viele"}]))
    notes: list[str] = []

    snapshot = await paid_data.collect_paid(client, _request(), notes, TODAY)

    assert notes == [
        "volumes: DataForSEO status 40501: x",
        f"backlinks {CUSTOMER}: DataForSEO result is malformed",
        f"backlinks {RIVAL}: DataForSEO result is malformed",
        f"keyword gap {RIVAL}: DataForSEO status 40400: x",
        "visibility history: DataForSEO answered HTTP 500",
    ]
    assert snapshot.volumes == {}
    assert snapshot.backlinks == {}
    assert snapshot.keyword_gap is None
    assert snapshot.historic_visibility == []
    assert snapshot.ranked_keywords == RANKED


@respx.mock
async def test_collect_paid_without_customer_website_or_competitors(
    client: paid_rankings.DataForSeoClient,
) -> None:
    routes = _mock_all()
    notes: list[str] = []

    snapshot = await paid_data.collect_paid(client, _request(customer_domain=None), notes, TODAY)

    assert snapshot.keyword_gap is None
    assert set(snapshot.backlinks) == {RIVAL, CUSTOMER}
    assert notes[-1].startswith("customer has no website")
    assert not routes["history"].called

    notes = []
    alone = await paid_data.collect_paid(client, _request(competitor_domains=[]), notes, TODAY)
    assert alone.keyword_gap is None
    assert notes == ["keyword gap: no competitor website known"]


@respx.mock
async def test_collect_paid_raises_nothing_on_transient_errors(
    client: paid_rankings.DataForSeoClient,
) -> None:
    routes = _mock_all()
    routes["ranked"].mock(return_value=httpx.Response(429))
    notes: list[str] = []

    snapshot = await paid_data.collect_paid(client, _request(), notes, TODAY)

    assert snapshot.ranked_keywords == {}
    assert sum("ranked keywords" in note for note in notes) == 2
