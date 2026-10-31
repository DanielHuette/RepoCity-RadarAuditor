"""Run all collectors for one audit and store the RawAudit.

Context: Entry point of a COLLECT job.
Input: Audit id.
Output: RawAudit JSON uploaded to storage.
"""

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from radarauditor.collectors import (
    access,
    assistants,
    directories,
    google_auth,
    identify,
    pagespeed,
    paid_data,
    paid_rankings,
    serp,
    wayback,
    website,
)
from radarauditor.collectors.browser import BrowserService
from radarauditor.collectors.maps import ProfileTarget
from radarauditor.collectors.profiles import Business, ProfileRoute
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import (
    AccessSnapshot,
    AssistantAnswer,
    DeferredSearch,
    DirectoryHit,
    GridPoint,
    IdentifiedValue,
    MapRanking,
    MapResultList,
    PageSpeedSnapshot,
    PaidSnapshot,
    PlaceSnapshot,
    RawAudit,
    SerpResult,
    WebsiteSnapshot,
)
from radarauditor.config import Settings
from radarauditor.db.app_settings import Source
from radarauditor.db.models import AuditKind, Competitor, Customer, Keyword, Location
from radarauditor.db.repositories import Repositories
from radarauditor.errors import (
    CollectionError,
    ScoringError,
    SourceUnavailableError,
    StorageError,
)
from radarauditor.industries import load_industries
from radarauditor.scoring.catalog import load_catalogue

logger = logging.getLogger(__name__)

DIRECTORY_CATALOGUE = Path("config") / "verzeichnisse.yaml"
COMPETITOR_COUNT = 3
GRID_CHECK_ID = "j3"


@dataclass(frozen=True)
class _Context:
    customer: Customer
    location: Location | None
    competitors: list[Competitor]
    keywords: list[Keyword]
    settings: Settings
    kind: AuditKind
    repositories: Repositories
    gate: SourceGate
    browser: BrowserService
    profiles: ProfileRoute

    @property
    def paid_switched_on(self) -> bool:
        """DataForSEO is switched on in the app settings; missing credentials become a warning."""
        return self.gate.is_enabled(Source.DATAFORSEO)

    @property
    def paid_active(self) -> bool:
        """DataForSEO is switched on and its credentials are set."""
        return self.paid_switched_on and paid_rankings.is_configured(self.settings)


@dataclass(frozen=True)
class _Outcome[T]:
    value: T | None
    warnings: list[str]


@dataclass(frozen=True)
class _ProfileOutcomes:
    place: _Outcome[PlaceSnapshot]
    competitors: _Outcome[list[PlaceSnapshot]]
    map_rankings: _Outcome[list[MapRanking]]
    map_results: _Outcome[MapResultList]
    grid: _Outcome[list[GridPoint]]


@dataclass(frozen=True)
class _SearchOutcomes:
    google: _Outcome[list[SerpResult]]
    bing: _Outcome[list[SerpResult]]
    images: _Outcome[list[SerpResult]]
    deferred: list[DeferredSearch]

    def serps(self) -> list[SerpResult]:
        """Results of Google, Bing and Google Images, in that order."""
        return (self.google.value or []) + (self.bing.value or []) + (self.images.value or [])


@dataclass(frozen=True)
class _SourceOutcomes:
    speed: _Outcome[PageSpeedSnapshot]
    profiles: _ProfileOutcomes
    searches: _SearchOutcomes
    listings: _Outcome[list[DirectoryHit]]
    answers: _Outcome[list[AssistantAnswer]]
    own_data: _Outcome[AccessSnapshot]
    domain_age: _Outcome[date | None]
    paid: _Outcome[PaidSnapshot]

    def warnings(self) -> list[str]:
        """Warnings of every source, in the order the report lists the sources."""
        profiles, searches = self.profiles, self.searches
        outcomes = (
            self.speed,
            profiles.place,
            profiles.competitors,
            profiles.map_rankings,
            profiles.map_results,
            profiles.grid,
            searches.google,
            searches.bing,
            searches.images,
            self.listings,
            self.answers,
            self.own_data,
            self.paid,
            self.domain_age,
        )
        return [warning for outcome in outcomes for warning in outcome.warnings]


@dataclass
class _GoogleState:
    """Google searches left for a serp_retry job, and why Google blocked the browser if it did."""

    deferred: list[DeferredSearch] = field(default_factory=list)
    blocked_reason: str | None = None


