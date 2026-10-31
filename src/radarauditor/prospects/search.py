"""Run one waiting customer search: read the list from Google, rank it, store it.

Context: The worker calls run_next_search once per loop, before it claims a job, so a search waits
at most for the job that is running. Every page of the Text Search is one request of the source
"prospect_search", which has its own switch, daily cap and monthly cap in euros, so searching never
uses up the caps of the audits (source "places"). Each answered page books the list price of a Text
Search Enterprise request (places.SEARCH_PROSPECTS_SKU) through SourceGate.record_cost. A cap or
switch that stops the first page fails the search with a German sentence; one that stops a later
page keeps the pages read so far with a note. Nothing is sent to any business.
Input: Repositories, Settings, a page reader (places.search_prospects outside tests).
Output: Stored results or the recorded failure.
"""

import logging
from collections.abc import Awaitable, Callable

from pydantic import SecretStr

from radarauditor.collectors import places
from radarauditor.collectors.places import ProspectPage, ProspectPlace
from radarauditor.collectors.quota import SourceGate
from radarauditor.config import Settings
from radarauditor.db.app_settings import WORKER_SETTINGS_KEYS, Source, parse_worker_settings
from radarauditor.db.models import ProspectSearch
from radarauditor.db.repositories import Repositories
from radarauditor.errors import (
    CollectionError,
    ConfigurationError,
    DailyCapReachedError,
    MonthlyCostCapReachedError,
    RadarError,
    SourceDisabledError,
    SourceUnavailableError,
)
from radarauditor.prospects.assessment import assess
from radarauditor.prospects.criteria import ProspectCriteria, load_criteria

logger = logging.getLogger(__name__)

PageReader = Callable[[str, SecretStr, str | None], Awaitable[ProspectPage]]

SWITCHED_OFF = "Die Kundensuche ist in den Einstellungen abgeschaltet."
DAILY_CAP = "Das Tageslimit der Kundensuche ist erreicht."
MONTHLY_CAP = "Das Monatslimit der Kundensuche in Euro ist erreicht."
GOOGLE_FAILED = "Die Google-Schnittstelle hat die Suche abgelehnt oder nicht beantwortet."
CRITERIA_UNREADABLE = "Die Kriterien der Kundensuche sind auf dem Server nicht lesbar."
SETTINGS_UNREADABLE = "Die Einstellungen der Datenquellen sind nicht lesbar."


class _StoppedError(Exception):
    """A page could not be read; carries the German reason."""

    def __init__(self, reason: str) -> None:
        """Keep the reason the app shows."""
        super().__init__(reason)
        self.reason = reason


def search_phrase(search: ProspectSearch) -> str:
    """What is typed into Google Maps: "<trade> in <city>"."""
    return f"{search.trade} in {search.city}"


async def run_next_search(
    repositories: Repositories,
    settings: Settings,
    read_page: PageReader = places.search_prospects,
) -> bool:
    """Claim and run the oldest waiting customer search; failures are recorded, not raised.

    Args:
        repositories: Database access.
        settings: Google key and the path of the search criteria.
        read_page: Reads one page of the Text Search; replaced in tests.

    Returns:
        True when a search was claimed, False when none waited or claiming failed.
    """
    try:
        claimed = repositories.claim_prospect_search()
    except RadarError as exc:
        logger.error("Customer searches could not be claimed: %s", exc)
        return False
    if claimed is None:
        return False
    try:
        await _run(claimed, repositories, settings, read_page)
    except RadarError as exc:
        logger.error("Customer search %s could not be stored: %s", claimed.id, exc)
    return True


async def _run(
    search: ProspectSearch,
    repositories: Repositories,
    settings: Settings,
    read_page: PageReader,
) -> None:
    try:
        criteria = load_criteria(settings.prospect_criteria_path)
    except ConfigurationError as exc:
        logger.error("Customer search %s cannot start: %s", search.id, exc)
        repositories.fail_prospect_search(search.id, CRITERIA_UNREADABLE)
        return
    try:
        gate = _gate(repositories)
    except ConfigurationError as exc:
        logger.error("Customer search %s cannot start: %s", search.id, exc)
        repositories.fail_prospect_search(search.id, SETTINGS_UNREADABLE)
        return
    found, pages, stop = await _read_pages(search, criteria, gate, settings, read_page)
    if pages == 0:
        repositories.fail_prospect_search(search.id, stop or GOOGLE_FAILED)
        return
    note = f"Nur {pages} von bis zu {criteria.max_pages} Seiten gelesen: {stop}" if stop else None
    results = [result.model_dump(mode="json") for result in assess(found, criteria)]
    stored = repositories.finish_prospect_search(search.id, results, note)
    logger.info("Customer search %s stored %d businesses from %d pages", search.id, stored, pages)


def _gate(repositories: Repositories) -> SourceGate:
    sources = parse_worker_settings(repositories.settings_values(WORKER_SETTINGS_KEYS)).sources
    return SourceGate(sources, repositories.consume_source_quota, repositories)


async def _read_pages(
    search: ProspectSearch,
    criteria: ProspectCriteria,
    gate: SourceGate,
    settings: Settings,
    read_page: PageReader,
) -> tuple[list[ProspectPlace], int, str | None]:
    """Read up to max_pages pages; returns the places, the pages read and why reading stopped."""
    found: list[ProspectPlace] = []
    token: str | None = None
    for pages in range(criteria.max_pages):
        try:
            page = await _one_page(search, gate, settings, read_page, token)
        except _StoppedError as stop:
            return found, pages, stop.reason
        found.extend(page.places)
        token = page.next_page_token
        if token is None:
            return found, pages + 1, None
    return found, criteria.max_pages, None


async def _one_page(
    search: ProspectSearch,
    gate: SourceGate,
    settings: Settings,
    read_page: PageReader,
    token: str | None,
) -> ProspectPage:
    try:
        gate.acquire(Source.PROSPECT_SEARCH)
    except SourceDisabledError as exc:
        raise _StoppedError(SWITCHED_OFF) from exc
    except MonthlyCostCapReachedError as exc:
        raise _StoppedError(MONTHLY_CAP) from exc
    except (DailyCapReachedError, SourceUnavailableError) as exc:
        raise _StoppedError(DAILY_CAP) from exc
    try:
        page = await read_page(search_phrase(search), settings.google_api_key, token)
    except CollectionError as exc:
        logger.warning("Customer search %s: Google answered with an error: %s", search.id, exc)
        raise _StoppedError(GOOGLE_FAILED) from exc
    cost = places.request_cost_eur(places.SEARCH_PROSPECTS_SKU, settings.google_eur_per_usd)
    gate.record_cost(Source.PROSPECT_SEARCH, cost)
    return page
