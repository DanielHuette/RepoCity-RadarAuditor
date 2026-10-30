"""Read the customer's own Google data: profile performance, Search Console, Analytics, reviews.

Context: Runs only for audits with customer access, with an access token of the operator's Google
account that the customer added to its profile and Search Console property. Every source is
optional: a missing identifier or a failing call adds a note and leaves its fields empty.
Input: Access token, CustomerAccess, reference date.
Output: AccessSnapshot.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.alias_generators import to_camel

from radarauditor.collectors.snapshot import AccessSnapshot, AnalyticsSnapshot, SearchQuery
from radarauditor.collectors.transport import send
from radarauditor.db.models import CustomerAccess
from radarauditor.errors import CollectionError

logger = logging.getLogger(__name__)

PERFORMANCE_URL = (
    "https://businessprofileperformance.googleapis.com/v1/{location}"
    ":fetchMultiDailyMetricsTimeSeries"
)
SEARCH_CONSOLE_URL = (
    "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
)
ANALYTICS_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
LOCATIONS_URL = "https://mybusinessbusinessinformation.googleapis.com/v1/{account}/locations"
REVIEWS_URL = "https://mybusiness.googleapis.com/v4/{account}/{location}/reviews"
REQUEST_TIMEOUT_SECONDS = 30
PERIOD_DAYS = 28
QUERY_LIMIT = 50
LOCATION_PAGE_SIZE = 100
MAX_LOCATION_PAGES = 10
REVIEW_PAGE_SIZE = 50
MAX_REVIEW_PAGES = 4
PROFILE_METRICS = (
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "CALL_CLICKS",
    "WEBSITE_CLICKS",
    "BUSINESS_DIRECTION_REQUESTS",
    "BUSINESS_CONVERSATIONS",
    "BUSINESS_BOOKINGS",
)
ANALYTICS_METRICS = ("sessions", "engagedSessions", "keyEvents")
STAR_RATINGS = {"ONE": 1.0, "TWO": 2.0, "THREE": 3.0, "FOUR": 4.0, "FIVE": 5.0}


class _ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class _Date(_ApiModel):
    year: int
    month: int
    day: int


class _DatedValue(_ApiModel):
    date: _Date
    value: str | None = None


class _TimeSeries(_ApiModel):
    dated_values: list[_DatedValue] = []


class _MetricSeries(_ApiModel):
    daily_metric: str
    time_series: _TimeSeries = _TimeSeries()


class _MultiSeries(_ApiModel):
    daily_metric_time_series: list[_MetricSeries] = []


class _PerformanceResponse(_ApiModel):
    multi_daily_metric_time_series: list[_MultiSeries] = []


class _SearchRow(_ApiModel):
    keys: list[str] = []
    clicks: float = 0.0
    impressions: float = 0.0
    ctr: float = 0.0
    position: float = 0.0


class _SearchResponse(_ApiModel):
    rows: list[_SearchRow] = []


class _MetricValue(_ApiModel):
    value: str = "0"


class _ReportRow(_ApiModel):
    metric_values: list[_MetricValue] = []


class _Header(_ApiModel):
    name: str


class _ReportResponse(_ApiModel):
    metric_headers: list[_Header] = []
    rows: list[_ReportRow] = []


class _Named(_ApiModel):
    name: str


class _AccountsResponse(_ApiModel):
    accounts: list[_Named] = []
    next_page_token: str | None = None


class _LocationsResponse(_ApiModel):
    locations: list[_Named] = []
    next_page_token: str | None = None


class _Reply(_ApiModel):
    comment: str = ""
    update_time: str | None = None


class _Review(_ApiModel):
    name: str | None = None
    star_rating: str | None = None
    comment: str = ""
    create_time: str | None = None
    review_reply: _Reply | None = None


class _ReviewsResponse(_ApiModel):
    reviews: list[_Review] = []
    next_page_token: str | None = None


@dataclass(frozen=True)
class _Window:
    start: date
    end: date


def periods(today: date) -> tuple[_Window, _Window]:
    """The last PERIOD_DAYS full days before today and the PERIOD_DAYS before those.

    Args:
        today: Reference day, not included.

    Returns:
        Current and previous window.
    """
    end = today - timedelta(days=1)
    current = _Window(start=end - timedelta(days=PERIOD_DAYS - 1), end=end)
    previous_end = current.start - timedelta(days=1)
    previous = _Window(start=previous_end - timedelta(days=PERIOD_DAYS - 1), end=previous_end)
    return current, previous


async def _request(
    method: str, url: str, token: str, source: str, **arguments: Any
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await send(client, method, url, source=source, headers=headers, **arguments)
    try:
        payload = response.json()
    except ValueError as error:
        raise CollectionError(f"{source} answer is not JSON") from error
    if not isinstance(payload, dict):
        raise CollectionError(f"{source} answer is malformed")
    return payload


def _parse[ModelT: BaseModel](model: type[ModelT], payload: dict[str, Any], source: str) -> ModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise CollectionError(f"{source} answer is malformed") from error


def _range_parameters(window: _Window) -> list[tuple[str, str | int]]:
    parameters: list[tuple[str, str | int]] = []
    for edge, day in (("startDate", window.start), ("endDate", window.end)):
        parameters.extend(
            [
                (f"dailyRange.{edge}.year", day.year),
                (f"dailyRange.{edge}.month", day.month),
                (f"dailyRange.{edge}.day", day.day),
            ]
        )
    return parameters


async def profile_metrics(
    token: str, location_name: str, today: date
) -> tuple[dict[str, int], dict[str, int]]:
    """Sum the Business Profile daily metrics of the last 28 days and the 28 days before.

    Args:
        token: OAuth access token with scope business.manage.
        location_name: Location as "locations/<id>".
        today: Reference day, not included.

    Returns:
        Sums per metric for the current and the previous period; every metric is present.

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: On missing permission or a malformed answer.
    """
    current, previous = periods(today)
    parameters: list[tuple[str, str | int]] = [("dailyMetrics", name) for name in PROFILE_METRICS]
    parameters.extend(_range_parameters(_Window(start=previous.start, end=current.end)))
    source = "Business Profile Performance"
    payload = await _request(
        "GET", PERFORMANCE_URL.format(location=location_name), token, source, params=parameters
    )
    answer = _parse(_PerformanceResponse, payload, source)
    now = dict.fromkeys(PROFILE_METRICS, 0)
    before = dict.fromkeys(PROFILE_METRICS, 0)
    for multi in answer.multi_daily_metric_time_series:
        for series in multi.daily_metric_time_series:
            for dated in series.time_series.dated_values:
                day = date(dated.date.year, dated.date.month, dated.date.day)
                target = now if day >= current.start else before
                target[series.daily_metric] = target.get(series.daily_metric, 0) + int(
                    dated.value or 0
                )
    return now, before


async def search_console(
    token: str, site: str, today: date
) -> tuple[list[SearchQuery], dict[str, float]]:
    """Top queries by clicks and site totals from Search Console for the last 28 days.

    Args:
        token: OAuth access token with scope webmasters.readonly.
        site: Property as named in Search Console, e.g. "sc-domain:example.de".
        today: Reference day, not included.

    Returns:
        Up to QUERY_LIMIT queries ordered by clicks, and clicks, impressions, ctr and position
        of the whole site (only clicks and impressions, both 0, when there was no traffic).

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: On missing permission or a malformed answer.
    """
    window, _ = periods(today)
    url = SEARCH_CONSOLE_URL.format(site=quote(site, safe=""))
    body: dict[str, Any] = {
        "startDate": window.start.isoformat(),
        "endDate": window.end.isoformat(),
        "type": "web",
    }
    source = "Search Console"
    query_payload, total_payload = await asyncio.gather(
        _request(
            "POST",
            url,
            token,
            source,
            json={**body, "dimensions": ["query"], "rowLimit": QUERY_LIMIT},
        ),
        _request("POST", url, token, source, json=body),
    )
    queries = _top_queries(_parse(_SearchResponse, query_payload, source).rows)
    return queries, _site_totals(_parse(_SearchResponse, total_payload, source).rows)


def _top_queries(rows: list[_SearchRow]) -> list[SearchQuery]:
    queries = [
        SearchQuery(
            query=row.keys[0],
            clicks=round(row.clicks),
            impressions=round(row.impressions),
            ctr=row.ctr,
            position=row.position,
        )
        for row in rows
        if row.keys
    ]
    return sorted(queries, key=lambda query: (-query.clicks, -query.impressions))[:QUERY_LIMIT]


def _site_totals(rows: list[_SearchRow]) -> dict[str, float]:
    if not rows:
        return {"clicks": 0.0, "impressions": 0.0}
    total = rows[0]
    return {
        "clicks": total.clicks,
        "impressions": total.impressions,
        "ctr": total.ctr,
        "position": total.position,
    }


async def analytics(token: str, property_id: str, today: date) -> AnalyticsSnapshot:
    """Sessions, engaged sessions and key events of the last 28 days from the GA4 Data API.

    Args:
        token: OAuth access token with scope analytics.readonly.
        property_id: Numeric GA4 property id.
        today: Reference day, not included.

    Returns:
        The three totals; zero when the property recorded nothing.

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: On missing permission or a malformed answer.
    """
    window, _ = periods(today)
    body = {
        "dateRanges": [{"startDate": window.start.isoformat(), "endDate": window.end.isoformat()}],
        "metrics": [{"name": name} for name in ANALYTICS_METRICS],
    }
    source = "Google Analytics"
    url = ANALYTICS_URL.format(property_id=quote(property_id, safe=""))
    answer = _parse(_ReportResponse, await _request("POST", url, token, source, json=body), source)
    values = dict.fromkeys(ANALYTICS_METRICS, 0)
    if answer.rows:
        names = [header.name for header in answer.metric_headers]
        for name, metric in zip(names, answer.rows[0].metric_values, strict=False):
            try:
                values[name] = round(float(metric.value))
            except ValueError as error:
                raise CollectionError(f"{source} value of {name} is not a number") from error
    return AnalyticsSnapshot(
        property_id=property_id,
        sessions=values["sessions"],
        engaged_sessions=values["engagedSessions"],
        key_events=values["keyEvents"],
    )


async def account_of(token: str, location_name: str) -> str:
    """Find the account of the operator under which the location is listed.

    Args:
        token: OAuth access token with scope business.manage.
        location_name: Location as "locations/<id>".

    Returns:
        Account as "accounts/<id>".

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: If no account of the operator manages the location.
    """
    source = "Business Profile accounts"
    accounts = _parse(
        _AccountsResponse, await _request("GET", ACCOUNTS_URL, token, source), source
    ).accounts
    for account in accounts:
        page_token: str | None = None
        for _ in range(MAX_LOCATION_PAGES):
            parameters: dict[str, str | int] = {"readMask": "name", "pageSize": LOCATION_PAGE_SIZE}
            if page_token:
                parameters["pageToken"] = page_token
            url = LOCATIONS_URL.format(account=account.name)
            page = _parse(
                _LocationsResponse,
                await _request("GET", url, token, source, params=parameters),
                source,
            )
            if any(location.name == location_name for location in page.locations):
                return account.name
            page_token = page.next_page_token
            if not page_token:
                break
    raise CollectionError(f"{location_name} is not managed by the authorised Google account")


def _review(review: _Review) -> dict[str, object]:
    """Keep a review in the shape of the Places reviews, plus the owner's reply and its id.

    reviewId (last part of the review name) is what a reply is posted to.
    """
    reply = review.review_reply
    shaped: dict[str, object] = {
        "publishTime": review.create_time,
        "rating": STAR_RATINGS.get(review.star_rating or ""),
        "text": review.comment,
        "reviewReply": (
            {"text": reply.comment, "updateTime": reply.update_time} if reply is not None else None
        ),
    }
    if review.name:
        shaped["reviewId"] = review.name.rsplit("/", 1)[-1]
    return shaped


async def owner_reviews(token: str, location_name: str) -> list[dict[str, object]]:
    """Newest reviews of the location with the owner's replies, up to four pages.

    Args:
        token: OAuth access token with scope business.manage.
        location_name: Location as "locations/<id>".

    Returns:
        Reviews with publishTime, rating, text, reviewReply and, when Google names it, reviewId.

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: If the location is not reachable or an answer is malformed.
    """
    account = await account_of(token, location_name)
    url = REVIEWS_URL.format(account=account, location=location_name)
    source = "Business Profile reviews"
    reviews: list[dict[str, object]] = []
    page_token: str | None = None
    for _ in range(MAX_REVIEW_PAGES):
        parameters: dict[str, str | int] = {
            "pageSize": REVIEW_PAGE_SIZE,
            "orderBy": "updateTime desc",
        }
        if page_token:
            parameters["pageToken"] = page_token
        page = _parse(
            _ReviewsResponse,
            await _request("GET", url, token, source, params=parameters),
            source,
        )
        reviews.extend(_review(review) for review in page.reviews)
        page_token = page.next_page_token
        if not page_token:
            break
    return reviews


type _Outcome[T] = tuple[T | None, str | None]


async def _attempt[T](
    label: str, identifier: str | None, call: Callable[[str], Awaitable[T]]
) -> _Outcome[T]:
    if not identifier:
        return None, f"{label}: no identifier recorded for the customer"
    try:
        return await call(identifier), None
    except CollectionError as error:
        return None, f"{label}: {error}"


async def collect_access(
    token: str, access: CustomerAccess, notes: list[str], today: date | None = None
) -> AccessSnapshot:
    """Read every source the customer's access record names.

    Args:
        token: OAuth access token of the operator.
        access: Identifiers recorded for the customer.
        notes: Receives one line per source that is missing or failed.
        today: Reference day; defaults to the current UTC date.

    Returns:
        Everything that could be read; fields of failed sources stay empty.
    """
    day = today or datetime.now(UTC).date()
    profile, queries, report, reviews = await _read_sources(token, access, day)
    notes.extend(note for _, note in (profile, queries, report, reviews) if note is not None)
    metrics, previous = profile[0] or ({}, {})
    search_queries, search_totals = queries[0] or ([], {})
    logger.info(
        "Read customer access data: %d profile metrics, %d queries, %d reviews",
        len(metrics),
        len(search_queries),
        len(reviews[0] or []),
    )
    return AccessSnapshot(
        profile_metrics=metrics,
        profile_metrics_previous=previous,
        search_queries=search_queries,
        search_totals=search_totals,
        analytics=report[0],
        owner_reviews=reviews[0] or [],
        collected_at=datetime.now(UTC),
    )


async def _read_sources(
    token: str, access: CustomerAccess, day: date
) -> tuple[
    _Outcome[tuple[dict[str, int], dict[str, int]]],
    _Outcome[tuple[list[SearchQuery], dict[str, float]]],
    _Outcome[AnalyticsSnapshot],
    _Outcome[list[dict[str, object]]],
]:
    """Profile metrics, Search Console, Analytics and reviews, read concurrently."""
    location = access.google_location_name
    return await asyncio.gather(
        _attempt("profile", location, lambda name: profile_metrics(token, name, day)),
        _attempt(
            "search console",
            access.search_console_property,
            lambda site: search_console(token, site, day),
        ),
        _attempt(
            "analytics",
            access.analytics_property_id,
            lambda property_id: analytics(token, property_id, day),
        ),
        _attempt("reviews", location, lambda name: owner_reviews(token, name)),
    )