async def collect_audit(
    audit_id: UUID, repositories: Repositories, settings: Settings, gate: SourceGate
) -> RawAudit:
    """Collect website, speed, place, competitors, searches, directories and assistants.

    Google is searched in the browser for every keyword and the business name; Bing and Google
    Images for the primary keywords. When Google blocks the browser, the web searches it did not
    answer run at once through the DataForSEO SERP API; only when DataForSEO is switched off,
    lacks credentials or reaches a cap are they listed in deferred_searches for a serp_retry job.
    An audit started with only the website address is identified first (name, address, industry,
    keywords; see collectors.identify) and the values are stored before the other collectors run.
    The Google profile, the competitors (the top three on Google Maps for the primary keyword
    unless recorded), the Maps position per keyword, the whole Maps result list of the primary
    keyword with review counts and the map grid around the location are read
    on Google Maps in the browser, from the Places API when Maps blocks it. With DataForSEO
    switched on, volumes, links and keyword data come from DataForSEO; the volumes are written
    back to the keywords. The Wayback Machine gives the first capture of the customer's domain.
    Audits of kind with_access also read the customer's own Google data with the stored
    authorisation. A failing optional source adds a warning; a failing website crawl fails the
    audit. Sources switched off in the app settings are not asked; a source whose daily cap runs
    out stops and is listed in capped_sources, one whose monthly cost cap is reached in
    cost_capped_sources. All browser collectors share one BrowserService on the profile of the
    settings.

    Args:
        audit_id: Audit to collect for.
        repositories: Database access.
        settings: Runtime settings.
        gate: Source switches and caps of the job.

    Returns:
        Everything collected, with one warning per failed or incomplete source.

    Raises:
        CollectionError: If the website crawl fails.
        IdentificationError: If an audit without a city finds none on the website or on Google.
        StorageError: If the audit, customer or access record cannot be loaded, or identified
            values cannot be stored.
        ConfigurationError: If the directory catalogue or the question templates are
            unreadable.
    """
    audit = repositories.audit(audit_id)
    customer = repositories.customer(audit.customer_id)
    locations = repositories.locations(customer.id)
    async with BrowserService(settings.browser_profile_dir) as browser:
        context = _Context(
            customer=customer,
            location=locations[0] if locations else None,
            competitors=repositories.competitors(customer.id),
            keywords=repositories.keywords(customer.id),
            settings=settings,
            kind=audit.kind,
            repositories=repositories,
            gate=gate,
            browser=browser,
            profiles=ProfileRoute(
                browser, gate, settings.google_api_key, settings.google_eur_per_usd
            ),
        )
        logger.info("Collecting audit %s for %s", audit_id, customer.name)
        return await _collect(audit_id, context)


async def _collect(audit_id: UUID, context: _Context) -> RawAudit:
    warnings: list[str] = []
    site = await _crawl(context, warnings)
    context, identified = await _identify(context, site, warnings)
    sources = await _collect_sources(context)
    warnings.extend(sources.warnings())
    for warning in warnings:
        logger.warning("Audit %s: %s", audit_id, warning)
    profiles, searches = sources.profiles, sources.searches
    return RawAudit(
        website=site,
        pagespeed=sources.speed.value,
        place=profiles.place.value,
        competitor_places=profiles.competitors.value or [],
        serps=searches.serps(),
        directories=sources.listings.value or [],
        assistant_answers=sources.answers.value or [],
        collected_at=datetime.now(UTC),
        warnings=warnings,
        grid=profiles.grid.value or [],
        access=sources.own_data.value,
        paid=sources.paid.value,
        capped_sources=sorted(context.gate.exhausted),
        cost_capped_sources=sorted(context.gate.cost_capped),
        deferred_searches=searches.deferred,
        domain_first_capture=sources.domain_age.value,
        map_rankings=profiles.map_rankings.value or [],
        map_results=profiles.map_results.value,
        identified=identified,
    )


async def _collect_sources(context: _Context) -> _SourceOutcomes:
    """Ask every source after the crawl and the identify step; independent sources run at once."""
    (
        speed,
        profiles,
        searches,
        listings,
        answers,
        (own_data, domain_age),
    ) = await asyncio.gather(
        _optional("pagespeed", _pagespeed, context),
        _google_profiles(context),
        _searches(context),
        _optional("directories", _directories, context),
        _optional("assistants", _assistants, context),
        asyncio.gather(_customer_access(context), _optional("wayback", _first_capture, context)),
    )
    paid = await _paid_outcome(context, profiles.competitors.value or [], profiles.place.value)
    return _SourceOutcomes(speed, profiles, searches, listings, answers, own_data, domain_age, paid)


