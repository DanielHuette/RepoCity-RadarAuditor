"""Tests for the collector of the customer's own Google data.

Context: Unit tests with respx for every Google API. | Input: Fixtures. | Output: Assertions.
"""

import json
from datetime import UTC, date, datetime
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from radarauditor.collectors import access
from radarauditor.db.models import CustomerAccess
from radarauditor.errors import CollectionError, TransientCollectionError

pytestmark = pytest.mark.usefixtures("fast_retries")

TOKEN = "ya29.token"
TODAY = date(2026, 10, 16)
LOCATION = "locations/4711"
ACCOUNT = "accounts/42"
SITE = "sc-domain:dachdeckerei-muster.de"
PERFORMANCE_URL = access.PERFORMANCE_URL.format(location=LOCATION)
SEARCH_URL = access.SEARCH_CONSOLE_URL.format(site="sc-domain%3Adachdeckerei-muster.de")
ANALYTICS_URL = access.ANALYTICS_URL.format(property_id="987654")
LOCATIONS_URL = access.LOCATIONS_URL.format(account=ACCOUNT)
REVIEWS_URL = access.REVIEWS_URL.format(account=ACCOUNT, location=LOCATION)


def _record(**overrides: object) -> CustomerAccess:
    values: dict[str, object] = {
        "customer_id": "00000000-0000-4000-8000-000000000001",
        "google_business_profile_granted": True,
        "search_console_property": SITE,
        "wordpress_url": None,
        "wordpress_user": None,
        "granted_at": datetime(2026, 10, 1, tzinfo=UTC),
        "google_location_name": LOCATION,
        "analytics_property_id": "987654",
    }
    values.update(overrides)
    return CustomerAccess.model_validate(values)


def _dated(day: date, value: str | None) -> dict[str, object]:
    entry: dict[str, object] = {"date": {"year": day.year, "month": day.month, "day": day.day}}
    if value is not None:
        entry["value"] = value
    return entry


PERFORMANCE_ANSWER = {
    "multiDailyMetricTimeSeries": [
        {
            "dailyMetricTimeSeries": [
                {
                    "dailyMetric": "CALL_CLICKS",
                    "timeSeries": {
                        "datedValues": [
                            _dated(date(2026, 8, 20), "4"),
                            _dated(date(2026, 9, 17), "2"),
                            _dated(date(2026, 9, 18), "5"),
                            _dated(date(2026, 10, 15), None),
                        ]
                    },
                },
                {
                    "dailyMetric": "WEBSITE_CLICKS",
                    "timeSeries": {"datedValues": [_dated(date(2026, 10, 1), "7")]},
                },
            ]
        }
    ]
}


def test_periods_cover_two_windows_of_28_days_before_today() -> None:
    current, previous = access.periods(TODAY)
    assert (current.start, current.end) == (date(2026, 9, 18), date(2026, 10, 15))
    assert (previous.start, previous.end) == (date(2026, 8, 21), date(2026, 9, 17))


@respx.mock
async def test_profile_metrics_split_the_two_periods() -> None:
    route = respx.get(PERFORMANCE_URL).respond(200, json=PERFORMANCE_ANSWER)

    now, before = await access.profile_metrics(TOKEN, LOCATION, TODAY)

    assert now["CALL_CLICKS"] == 5
    assert before["CALL_CLICKS"] == 6
    assert now["WEBSITE_CLICKS"] == 7
    assert set(now) == set(access.PROFILE_METRICS) == set(before)
    request = route.calls[0].request
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    query = parse_qs(request.url.query.decode())
    assert query["dailyMetrics"] == list(access.PROFILE_METRICS)
    assert query["dailyRange.startDate.day"] == ["21"]
    assert query["dailyRange.endDate.month"] == ["10"]


