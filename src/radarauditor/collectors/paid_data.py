"""Search volumes, link profiles and keyword data of customer and competitors from DataForSEO.

Context: Runs when the dataforseo source is switched on in the app settings (the default), after
the competitors are known; feeds checks c2, c12, i1, i2, i3, i5 and j5. Every part is optional: a
failing part (also a reached daily or monthly cost cap) adds a note and leaves its field empty
(keyword_gap None).
Input: DataForSEO client and a PaidRequest.
Output: PaidSnapshot.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from radarauditor.collectors.paid_rankings import (
    CONCURRENT_REQUESTS,
    GERMANY_LOCATION_CODE,
    LANGUAGE_CODE,
    DataForSeoClient,
    monthly_volumes,
)
from radarauditor.collectors.patterns import site_key
from radarauditor.collectors.snapshot import BacklinkSummary, GapKeyword, PaidSnapshot
from radarauditor.errors import CollectionError

logger = logging.getLogger(__name__)

SUMMARY_PATH = "/v3/backlinks/summary/live"
REFERRING_DOMAINS_PATH = "/v3/backlinks/referring_domains/live"
RANKED_KEYWORDS_PATH = "/v3/dataforseo_labs/google/ranked_keywords/live"
INTERSECTION_PATH = "/v3/dataforseo_labs/google/domain_intersection/live"
HISTORY_PATH = "/v3/dataforseo_labs/google/historical_rank_overview/live"
REFERRING_DOMAINS_LIMIT = 100
GAP_LIMIT = 50
HISTORY_MONTHS = 12
DAYS_PER_YEAR = 365
SPAM_SCORE_LIMIT = 50
LOCAL_TLD = ".de"
UMLAUT_SPELLINGS = (
    str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}),
    str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"}),
)


class PaidRequest(BaseModel):
    """What to measure: the customer's domain, competitor domains and keyword phrases."""

    customer_domain: str | None
    competitor_domains: list[str]
    phrases: list[str]
    primary_phrases: list[str]
    city: str


class _Summary(BaseModel):
    referring_domains: int | None = 0
    backlinks_spam_score: int | None = None


class _ReferringDomain(BaseModel):
    domain: str
    backlinks_spam_score: int | None = None


class _ReferringDomains(BaseModel):
    items: list[_ReferringDomain] | None = None


class _Total(BaseModel):
    total_count: int | None = 0


class _KeywordInfo(BaseModel):
    search_volume: int | None = None


class _KeywordData(BaseModel):
    keyword: str
    keyword_info: _KeywordInfo = Field(default_factory=_KeywordInfo)


class _SerpElement(BaseModel):
    rank_group: int | None = None


class _IntersectionItem(BaseModel):
    keyword_data: _KeywordData
    first_domain_serp_element: _SerpElement | None = None


class _Intersection(BaseModel):
    items: list[_IntersectionItem] | None = None


class _Organic(BaseModel):
    pos_1: int = 0
    pos_2_3: int = 0
    pos_4_10: int = 0


class _Metrics(BaseModel):
    organic: _Organic = Field(default_factory=_Organic)


class _HistoryItem(BaseModel):
    year: int
    month: int
    metrics: _Metrics = Field(default_factory=_Metrics)


class _History(BaseModel):
    items: list[_HistoryItem] | None = None


def _parse[ModelT: BaseModel](model: type[ModelT], result: list[dict[str, Any]]) -> ModelT:
    try:
        return model.model_validate(result[0] if result else {})
    except ValidationError as error:
        raise CollectionError("DataForSEO result is malformed") from error


def city_spellings(city: str) -> set[str]:
    """Ways a city name appears in a domain: umlauts as ae or a, without spaces or hyphens."""
    lowered = city.strip().lower()
    spellings = {lowered.translate(table) for table in UMLAUT_SPELLINGS}
    spellings.add(lowered)
    return {spelling.replace(" ", "").replace("-", "") for spelling in spellings if spelling}


def is_local_domain(domain: str, city: str) -> bool:
    """True for a .de domain or one whose name contains the city."""
    host = domain.lower().rstrip(".")
    if host.endswith(LOCAL_TLD):
        return True
    compact = host.replace("-", "")
    return any(spelling in compact for spelling in city_spellings(city))