async def _identify(
    context: _Context, site: WebsiteSnapshot | None, warnings: list[str]
) -> tuple[_Context, list[IdentifiedValue]]:
    """Fill in name, city, industry and keywords an audit started with only the website lacks.

    Raises:
        IdentificationError: If no source names the city of the business.
        ConfigurationError: If the industry groups are unreadable.
        StorageError: If the identified values cannot be written.
    """
    if not identify.needs_identification(context.customer, context.keywords):
        return context, []
    notes: list[str] = []
    result = await identify.identify_customer(
        context.customer,
        context.location,
        context.keywords,
        site,
        repositories=context.repositories,
        industries=load_industries(context.settings.industries_path),
        gate=context.gate,
        api_key=context.settings.google_api_key,
        notes=notes,
        eur_per_usd=context.settings.google_eur_per_usd,
    )
    warnings.extend(f"identify: {note}" for note in notes)
    if not result.identified:
        return context, []
    for value in result.identified:
        logger.info("Identified %s from %s: %s", value.field, value.source, value.value)
    updated = dataclasses.replace(
        context, customer=result.customer, location=result.location, keywords=result.keywords
    )
    return updated, result.identified


async def _crawl(context: _Context, warnings: list[str]) -> WebsiteSnapshot | None:
    if context.customer.website is None:
        warnings.append("website: customer has no website")
        return None
    return await website.crawl_website(context.customer.website)


async def _optional[T](
    step: str, action: Callable[[_Context, list[str]], Awaitable[T]], context: _Context
) -> _Outcome[T]:
    notes: list[str] = []
    try:
        value = await action(context, notes)
    except CollectionError as error:
        return _Outcome(None, [*(f"{step}: {note}" for note in notes), f"{step}: {error}"])
    return _Outcome(value, [f"{step}: {note}" for note in notes])


async def _first_capture(context: _Context, notes: list[str]) -> date | None:
    site = context.customer.website
    domain = paid_data.domain_of(str(site) if site is not None else None)
    if domain is None:
        return None
    return await wayback.first_capture(domain)


async def _pagespeed(context: _Context, notes: list[str]) -> PageSpeedSnapshot:
    if context.customer.website is None:
        raise CollectionError("customer has no website")
    return await pagespeed.measure(context.customer.website, context.settings.google_api_key)


async def _google_profiles(context: _Context) -> _ProfileOutcomes:
    place = await _optional("places", _own_place, context)
    own = _business(context, place.value)

    async def competitors(inner: _Context, notes: list[str]) -> list[PlaceSnapshot]:
        return await _competitors(inner, notes, own)

    async def rankings(inner: _Context, notes: list[str]) -> list[MapRanking]:
        return await _map_rankings(inner, notes, own)

    async def results(inner: _Context, notes: list[str]) -> MapResultList:
        return await _map_results(inner, notes, own)

    async def grid(inner: _Context, notes: list[str]) -> list[GridPoint]:
        return await _grid(inner, notes, own)

    return _ProfileOutcomes(
        place,
        await _optional("competitors", competitors, context),
        await _optional("map_rankings", rankings, context),
        await _optional("map_results", results, context),
        await _optional("grid", grid, context),
    )


def _business(context: _Context, own_place: PlaceSnapshot | None) -> Business:
    location = context.location
    ids = {own_place.place_id} if own_place is not None else set()
    if location is not None and location.google_place_id:
        ids.add(location.google_place_id)
    return Business(
        name=own_place.name if own_place is not None else context.customer.name,
        address=_address(location) if location is not None else context.customer.city,
        city=context.customer.city,
        place_ids=frozenset(ids),
    )


def _address(location: Location) -> str:
    return f"{location.street}, {location.postal_code} {location.city}"


async def _own_place(context: _Context, notes: list[str]) -> PlaceSnapshot:
    location = _require_location(context)
    target = ProfileTarget(
        location.name, _address(location), location.google_place_id, with_photos=True
    )
    place = await context.profiles.profile(target, notes)
    if place is None:
        raise CollectionError(f"no Google Business Profile found for {location.name}")
    return place


