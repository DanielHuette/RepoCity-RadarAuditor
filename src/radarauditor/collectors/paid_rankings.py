"""Exact positions and search volume from DataForSEO, plus the map grid geometry.

Context: Paid provider behind the dataforseo source switch of the app settings (on by default);
the credentials come from the environment. Every task counts against the source's daily cap, and
the cost DataForSEO reports per request (US dollars, converted with DATAFORSEO_EUR_PER_USD) is
added to the month's spend, which stops further tasks at the monthly cost cap. Google searches
reach this module only when Google blocked the server browser.
Input: Phrases, coordinates, credentials.
Output: SerpResult, volumes, grid point coordinates and keys.
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from radarauditor.collectors.patterns import collapse_whitespace, site_key
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.serp import MAX_AI_MENTIONS, ai_overview_names
from radarauditor.collectors.snapshot import SerpResult
from radarauditor.collectors.transport import send
from radarauditor.config import Settings
from radarauditor.db.app_settings import Source
from radarauditor.errors import CollectionError, SourceDisabledError, TransientCollectionError

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.dataforseo.com"
ORGANIC_PATH = "/v3/serp/google/organic/live/advanced"
VOLUME_PATH = "/v3/keywords_data/google_ads/search_volume/live"
REQUEST_TIMEOUT_SECONDS = 120
CONCURRENT_REQUESTS = 4
LANGUAGE_CODE = "de"
GERMANY_LOCATION_CODE = 2276
SEARCH_ENGINE_DOMAIN = "google.de"
SERP_DEPTH = 20
ORGANIC_RADIUS = 200
GRID_SPACING_KM = 1.0
KM_PER_DEGREE_LATITUDE = 111.32
VOLUME_BATCH_SIZE = 1000
STATUS_OK = 20000
FIRST_SERVER_STATUS = 50000


class _Reference(BaseModel):
    source: str | None = None
    title: str | None = None
    domain: str | None = None


class _SerpItem(BaseModel):
    type: str
    rank_group: int | None = None
    domain: str | None = None
    title: str | None = None
    text: str | None = None
    markdown: str | None = None
    items: list["_SerpItem"] | None = None
    references: list[_Reference] | None = None


class _SerpPayload(BaseModel):
    items: list[_SerpItem] | None = None


class _VolumePayload(BaseModel):
    keyword: str
    search_volume: int | None = None


class _Task(BaseModel):
    status_code: int
    status_message: str = ""
    result: list[dict[str, Any]] | None = None


class _Envelope(BaseModel):
    status_code: int
    status_message: str = ""
    cost: Decimal = Decimal(0)
    tasks: list[_Task] = []


@dataclass(frozen=True)
class PaidSearchBatch:
    """Results of the phrases DataForSEO answered and the phrases it could not answer, with why."""

    results: list[SerpResult]
    failed: list[str]
    reason: str | None = None


def is_configured(settings: Settings) -> bool:
    """Return whether the DataForSEO credentials are set in the environment."""
    return bool(settings.dataforseo_login) and settings.dataforseo_password is not None


class DataForSeoClient:
    """Credentials and transport for the DataForSEO v3 API."""

    def __init__(self, settings: Settings, gate: SourceGate) -> None:
        """Take the credentials from settings.

        Args:
            settings: Runtime settings.
            gate: Source switches and caps of the job.

        Raises:
            SourceDisabledError: If the dataforseo source is switched off in the app settings.
            CollectionError: If the credentials are missing.
        """
        if not gate.is_enabled(Source.DATAFORSEO):
            raise SourceDisabledError(Source.DATAFORSEO.value, "switched off in the app settings")
        if not settings.dataforseo_login or settings.dataforseo_password is None:
            raise CollectionError("DataForSEO credentials are missing")
        self._login = settings.dataforseo_login
        self._password: SecretStr = settings.dataforseo_password
        self._eur_per_usd = settings.dataforseo_eur_per_usd
        self._gate = gate

    async def post(self, path: str, task: dict[str, Any]) -> list[dict[str, Any]]:
        """Send one task and return its result list.

        Args:
            path: Endpoint path below the API base URL.
            task: Task body.

        Returns:
            The `result` array of the task, empty when the task found nothing.

        Raises:
            TransientCollectionError: On rate limits or server errors after retries.
            DailyCapReachedError: If the daily cap of the dataforseo source is used up.
            MonthlyCostCapReachedError: If the month's spend reached the monthly cost cap.
            CollectionError: On rejected credentials, a rejected task or a malformed answer.
            StorageError: If the cap counter or the spend cannot be updated.
        """
        self._gate.acquire(Source.DATAFORSEO)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await send(
                client,
                "POST",
                f"{API_BASE_URL}{path}",
                source="DataForSEO",
                check=_check_envelope,
                auth=(self._login, self._password.get_secret_value()),
                json=[task],
            )
        envelope = _envelope(response)
        self._gate.record_cost(Source.DATAFORSEO, envelope.cost * self._eur_per_usd)
        return envelope.tasks[0].result or []


def _envelope(response: httpx.Response) -> _Envelope:
    try:
        return _Envelope.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise CollectionError("DataForSEO answer is malformed") from error


def _check_envelope(response: httpx.Response) -> None:
    envelope = _envelope(response)
    statuses = [(envelope.status_code, envelope.status_message)]
    statuses.extend((task.status_code, task.status_message) for task in envelope.tasks)
    for code, message in statuses:
        if code >= FIRST_SERVER_STATUS:
            raise TransientCollectionError(f"DataForSEO status {code}: {message}")
        if code != STATUS_OK:
            raise CollectionError(f"DataForSEO status {code}: {message}")
    if not envelope.tasks:
        raise CollectionError("DataForSEO answer contains no task")


async def search_each(
    client: DataForSeoClient,
    phrases: list[str],
    latitude: float | None,
    longitude: float | None,
) -> PaidSearchBatch:
    """Search every phrase on google.de in German, organic results with the local pack.

    The searches are made from the business location when its coordinates are known, else from
    Germany; the phrases carry the city. A phrase that cannot be searched (cap reached, rejected
    task, server error) is handed back as failed instead of failing the others.

    Args:
        client: DataForSEO client.
        phrases: Search phrases.
        latitude: Point the searches are made from, or None.
        longitude: Point the searches are made from, or None.

    Returns:
        Results in input order and the failed phrases in input order with the first reason.

    Raises:
        StorageError: If the cap counter or the spend cannot be updated.
    """
    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
    location = _search_location(latitude, longitude)

    async def search(phrase: str) -> SerpResult | CollectionError:
        task = {
            "keyword": phrase,
            "se_domain": SEARCH_ENGINE_DOMAIN,
            "language_code": LANGUAGE_CODE,
            "depth": SERP_DEPTH,
            **location,
        }
        try:
            async with semaphore:
                result = await client.post(ORGANIC_PATH, task)
            return _serp_from_items(phrase, _serp_items(result))
        except CollectionError as error:
            return error

    answers = await asyncio.gather(*(search(phrase) for phrase in phrases))
    return _search_batch(phrases, answers)


def _search_location(latitude: float | None, longitude: float | None) -> dict[str, object]:
    if latitude is not None and longitude is not None:
        return {"location_coordinate": f"{latitude:.7f},{longitude:.7f},{ORGANIC_RADIUS}"}
    return {"location_code": GERMANY_LOCATION_CODE}


def _search_batch(
    phrases: list[str], answers: list[SerpResult | CollectionError]
) -> PaidSearchBatch:
    results = [answer for answer in answers if isinstance(answer, SerpResult)]
    failures = [
        (phrase, answer)
        for phrase, answer in zip(phrases, answers, strict=True)
        if isinstance(answer, CollectionError)
    ]
    reason = str(failures[0][1]) if failures else None
    if failures:
        logger.warning("DataForSEO could not search %d of %d phrases", len(failures), len(phrases))
    return PaidSearchBatch(results, [phrase for phrase, _ in failures], reason)


def _serp_from_items(phrase: str, items: list[_SerpItem]) -> SerpResult:
    """Count organic positions by distinct domain in rank order, like the browser parser."""
    ranked = sorted(items, key=lambda item: item.rank_group or 0)
    local_names = [item.title for item in ranked if item.type == "local_pack" and item.title]
    overviews = [item for item in items if item.type == "ai_overview"]
    return SerpResult(
        phrase=phrase,
        organic=list(enumerate(_organic_domains(ranked), start=1)),
        local_pack=list(enumerate(dict.fromkeys(local_names), start=1)),
        ai_overview_present=bool(overviews),
        ai_overview_mentions=_overview_mentions(overviews),
        people_also_ask=_people_also_ask(items),
        measured_at=datetime.now(UTC),
        ads=_advertisers(ranked),
        knowledge_panel=any(item.type == "knowledge_graph" for item in items),
        route="dataforseo",
    )


def _organic_domains(ranked: list[_SerpItem]) -> list[str]:
    domains: list[str] = []
    for item in ranked:
        if item.type == "organic" and item.domain:
            domain = site_key(item.domain)
            if domain not in domains:
                domains.append(domain)
    return domains


def _people_also_ask(items: list[_SerpItem]) -> list[str]:
    questions = [
        collapse_whitespace(element.title)
        for item in items
        if item.type == "people_also_ask"
        for element in item.items or []
        if element.title
    ]
    return list(dict.fromkeys(questions))


def _advertisers(ranked: list[_SerpItem]) -> list[str]:
    advertisers = [
        site_key(item.domain) if item.domain else item.title
        for item in ranked
        if item.type == "paid" and (item.domain or item.title)
    ]
    return [advertiser for advertiser in dict.fromkeys(advertisers) if advertiser]


def _overview_mentions(overviews: list[_SerpItem]) -> list[str]:
    names: list[str] = []
    texts: list[str] = []
    for overview in overviews:
        for element in [overview, *(overview.items or [])]:
            names.extend(
                reference.source or reference.title or "" for reference in element.references or []
            )
            texts.append(element.markdown or element.text or "")
    names.extend(ai_overview_names(" ".join(texts)))
    unique = [name for name in dict.fromkeys(names) if name]
    return unique[:MAX_AI_MENTIONS]


def grid_key(index: int, grid_size: int) -> str:
    """Key "r{row}c{column}" of the point at index in the south-west-first order of grid_points.

    Args:
        index: Position in the list returned by grid_points.
        grid_size: Points per side.

    Returns:
        Key with 1-based row (1 = north) and column (1 = west).

    Raises:
        ValueError: If index lies outside the grid.
    """
    if not 0 <= index < grid_size * grid_size:
        raise ValueError(f"index {index} lies outside a {grid_size}x{grid_size} grid")
    row_from_south, column = divmod(index, grid_size)
    return f"r{grid_size - row_from_south}c{column + 1}"


def grid_points(latitude: float, longitude: float, grid_size: int) -> list[tuple[float, float]]:
    """Return grid_size x grid_size points spaced GRID_SPACING_KM apart around a centre.

    Args:
        latitude: Centre latitude.
        longitude: Centre longitude.
        grid_size: Points per side.

    Returns:
        Points row by row from south-west to north-east.

    Raises:
        ValueError: If grid_size is smaller than 1.
    """
    if grid_size < 1:
        raise ValueError(f"grid_size must be at least 1, got {grid_size}")
    latitude_step = GRID_SPACING_KM / KM_PER_DEGREE_LATITUDE
    longitude_step = GRID_SPACING_KM / (KM_PER_DEGREE_LATITUDE * math.cos(math.radians(latitude)))
    offsets = [index - (grid_size - 1) / 2 for index in range(grid_size)]
    return [
        (latitude + row * latitude_step, longitude + column * longitude_step)
        for row in offsets
        for column in offsets
    ]


async def monthly_volumes(client: DataForSeoClient, phrases: list[str]) -> dict[str, int]:
    """Return German monthly search volumes per phrase.

    Args:
        client: DataForSEO client.
        phrases: Search phrases.

    Returns:
        Volume per input phrase; phrases without a known volume are left out.

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: On rejected credentials or tasks.
    """
    by_lowercase = {phrase.lower(): phrase for phrase in phrases}
    volumes: dict[str, int] = {}
    for start in range(0, len(phrases), VOLUME_BATCH_SIZE):
        task = {
            "keywords": phrases[start : start + VOLUME_BATCH_SIZE],
            "location_code": GERMANY_LOCATION_CODE,
            "language_code": LANGUAGE_CODE,
        }
        for row in await client.post(VOLUME_PATH, task):
            entry = _parse(_VolumePayload, row)
            phrase = by_lowercase.get(entry.keyword.lower())
            if phrase is not None and entry.search_volume is not None:
                volumes[phrase] = entry.search_volume
    return volumes


def _serp_items(result: list[dict[str, Any]]) -> list[_SerpItem]:
    items: list[_SerpItem] = []
    for row in result:
        items.extend(_parse(_SerpPayload, row).items or [])
    return items


def _parse[ModelT: BaseModel](model: type[ModelT], row: dict[str, Any]) -> ModelT:
    try:
        return model.model_validate(row)
    except ValidationError as error:
        raise CollectionError("DataForSEO result is malformed") from error