async def backlink_summary(client: DataForSeoClient, domain: str, city: str) -> BacklinkSummary:
    """Referring domains, spam score, and local and spam share of the top referring domains.

    Args:
        client: DataForSEO client.
        domain: Domain without www.
        city: City whose name marks a referring domain as local.

    Returns:
        Summary; shares are 0.0 for a domain without referring domains.

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: On a rejected task or malformed answer.
    """
    summary_rows, referring_rows = await asyncio.gather(
        client.post(SUMMARY_PATH, {"target": domain, "include_subdomains": True}),
        client.post(
            REFERRING_DOMAINS_PATH,
            {
                "target": domain,
                "limit": REFERRING_DOMAINS_LIMIT,
                "order_by": ["rank,desc"],
            },
        ),
    )
    summary = _parse(_Summary, summary_rows)
    referring = _parse(_ReferringDomains, referring_rows).items or []
    local = sum(1 for item in referring if is_local_domain(item.domain, city))
    spam = sum(
        1
        for item in referring
        if item.backlinks_spam_score is not None and item.backlinks_spam_score >= SPAM_SCORE_LIMIT
    )
    return BacklinkSummary(
        domain=domain,
        referring_domains=summary.referring_domains or 0,
        referring_domains_local_share=round(local / len(referring), 3) if referring else 0.0,
        spam_score=summary.backlinks_spam_score,
        referring_domains_spam_share=round(spam / len(referring), 3) if referring else 0.0,
    )


async def ranked_keyword_count(client: DataForSeoClient, domain: str) -> int:
    """Number of keywords the domain ranks for in German Google.

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: On a rejected task or malformed answer.
    """
    task = {
        "target": domain,
        "location_code": GERMANY_LOCATION_CODE,
        "language_code": LANGUAGE_CODE,
        "limit": 1,
    }
    return _parse(_Total, await client.post(RANKED_KEYWORDS_PATH, task)).total_count or 0


async def keyword_gap(client: DataForSeoClient, competitor: str, customer: str) -> list[GapKeyword]:
    """Keywords the competitor ranks for and the customer does not, highest volume first.

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: On a rejected task or malformed answer.
    """
    task = {
        "target1": competitor,
        "target2": customer,
        "intersections": False,
        "location_code": GERMANY_LOCATION_CODE,
        "language_code": LANGUAGE_CODE,
        "limit": GAP_LIMIT,
        "order_by": ["keyword_data.keyword_info.search_volume,desc"],
    }
    items = _parse(_Intersection, await client.post(INTERSECTION_PATH, task)).items or []
    return [
        GapKeyword(
            phrase=item.keyword_data.keyword,
            volume=item.keyword_data.keyword_info.search_volume or 0,
            competitor_domain=competitor,
            competitor_position=(
                item.first_domain_serp_element.rank_group
                if item.first_domain_serp_element is not None
                else None
            ),
        )
        for item in items
    ]


def merge_gaps(gaps: list[list[GapKeyword]]) -> list[GapKeyword]:
    """One entry per phrase (the competitor ranking best), highest volume first, GAP_LIMIT max."""
    best: dict[str, GapKeyword] = {}
    for gap in gaps:
        for keyword in gap:
            key = keyword.phrase.casefold()
            known = best.get(key)
            if known is None or _position(keyword) < _position(known):
                best[key] = keyword
    ordered = sorted(best.values(), key=lambda keyword: (-keyword.volume, keyword.phrase))
    return ordered[:GAP_LIMIT]


def _position(keyword: GapKeyword) -> int:
    return keyword.competitor_position if keyword.competitor_position is not None else 10**6


async def historic_visibility(
    client: DataForSeoClient, domain: str, today: date
) -> list[tuple[str, float]]:
    """Top-10 keyword count per month over the last HISTORY_MONTHS months, oldest first.

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: On a rejected task or malformed answer.
    """
    task = {
        "target": domain,
        "location_code": GERMANY_LOCATION_CODE,
        "language_code": LANGUAGE_CODE,
        "date_from": (today - timedelta(days=DAYS_PER_YEAR)).isoformat(),
    }
    items = _parse(_History, await client.post(HISTORY_PATH, task)).items or []
    points = sorted(
        (
            f"{item.year:04d}-{item.month:02d}",
            float(
                item.metrics.organic.pos_1
                + item.metrics.organic.pos_2_3
                + item.metrics.organic.pos_4_10
            ),
        )
        for item in items
    )
    return points[-HISTORY_MONTHS:]


type _Outcome[T] = tuple[T | None, str | None]