async def _competitors(context: _Context, notes: list[str], own: Business) -> list[PlaceSnapshot]:
    found: list[PlaceSnapshot] = []
    try:
        for competitor in context.competitors:
            await _add_competitor(context, notes, competitor, found)
        if len(found) < COMPETITOR_COUNT:
            found.extend(await _ranked_competitors(context, notes, own, found))
    except SourceUnavailableError as error:
        notes.append(str(error))
    return found


async def _add_competitor(
    context: _Context, notes: list[str], competitor: Competitor, found: list[PlaceSnapshot]
) -> None:
    target = ProfileTarget(competitor.name, context.customer.city, competitor.google_place_id)
    try:
        place = await context.profiles.profile(target, notes)
    except SourceUnavailableError:
        raise
    except CollectionError as error:
        notes.append(f"{competitor.name}: {error}")
        return
    if place is None:
        notes.append(f"{competitor.name} not found on Google")
        return
    found.append(place)


async def _ranked_competitors(
    context: _Context, notes: list[str], own: Business, known: list[PlaceSnapshot]
) -> list[PlaceSnapshot]:
    keyword = _primary_keyword(context.keywords)
    centre = _centre(context)
    if keyword is None or centre is None:
        notes.append("no primary keyword or location coordinates to find ranked competitors")
        return []
    return await context.profiles.competitors(
        _phrase(keyword), centre, known, own, COMPETITOR_COUNT - len(known), notes
    )


async def _map_rankings(context: _Context, notes: list[str], own: Business) -> list[MapRanking]:
    phrases = list(dict.fromkeys(_phrase(keyword) for keyword in context.keywords))
    if not phrases:
        raise CollectionError("customer has no keywords")
    centre = _require_centre(context)
    return [await context.profiles.map_ranking(phrase, centre, own, notes) for phrase in phrases]


async def _map_results(context: _Context, notes: list[str], own: Business) -> MapResultList:
    keyword = _primary_keyword(context.keywords)
    if keyword is None:
        raise CollectionError("customer has no keywords")
    centre = _require_centre(context)
    return await context.profiles.result_list(_phrase(keyword), centre, own, notes)


async def _grid(context: _Context, notes: list[str], own: Business) -> list[GridPoint]:
    keyword = _primary_keyword(context.keywords)
    if keyword is None:
        raise CollectionError("customer has no keywords")
    centre = _require_centre(context)
    size = _grid_size(context.settings)
    return await context.profiles.grid(_phrase(keyword), centre, own, size, notes)


def _grid_size(settings: Settings) -> int:
    """Points per side of the map grid, from the threshold "grid" of check j3."""
    try:
        catalogue = load_catalogue(settings.catalogue_path)
    except ScoringError as error:
        raise CollectionError(f"grid size unreadable: {error}") from error
    for area in catalogue.areas:
        for check in area.checks:
            size = check.thresholds.get("grid")
            if check.id == GRID_CHECK_ID and isinstance(size, int):
                return size
    raise CollectionError(f"catalogue check {GRID_CHECK_ID} has no integer threshold grid")


def _phrase(keyword: Keyword) -> str:
    return f"{keyword.phrase} {keyword.city}"


def _centre(context: _Context) -> tuple[float, float] | None:
    location = context.location
    if location is None or location.latitude is None or location.longitude is None:
        return None
    return location.latitude, location.longitude


def _require_centre(context: _Context) -> tuple[float, float]:
    location = _require_location(context)
    if location.latitude is None or location.longitude is None:
        raise CollectionError("location has no coordinates for the map searches")
    return location.latitude, location.longitude


async def _searches(context: _Context) -> _SearchOutcomes:
    state = _GoogleState()

    async def google(inner: _Context, notes: list[str]) -> list[SerpResult]:
        return await _google_searches(inner, notes, state)

    async def images(inner: _Context, notes: list[str]) -> list[SerpResult]:
        return await _image_searches(inner, notes, state)

    google_outcome = await _optional("serps", google, context)
    bing = await _optional("bing", _bing_searches, context)
    images_outcome = await _optional("images", images, context)
    return _SearchOutcomes(google_outcome, bing, images_outcome, state.deferred)