@respx.mock
async def test_profile_metrics_errors() -> None:
    respx.get(PERFORMANCE_URL).respond(403, json={"error": {"message": "Permission denied"}})
    with pytest.raises(CollectionError, match="Permission denied"):
        await access.profile_metrics(TOKEN, LOCATION, TODAY)
    respx.get(PERFORMANCE_URL).respond(200, json={"multiDailyMetricTimeSeries": "x"})
    with pytest.raises(CollectionError, match="malformed"):
        await access.profile_metrics(TOKEN, LOCATION, TODAY)
    respx.get(PERFORMANCE_URL).respond(200, json=[1])
    with pytest.raises(CollectionError, match="malformed"):
        await access.profile_metrics(TOKEN, LOCATION, TODAY)
    respx.get(PERFORMANCE_URL).respond(200, text="<html>")
    with pytest.raises(CollectionError, match="not JSON"):
        await access.profile_metrics(TOKEN, LOCATION, TODAY)
    respx.get(PERFORMANCE_URL).respond(429)
    with pytest.raises(TransientCollectionError):
        await access.profile_metrics(TOKEN, LOCATION, TODAY)


def _search_answer(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if "dimensions" in body:
        return httpx.Response(
            200,
            json={
                "rows": [
                    {"keys": ["dachsanierung münster"], "clicks": 4, "impressions": 90},
                    {
                        "keys": ["dachdecker münster"],
                        "clicks": 12,
                        "impressions": 400,
                        "ctr": 0.03,
                        "position": 4.2,
                    },
                ]
            },
        )
    return httpx.Response(
        200, json={"rows": [{"clicks": 30, "impressions": 900, "ctr": 0.033, "position": 7.5}]}
    )


@respx.mock
async def test_search_console_reads_queries_and_totals() -> None:
    route = respx.post(SEARCH_URL).mock(side_effect=_search_answer)

    queries, totals = await access.search_console(TOKEN, SITE, TODAY)

    assert [(query.query, query.clicks) for query in queries] == [
        ("dachdecker münster", 12),
        ("dachsanierung münster", 4),
    ]
    assert totals == {"clicks": 30, "impressions": 900, "ctr": 0.033, "position": 7.5}
    bodies = [json.loads(call.request.content) for call in route.calls]
    with_dimensions = next(body for body in bodies if "dimensions" in body)
    assert with_dimensions["rowLimit"] == 50
    assert with_dimensions["startDate"] == "2026-09-18"
    assert with_dimensions["endDate"] == "2026-10-15"


@respx.mock
async def test_search_console_without_traffic() -> None:
    respx.post(SEARCH_URL).respond(200, json={})
    queries, totals = await access.search_console(TOKEN, SITE, TODAY)
    assert queries == []
    assert totals == {"clicks": 0.0, "impressions": 0.0}


@respx.mock
async def test_analytics_reads_the_three_totals() -> None:
    route = respx.post(ANALYTICS_URL).respond(
        200,
        json={
            "metricHeaders": [
                {"name": "sessions"},
                {"name": "engagedSessions"},
                {"name": "keyEvents"},
            ],
            "rows": [{"metricValues": [{"value": "640"}, {"value": "410"}, {"value": "23"}]}],
        },
    )

    snapshot = await access.analytics(TOKEN, "987654", TODAY)

    assert (snapshot.sessions, snapshot.engaged_sessions, snapshot.key_events) == (640, 410, 23)
    body = json.loads(route.calls[0].request.content)
    assert body["metrics"] == [{"name": name} for name in access.ANALYTICS_METRICS]


@respx.mock
async def test_analytics_without_rows_and_with_bad_values() -> None:
    respx.post(ANALYTICS_URL).respond(200, json={"metricHeaders": []})
    snapshot = await access.analytics(TOKEN, "987654", TODAY)
    assert (snapshot.sessions, snapshot.key_events) == (0, 0)
    respx.post(ANALYTICS_URL).respond(
        200,
        json={
            "metricHeaders": [{"name": "sessions"}],
            "rows": [{"metricValues": [{"value": "viele"}]}],
        },
    )
    with pytest.raises(CollectionError, match="not a number"):
        await access.analytics(TOKEN, "987654", TODAY)


def _mock_accounts(locations_pages: list[dict[str, object]]) -> respx.Route:
    respx.get(access.ACCOUNTS_URL).respond(
        200, json={"accounts": [{"name": "accounts/1"}, {"name": ACCOUNT}]}
    )
    respx.get(access.LOCATIONS_URL.format(account="accounts/1")).respond(200, json={})
    return respx.get(LOCATIONS_URL).mock(
        side_effect=[httpx.Response(200, json=page) for page in locations_pages]
    )


REVIEW_PAGE_ONE = {
    "reviews": [
        {
            "starRating": "FIVE",
            "comment": "Top",
            "createTime": "2026-09-01T10:00:00Z",
            "reviewReply": {"comment": "Danke", "updateTime": "2026-09-02T08:00:00Z"},
        },
        {"starRating": "TWO", "createTime": "2026-09-05T10:00:00Z"},
    ],
    "nextPageToken": "page-2",
}


@respx.mock
async def test_owner_reviews_resolve_the_account_and_keep_replies() -> None:
    locations = _mock_accounts(
        [
            {"locations": [{"name": "locations/1"}], "nextPageToken": "next"},
            {"locations": [{"name": LOCATION}]},
        ]
    )
    reviews = respx.get(REVIEWS_URL).mock(
        side_effect=[
            httpx.Response(200, json=REVIEW_PAGE_ONE),
            httpx.Response(200, json={"reviews": [{"starRating": "STAR_RATING_UNSPECIFIED"}]}),
        ]
    )

    found = await access.owner_reviews(TOKEN, LOCATION)

    assert found[0] == {
        "publishTime": "2026-09-01T10:00:00Z",
        "rating": 5.0,
        "text": "Top",
        "reviewReply": {"text": "Danke", "updateTime": "2026-09-02T08:00:00Z"},
    }
    assert found[1]["reviewReply"] is None
    assert found[2]["rating"] is None
    assert len(found) == 3
    assert parse_qs(locations.calls[1].request.url.query.decode())["pageToken"] == ["next"]
    assert parse_qs(reviews.calls[1].request.url.query.decode())["pageToken"] == ["page-2"]


@respx.mock
async def test_owner_reviews_for_a_location_of_another_account() -> None:
    _mock_accounts([{"locations": [{"name": "locations/1"}]}])
    with pytest.raises(CollectionError, match="not managed by the authorised"):
        await access.owner_reviews(TOKEN, LOCATION)


def _mock_everything() -> None:
    respx.get(PERFORMANCE_URL).respond(200, json=PERFORMANCE_ANSWER)
    respx.post(SEARCH_URL).mock(side_effect=_search_answer)
    respx.post(ANALYTICS_URL).respond(
        200,
        json={
            "metricHeaders": [{"name": "sessions"}, {"name": "keyEvents"}],
            "rows": [{"metricValues": [{"value": "5"}, {"value": "1"}]}],
        },
    )
    _mock_accounts([{"locations": [{"name": LOCATION}]}])
    respx.get(REVIEWS_URL).respond(200, json={"reviews": REVIEW_PAGE_ONE["reviews"]})


@respx.mock
async def test_collect_access_reads_every_source() -> None:
    _mock_everything()
    notes: list[str] = []

    snapshot = await access.collect_access(TOKEN, _record(), notes, TODAY)

    assert notes == []
    assert snapshot.profile_metrics["CALL_CLICKS"] == 5
    assert snapshot.profile_metrics_previous["CALL_CLICKS"] == 6
    assert len(snapshot.search_queries) == 2
    assert snapshot.search_totals["clicks"] == 30
    assert snapshot.analytics is not None
    assert snapshot.analytics.key_events == 1
    assert len(snapshot.owner_reviews) == 2


@respx.mock
async def test_collect_access_notes_missing_ids_and_failures() -> None:
    respx.post(SEARCH_URL).respond(403, json={"error": {"message": "User does not have access"}})
    notes: list[str] = []
    record = _record(google_location_name=None, analytics_property_id=None)

    snapshot = await access.collect_access(TOKEN, record, notes, TODAY)

    assert notes == [
        "profile: no identifier recorded for the customer",
        "search console: Search Console rejected the request with HTTP 403: "
        "User does not have access",
        "analytics: no identifier recorded for the customer",
        "reviews: no identifier recorded for the customer",
    ]
    assert snapshot.profile_metrics == {}
    assert snapshot.search_totals == {}
    assert snapshot.analytics is None
    assert snapshot.owner_reviews == []