async def _part[T](label: str, call: Callable[[], Awaitable[T]]) -> _Outcome[T]:
    try:
        return await call(), None
    except CollectionError as error:
        return None, f"{label}: {error}"


async def collect_paid(
    client: DataForSeoClient, request: PaidRequest, notes: list[str], today: date | None = None
) -> PaidSnapshot:
    """Measure volumes, links, ranked keywords, the keyword gap and the visibility history.

    Args:
        client: DataForSEO client.
        request: Domains and phrases to measure.
        notes: Receives one line per part that failed or could not run, in a fixed order.
        today: Reference day of the history; defaults to the current UTC date.

    Returns:
        Everything that could be measured.
    """
    day = today or datetime.now(UTC).date()
    customer = request.customer_domain
    competitors = [domain for domain in request.competitor_domains if domain != customer]
    domains = [domain for domain in [customer, *competitors] if domain]
    volumes = await _part("volumes", lambda: monthly_volumes(client, request.phrases))
    summaries, counts = await _domain_profiles(client, domains, request.city)
    gap, history, later_notes = await _gap_and_history(client, customer, competitors, day)
    notes.extend(note for _, note in [volumes, *summaries, *counts] if note is not None)
    notes.extend(later_notes)
    if customer is None:
        notes.append("customer has no website; links and keyword data cover competitors only")
    snapshot = PaidSnapshot(
        customer_domain=customer,
        volumes=volumes[0] or {},
        primary_phrases=request.primary_phrases,
        backlinks={value.domain: value for value, _ in summaries if value is not None},
        ranked_keywords={
            domain: count
            for domain, (count, _) in zip(domains, counts, strict=True)
            if count is not None
        },
        keyword_gap=gap,
        historic_visibility=history,
        collected_at=datetime.now(UTC),
    )
    _log_snapshot(snapshot)
    return snapshot


async def _domain_profiles(
    client: DataForSeoClient, domains: list[str], city: str
) -> tuple[list[_Outcome[BacklinkSummary]], list[_Outcome[int]]]:
    """Link summary and ranked keyword count per domain, CONCURRENT_REQUESTS calls at a time."""
    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)

    async def limited[T](label: str, call: Callable[[], Awaitable[T]]) -> _Outcome[T]:
        async with semaphore:
            return await _part(label, call)

    def summary_of(domain: str) -> Callable[[], Awaitable[BacklinkSummary]]:
        return lambda: backlink_summary(client, domain, city)

    def count_of(domain: str) -> Callable[[], Awaitable[int]]:
        return lambda: ranked_keyword_count(client, domain)

    summaries = await asyncio.gather(
        *(limited(f"backlinks {domain}", summary_of(domain)) for domain in domains)
    )
    counts = await asyncio.gather(
        *(limited(f"ranked keywords {domain}", count_of(domain)) for domain in domains)
    )
    return summaries, counts


def _log_snapshot(snapshot: PaidSnapshot) -> None:
    gap = snapshot.keyword_gap
    logger.info(
        "Paid data: %d volumes, %d link profiles, %s gap keywords",
        len(snapshot.volumes),
        len(snapshot.backlinks),
        "no" if gap is None else len(gap),
    )


async def _gap_and_history(
    client: DataForSeoClient, customer: str | None, competitors: list[str], day: date
) -> tuple[list[GapKeyword] | None, list[tuple[str, float]], list[str]]:
    """Gap against every competitor and the customer's history, with notes in a fixed order."""
    if customer is None:
        return None, [], []

    def gap_with(competitor: str) -> Callable[[], Awaitable[list[GapKeyword]]]:
        return lambda: keyword_gap(client, competitor, customer)

    gaps = await asyncio.gather(
        *(_part(f"keyword gap {competitor}", gap_with(competitor)) for competitor in competitors)
    )
    history, history_note = await _part(
        "visibility history", lambda: historic_visibility(client, customer, day)
    )
    notes = [note for _, note in gaps if note is not None]
    if history_note is not None:
        notes.append(history_note)
    if not competitors:
        notes.append("keyword gap: no competitor website known")
    measured = [gap for gap, _ in gaps if gap is not None]
    if not measured:
        return None, history or [], notes
    return merge_gaps(measured), history or [], notes


def domain_of(url: str | None) -> str | None:
    """Domain of a website address without scheme, path and leading www."""
    if not url:
        return None
    text = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return site_key(text) or None