async def _google_searches(
    context: _Context, notes: list[str], state: _GoogleState
) -> list[SerpResult]:
    phrases = [f"{keyword.phrase} {keyword.city}" for keyword in context.keywords]
    if not phrases:
        raise CollectionError("customer has no keywords")
    brand = context.customer.name.strip()
    # The brand search feeds the brand ranking, knowledge panel and competitor-ad checks.
    if brand and brand.casefold() not in {phrase.casefold() for phrase in phrases}:
        phrases.append(brand)
    granted = _granted_google_phrases(context, phrases, notes)
    batch = await serp.search_all(context.browser, granted)
    if batch.deferred:
        state.blocked_reason = batch.reason or "Google blocked the browser"
        batch = await _search_blocked_through_dataforseo(context, batch, notes)
    _defer(batch, "web", state.deferred, notes)
    return batch.results


async def _search_blocked_through_dataforseo(
    context: _Context, batch: serp.SearchBatch, notes: list[str]
) -> serp.SearchBatch:
    """Run the phrases Google blocked through the DataForSEO SERP API right away, within its caps.

    Returns:
        The browser results plus the DataForSEO results; deferred keeps the phrases DataForSEO
        could not search, all of them when it is switched off or lacks credentials.
    """
    if not context.paid_active:
        return batch
    location = context.location
    latitude = location.latitude if location is not None else None
    longitude = location.longitude if location is not None else None
    client = paid_rankings.DataForSeoClient(context.settings, context.gate)
    paid = await paid_rankings.search_each(client, batch.deferred, latitude, longitude)
    if paid.results:
        notes.append(
            f"Google blocked the browser, {len(paid.results)} of {len(batch.deferred)} searches "
            "measured through DataForSEO"
        )
    reason = f"{batch.reason}; DataForSEO: {paid.reason}" if paid.failed else None
    return serp.SearchBatch([*batch.results, *paid.results], paid.failed, reason)


def _granted_google_phrases(context: _Context, phrases: list[str], notes: list[str]) -> list[str]:
    """Phrases the daily cap of Google searches still allows, in order."""
    granted = context.gate.acquire_up_to(Source.GOOGLE_SEARCH, len(phrases))
    if granted < len(phrases):
        notes.append(f"daily cap reached, {len(phrases) - granted} of {len(phrases)} not searched")
    return phrases[:granted]


def _defer(
    batch: serp.SearchBatch,
    vertical: Literal["web", "images"],
    deferred: list[DeferredSearch],
    notes: list[str],
) -> None:
    if not batch.deferred:
        return
    deferred.extend(DeferredSearch(phrase=phrase, vertical=vertical) for phrase in batch.deferred)
    notes.append(serp.deferral_note(len(batch.deferred), batch.reason))


async def _bing_searches(context: _Context, notes: list[str]) -> list[SerpResult]:
    phrases = _main_phrases(context)
    batch = await serp.search_bing(context.browser, phrases)
    if batch.deferred:
        notes.append(f"{len(batch.deferred)} of {len(phrases)} not searched: {batch.reason}")
    return batch.results


async def _image_searches(
    context: _Context, notes: list[str], state: _GoogleState
) -> list[SerpResult]:
    phrases = _main_phrases(context)
    if state.blocked_reason is not None:
        # Google already blocks this browser; another request now would only prolong the block.
        blocked = serp.SearchBatch([], phrases, "Google blocked the web searches before")
        _defer(blocked, "images", state.deferred, notes)
        return []
    batch = await serp.search_images(
        context.browser, _granted_google_phrases(context, phrases, notes)
    )
    _defer(batch, "images", state.deferred, notes)
    return batch.results


def _main_phrases(context: _Context) -> list[str]:
    primary = [keyword for keyword in context.keywords if keyword.is_primary]
    chosen = primary or context.keywords[:1]
    if not chosen:
        raise CollectionError("customer has no keywords")
    return [f"{keyword.phrase} {keyword.city}" for keyword in chosen]


async def _directories(context: _Context, notes: list[str]) -> list[DirectoryHit]:
    location = _require_location(context)
    hits = await directories.check_directories(location, DIRECTORY_CATALOGUE)
    searchable = [
        entry.name
        for entry in directories.load_catalogue(DIRECTORY_CATALOGUE).directories
        if entry.search_url is not None
    ]
    checked = {hit.directory for hit in hits}
    unchecked = [name for name in searchable if name not in checked]
    if unchecked:
        notes.append(f"not checked: {', '.join(unchecked)}")
    return hits


async def _assistants(context: _Context, notes: list[str]) -> list[AssistantAnswer]:
    providers = assistants.enabled_providers(context.settings, context.gate)
    if not providers:
        return []
    customer = context.customer
    primary = next((keyword.phrase for keyword in context.keywords if keyword.is_primary), "")
    keyword_trade = assistants.trade_from_keyword(primary, customer.city)
    if keyword_trade is not None and keyword_trade.casefold() in customer.name.casefold():
        keyword_trade = None
    trade = keyword_trade or load_industries(context.settings.industries_path).question_term(
        customer.industry
    )
    if not trade:
        raise CollectionError("neither keywords nor industry name the trade to ask about")
    services = list(
        dict.fromkeys(
            keyword.phrase
            for keyword in sorted(context.keywords, key=lambda item: not item.is_primary)
            if keyword.phrase.lower() not in {trade.lower(), customer.industry.lower()}
        )
    )
    templates = assistants.load_question_templates(context.settings.questions_path)
    questions = assistants.build_questions(trade, customer.city, services, templates)
    answers = await assistants.ask_all(
        questions, customer.name, context.settings, context.gate, context.browser
    )
    answered = {answer.provider for answer in answers}
    silent = [provider for provider in providers if provider not in answered]
    if silent:
        notes.append(f"no answers from {', '.join(silent)}")
    return answers


async def _customer_access(context: _Context) -> _Outcome[AccessSnapshot]:
    if context.kind is not AuditKind.WITH_ACCESS:
        return _Outcome(None, [])
    return await _optional("access", _read_access, context)


async def _read_access(context: _Context, notes: list[str]) -> AccessSnapshot:
    record = context.repositories.customer_access(context.customer.id)
    if record is None:
        raise CollectionError("customer has recorded no access")
    token = await google_auth.access_token(context.repositories, context.settings)
    return await access.collect_access(token, record, notes)


async def _paid_outcome(
    context: _Context, rivals: list[PlaceSnapshot], own_place: PlaceSnapshot | None
) -> _Outcome[PaidSnapshot]:
    if not context.paid_switched_on:
        return _Outcome(None, [])

    async def measure(inner: _Context, notes: list[str]) -> PaidSnapshot:
        return await _paid(inner, notes, rivals, own_place)

    return await _optional("paid", measure, context)


async def _paid(
    context: _Context,
    notes: list[str],
    rivals: list[PlaceSnapshot],
    own_place: PlaceSnapshot | None,
) -> PaidSnapshot:
    client = paid_rankings.DataForSeoClient(context.settings, context.gate)
    own_site = context.customer.website or (own_place.website if own_place else None)
    phrases = {keyword.id: f"{keyword.phrase} {keyword.city}" for keyword in context.keywords}
    request = paid_data.PaidRequest(
        customer_domain=paid_data.domain_of(str(own_site) if own_site else None),
        competitor_domains=_competitor_domains(context, rivals),
        phrases=list(dict.fromkeys(phrases.values())),
        primary_phrases=[phrases[keyword.id] for keyword in context.keywords if keyword.is_primary],
        city=context.customer.city,
    )
    snapshot = await paid_data.collect_paid(client, request, notes)
    for keyword in context.keywords:
        volume = snapshot.volumes.get(phrases[keyword.id])
        if volume is None or volume == keyword.monthly_volume:
            continue
        try:
            context.repositories.update_keyword_volume(keyword.id, volume)
        except StorageError as error:
            notes.append(f"volume of {keyword.phrase} not saved: {error}")
    return snapshot


def _competitor_domains(context: _Context, rivals: list[PlaceSnapshot]) -> list[str]:
    """Websites of the recorded competitors first, then of the compared Google profiles."""
    sites = [str(item.website) for item in context.competitors if item.website is not None]
    sites.extend(str(place.website) for place in rivals if place.website is not None)
    domains = [domain for site in sites if (domain := paid_data.domain_of(site)) is not None]
    return list(dict.fromkeys(domains))[:COMPETITOR_COUNT]


def _require_location(context: _Context) -> Location:
    if context.location is None:
        raise CollectionError("customer has no location")
    return context.location


def _primary_keyword(keywords: list[Keyword]) -> Keyword | None:
    return next((keyword for keyword in keywords if keyword.is_primary), None) or (
        keywords[0] if keywords else None
    )
