"""Tests for the collection run of one audit.

Context: Unit tests with every collector monkeypatched. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import HttpUrl, SecretStr

from radarauditor.collectors import (
    access,
    assistants,
    directories,
    google_auth,
    maps,
    pagespeed,
    paid_data,
    paid_rankings,
    places,
    run,
    serp,
    wayback,
    website,
)
from radarauditor.collectors.browser import BrowserService, Geolocation
from radarauditor.collectors.maps import ProfileTarget
from radarauditor.collectors.maps_pages import MapListing
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import (
    AccessSnapshot,
    AssistantAnswer,
    DeferredSearch,
    DirectoryHit,
    PageSpeedSnapshot,
    PaidSnapshot,
    PlaceSnapshot,
    SerpResult,
    WebsiteSnapshot,
)
from radarauditor.config import Settings
from radarauditor.db.app_settings import Source, SourcesSettings
from radarauditor.db.models import (
    Audit,
    AuditKind,
    AuditStatus,
    Competitor,
    Customer,
    CustomerAccess,
    Keyword,
    Location,
)
from radarauditor.errors import (
    AuthorizationError,
    CollectionError,
    ProfileNotFoundError,
    SearchBlockedError,
    StorageError,
    TransientCollectionError,
)

CUSTOMER_ID = uuid4()
AUDIT_ID = uuid4()
SITE = HttpUrl("https://www.dachdecker-hollenbeck.de/")
CATALOGUE = Path(__file__).parents[1] / "config" / "verzeichnisse.yaml"
FIRST_CAPTURE = date(2011, 4, 2)


def _customer(website_url: HttpUrl | None = SITE) -> Customer:
    return Customer(
        id=CUSTOMER_ID,
        name="Dachdeckerei Hollenbeck GmbH",
        website=website_url,
        industry="Dachdecker",
        city="Münster",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def _location(place_id: str | None = None) -> Location:
    return Location(
        id=uuid4(),
        customer_id=CUSTOMER_ID,
        name="Dachdeckerei Hollenbeck GmbH",
        street="Hiltruper Straße 210",
        postal_code="48165",
        city="Münster",
        phone="0251 123456",
        google_place_id=place_id,
        latitude=51.9,
        longitude=7.63,
    )


def _keyword(phrase: str, primary: bool) -> Keyword:
    return Keyword(
        id=uuid4(),
        customer_id=CUSTOMER_ID,
        phrase=phrase,
        city="Münster",
        monthly_volume=None,
        is_primary=primary,
    )


LISTED_NAMES = [
    "Dachdeckerei Hollenbeck GmbH",
    "Bedachungen Overmann",
    "Dach Service Kemper",
    "Zimmerei Lüttke",
    "Noch ein Betrieb",
]
GRID_POINTS = 25


def _listing(name: str) -> MapListing:
    return MapListing(name, f"https://www.google.com/maps/place/{name}", None, 4.5, 10)


def _place(place_id: str, name: str) -> PlaceSnapshot:
    return PlaceSnapshot(
        place_id=place_id,
        name=name,
        formatted_address="Münster",
        phone=None,
        website=None,
        primary_type="roofing_contractor",
        types=[],
        rating=4.5,
        review_count=10,
        has_opening_hours=True,
        photo_count=3,
        recent_reviews=[],
        business_status="OPERATIONAL",
    )


def _serp(
    phrase: str, engine: str = "google", vertical: str = "web", route: str = "browser"
) -> SerpResult:
    return SerpResult.model_validate(
        {
            "phrase": phrase,
            "organic": [(1, "dachdecker-hollenbeck.de")],
            "local_pack": [],
            "ai_overview_present": False,
            "ai_overview_mentions": [],
            "people_also_ask": [],
            "measured_at": datetime(2026, 10, 16, tzinfo=UTC),
            "engine": engine,
            "vertical": vertical,
            "route": route,
        }
    )


WEBSITE = WebsiteSnapshot(
    start_url=SITE,
    uses_https=True,
    redirects_to_single_host=True,
    robots_txt=None,
    sitemap_urls=[],
    legal_notice_url=None,
    privacy_url=None,
    footer_nap={},
    opening_hours_text=None,
    pages=[],
    crawl_errors=[],
)
SPEED = PageSpeedSnapshot(
    url=SITE,
    mobile_performance=40,
    desktop_performance=80,
    lcp_ms=3000,
    cls=0.1,
    inp_ms=None,
    accessibility=90,
)


async def no_pause(browser: BrowserService) -> None:
    """Skip the human pause between two browser reads."""


class Recorder:
    """Collects the arguments the monkeypatched collectors were called with."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.calls: dict[str, list[tuple[object, ...]]] = {}

    def record(self, name: str, *arguments: object) -> None:
        """Remember one call."""
        self.calls.setdefault(name, []).append(arguments)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Replace every collector with a fake that succeeds and records its arguments."""
    calls = Recorder()

    async def crawl(start_url: HttpUrl) -> WebsiteSnapshot:
        calls.record("crawl", start_url)
        return WEBSITE

    async def measure(url: HttpUrl, api_key: SecretStr) -> PageSpeedSnapshot:
        calls.record("measure", url)
        return SPEED

    async def find_place(name: str, address: str, api_key: SecretStr) -> str | None:
        calls.record("find_place", name, address)
        return f"id-{name}"

    async def place_details(place_id: str, api_key: SecretStr) -> PlaceSnapshot:
        calls.record("place_details", place_id)
        return _place(place_id, place_id.removeprefix("id-"))

    async def top_competitors(
        phrase: str, latitude: float, longitude: float, api_key: SecretStr, limit: int = 3
    ) -> list[PlaceSnapshot]:
        calls.record("top_competitors", phrase, limit)
        return [
            _place("id-Dachdeckerei Hollenbeck GmbH", "Dachdeckerei Hollenbeck GmbH"),
            _place("id-Bedachungen Overmann", "Bedachungen Overmann"),
            _place("ChIJ-kemper", "Dach Service Kemper"),
            _place("ChIJ-luettke", "Zimmerei Lüttke"),
            _place("ChIJ-extra", "Noch ein Betrieb"),
        ]

    async def read_profile(browser: BrowserService, target: ProfileTarget) -> PlaceSnapshot:
        calls.record("read_profile", target)
        return _place(target.place_id or f"maps-{target.name}", target.name)

    async def search_listings(
        browser: BrowserService, phrase: str, at: Geolocation | None = None
    ) -> list[MapListing]:
        calls.record("search_listings", phrase, at)
        return [_listing(name) for name in LISTED_NAMES]

    async def ranked_place_ids(
        phrase: str, latitude: float, longitude: float, api_key: SecretStr, radius: float
    ) -> list[str]:
        calls.record("ranked_place_ids", phrase, radius)
        return ["ChIJ-a", "id-Dachdeckerei Hollenbeck GmbH"]

    async def listed_businesses(
        phrase: str, latitude: float, longitude: float, api_key: SecretStr, radius: float
    ) -> list[places.ListedPlace]:
        calls.record("listed_businesses", phrase, radius)
        return [
            places.ListedPlace("ChIJ-a", "Bedachungen Overmann", 154, 4.8),
            places.ListedPlace(
                "id-Dachdeckerei Hollenbeck GmbH", "Dachdeckerei Hollenbeck GmbH", 86, 4.7
            ),
        ]

    async def search_all(browser: BrowserService, phrases: list[str]) -> serp.SearchBatch:
        calls.record("search_all", phrases)
        return serp.SearchBatch([_serp(phrase) for phrase in phrases], [])

    async def search_bing(browser: BrowserService, phrases: list[str]) -> serp.SearchBatch:
        calls.record("search_bing", phrases)
        return serp.SearchBatch([_serp(phrase, "bing") for phrase in phrases], [])

    async def search_images(browser: BrowserService, phrases: list[str]) -> serp.SearchBatch:
        calls.record("search_images", phrases)
        return serp.SearchBatch([_serp(phrase, vertical="images") for phrase in phrases], [])

    async def check_directories(location: Location, catalogue: Path) -> list[DirectoryHit]:
        calls.record("check_directories", location.name, catalogue)
        return [
            DirectoryHit(
                directory=name,
                found=False,
                listing_url=None,
                name=None,
                address=None,
                phone=None,
            )
            for name in ("Gelbe Seiten", "Das Örtliche", "11880")
        ]

    async def ask_all(
        questions: list[str],
        business_name: str,
        settings: Settings,
        gate: SourceGate,
        browser: BrowserService,
    ) -> list[AssistantAnswer]:
        calls.record("ask_all", questions, business_name)
        return [
            AssistantAnswer(
                provider=provider,
                question=questions[0],
                mentioned=True,
                cited_sources=[],
                answer_excerpt="Hollenbeck",
                route="browser",
            )
            for provider in assistants.enabled_providers(settings, gate)
        ]

    monkeypatch.setattr(website, "crawl_website", crawl)
    monkeypatch.setattr(pagespeed, "measure", measure)
    monkeypatch.setattr(places, "find_place", find_place)
    monkeypatch.setattr(places, "place_details", place_details)
    monkeypatch.setattr(places, "top_competitors", top_competitors)
    monkeypatch.setattr(places, "ranked_place_ids", ranked_place_ids)
    monkeypatch.setattr(places, "listed_businesses", listed_businesses)
    monkeypatch.setattr(maps, "read_profile", read_profile)
    monkeypatch.setattr(maps, "search_listings", search_listings)
    monkeypatch.setattr(BrowserService, "pause", no_pause)
    monkeypatch.setattr(serp, "search_all", search_all)
    monkeypatch.setattr(serp, "search_bing", search_bing)
    monkeypatch.setattr(serp, "search_images", search_images)
    monkeypatch.setattr(directories, "check_directories", check_directories)

    async def first_capture(domain: str) -> date | None:
        calls.record("first_capture", domain)
        return FIRST_CAPTURE

    async def search_each(
        client: paid_rankings.DataForSeoClient,
        phrases: list[str],
        latitude: float | None,
        longitude: float | None,
    ) -> paid_rankings.PaidSearchBatch:
        calls.record("search_each", phrases, latitude, longitude)
        return paid_rankings.PaidSearchBatch(
            [_serp(phrase, route="dataforseo") for phrase in phrases], []
        )

    monkeypatch.setattr(assistants, "ask_all", ask_all)
    monkeypatch.setattr(run, "DIRECTORY_CATALOGUE", CATALOGUE)
    monkeypatch.setattr(wayback, "first_capture", first_capture)
    monkeypatch.setattr(paid_rankings, "search_each", search_each)
    monkeypatch.setattr(paid_data, "collect_paid", _fake_paid([]))
    return calls


@pytest.fixture
def repositories(fake_repositories: MagicMock) -> MagicMock:
    """Repositories holding one customer with location, competitors and keywords."""
    fake_repositories.audit.return_value = Audit(
        id=AUDIT_ID,
        customer_id=CUSTOMER_ID,
        kind=AuditKind.REMOTE,
        status=AuditStatus.COLLECTING,
        requested_at=datetime(2026, 10, 16, tzinfo=UTC),
        finished_at=None,
        total_score=None,
        raw_data_path=None,
        report_path=None,
    )
    fake_repositories.customer.return_value = _customer()
    fake_repositories.locations.return_value = [_location()]
    fake_repositories.competitors.return_value = [
        Competitor(
            id=uuid4(),
            customer_id=CUSTOMER_ID,
            name="Bedachungen Overmann",
            google_place_id=None,
            website=None,
        )
    ]
    fake_repositories.keywords.return_value = [
        _keyword("Flachdach", primary=False),
        _keyword("Dachdecker", primary=True),
    ]
    return fake_repositories


@pytest.fixture
def assistant_settings(collector_settings: Settings) -> Settings:
    """Settings with one assistant provider enabled."""
    return collector_settings.model_copy(update={"openai_api_key": SecretStr("openai-key")})


async def test_collect_audit_merges_every_source(
    repositories: MagicMock, recorder: Recorder, assistant_settings: Settings, open_gate: SourceGate
) -> None:
    """A run where every source answers produces a complete RawAudit without warnings."""
    settings = _paid_settings(assistant_settings)

    raw = await run.collect_audit(AUDIT_ID, repositories, settings, open_gate)

    assert raw.warnings == []
    assert raw.paid is not None
    assert raw.domain_first_capture == FIRST_CAPTURE
    assert recorder.calls["first_capture"] == [("dachdecker-hollenbeck.de",)]
    assert "search_each" not in recorder.calls
    assert {result.route for result in raw.serps} == {"browser"}
    assert raw.website == WEBSITE
    assert raw.pagespeed == SPEED
    assert raw.place is not None
    assert raw.place.name == "Dachdeckerei Hollenbeck GmbH"
    assert [place.name for place in raw.competitor_places] == [
        "Bedachungen Overmann",
        "Dach Service Kemper",
        "Zimmerei Lüttke",
    ]
    assert [(result.phrase, result.engine, result.vertical) for result in raw.serps] == [
        ("Flachdach Münster", "google", "web"),
        ("Dachdecker Münster", "google", "web"),
        ("Dachdeckerei Hollenbeck GmbH", "google", "web"),
        ("Dachdecker Münster", "bing", "web"),
        ("Dachdecker Münster", "google", "images"),
    ]
    assert len(raw.directories) == 3
    assert [answer.provider for answer in raw.assistant_answers] == [
        "chatgpt",
        "gemini",
        "perplexity",
        "claude",
    ]
    assert raw.collected_at.tzinfo is UTC
    repositories.customer.assert_called_once_with(CUSTOMER_ID)
    own, overmann, *ranked = (target for (target,) in recorder.calls["read_profile"])
    assert isinstance(own, ProfileTarget) and isinstance(overmann, ProfileTarget)
    assert (own.name, own.address) == (
        "Dachdeckerei Hollenbeck GmbH",
        "Hiltruper Straße 210, 48165 Münster",
    )
    assert (overmann.name, overmann.address) == ("Bedachungen Overmann", "Münster")
    assert [target.url for target in ranked if isinstance(target, ProfileTarget)] == [
        _listing("Dach Service Kemper").url,
        _listing("Zimmerei Lüttke").url,
    ]
    assert "find_place" not in recorder.calls
    assert [(item.phrase, item.position, item.route) for item in raw.map_rankings] == [
        ("Flachdach Münster", 1, "browser"),
        ("Dachdecker Münster", 1, "browser"),
    ]
    assert len(raw.grid) == GRID_POINTS
    assert {point.route for point in raw.grid} == {"browser"}
    assert len(recorder.calls["search_listings"]) == 2 + GRID_POINTS
    assert raw.map_results is not None
    assert (raw.map_results.phrase, raw.map_results.route) == ("Dachdecker Münster", "browser")
    assert [
        (item.name, item.review_count, item.is_customer) for item in raw.map_results.businesses
    ] == [(name, 10, name == "Dachdeckerei Hollenbeck GmbH") for name in LISTED_NAMES]
    assert own.with_photos is True
    assert not overmann.with_photos and not any(
        isinstance(target, ProfileTarget) and target.with_photos for target in ranked
    )
    questions, business_name = recorder.calls["ask_all"][0]
    assert business_name == "Dachdeckerei Hollenbeck GmbH"
    assert isinstance(questions, list)
    assert questions[3] == "Flachdach in Münster, wen beauftragen?"


async def test_collect_audit_fails_when_crawl_fails(
    repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    open_gate: SourceGate,
) -> None:
    """A failing website crawl fails the whole audit."""

    async def broken_crawl(start_url: HttpUrl) -> WebsiteSnapshot:
        raise CollectionError("website start page answered HTTP 500")

    monkeypatch.setattr(website, "crawl_website", broken_crawl)

    with pytest.raises(CollectionError, match="start page"):
        await run.collect_audit(AUDIT_ID, repositories, collector_settings, open_gate)

    assert "measure" not in recorder.calls


async def test_collect_audit_turns_optional_failures_into_warnings(
    repositories: MagicMock,
    recorder: Recorder,
    assistant_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    open_gate: SourceGate,
) -> None:
    """Failing optional sources add one warning each and leave their fields empty."""

    async def rate_limited(url: HttpUrl, api_key: SecretStr) -> PageSpeedSnapshot:
        raise TransientCollectionError("PageSpeed Insights (mobile) answered HTTP 429")

    async def not_listed(browser: BrowserService, target: ProfileTarget) -> PlaceSnapshot:
        if target.url is None:
            raise ProfileNotFoundError(f"Google Maps lists no profile for {target.name}")
        return _place(target.url, target.name)

    async def captcha(browser: BrowserService, phrases: list[str]) -> serp.SearchBatch:
        return serp.SearchBatch([_serp(phrases[0])], phrases[1:], "www.google.de shows a bot check")

    async def bing_blocked(browser: BrowserService, phrases: list[str]) -> serp.SearchBatch:
        return serp.SearchBatch([], phrases, "www.bing.com shows a bot check")

    async def partial_directories(location: Location, catalogue: Path) -> list[DirectoryHit]:
        return [
            DirectoryHit(
                directory="Gelbe Seiten",
                found=True,
                listing_url=None,
                name="Hollenbeck",
                address=None,
                phone=None,
            )
        ]

    async def no_answers(
        questions: list[str],
        business_name: str,
        settings: Settings,
        gate: SourceGate,
        browser: BrowserService,
    ) -> list[AssistantAnswer]:
        raise CollectionError("no assistant answered: OpenAI rejected the request with HTTP 401")

    monkeypatch.setattr(pagespeed, "measure", rate_limited)
    monkeypatch.setattr(maps, "read_profile", not_listed)
    monkeypatch.setattr(serp, "search_all", captcha)
    monkeypatch.setattr(serp, "search_bing", bing_blocked)
    monkeypatch.setattr(directories, "check_directories", partial_directories)
    monkeypatch.setattr(assistants, "ask_all", no_answers)

    raw = await run.collect_audit(AUDIT_ID, repositories, assistant_settings, open_gate)

    assert raw.warnings == [
        "pagespeed: PageSpeed Insights (mobile) answered HTTP 429",
        "places: no Google Business Profile found for Dachdeckerei Hollenbeck GmbH",
        "competitors: Bedachungen Overmann not found on Google",
        "serps: 2 searches deferred to a retry: www.google.de shows a bot check",
        "bing: 1 of 1 not searched: www.bing.com shows a bot check",
        "images: 1 searches deferred to a retry: Google blocked the web searches before",
        "directories: not checked: Das Örtliche, 11880",
        "assistants: no assistant answered: OpenAI rejected the request with HTTP 401",
        "paid: DataForSEO credentials are missing",
    ]
    assert raw.website == WEBSITE
    assert raw.pagespeed is None
    assert raw.place is None
    assert [place.name for place in raw.competitor_places] == [
        "Bedachungen Overmann",
        "Dach Service Kemper",
        "Zimmerei Lüttke",
    ]
    assert [(result.phrase, result.engine) for result in raw.serps] == [
        ("Flachdach Münster", "google")
    ]
    assert raw.deferred_searches == [
        DeferredSearch(phrase="Dachdecker Münster"),
        DeferredSearch(phrase="Dachdeckerei Hollenbeck GmbH"),
        DeferredSearch(phrase="Dachdecker Münster", vertical="images"),
    ]
    assert "search_images" not in recorder.calls
    assert len(raw.directories) == 1
    assert raw.assistant_answers == []


async def test_collect_audit_without_website_or_location(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings, open_gate: SourceGate
) -> None:
    """Missing website and location produce warnings instead of failing the audit."""
    repositories.customer.return_value = _customer(website_url=None)
    repositories.locations.return_value = []
    repositories.competitors.return_value = []

    raw = await run.collect_audit(AUDIT_ID, repositories, collector_settings, open_gate)

    assert raw.website is None
    assert raw.warnings == [
        "website: customer has no website",
        "pagespeed: customer has no website",
        "places: customer has no location",
        "competitors: no primary keyword or location coordinates to find ranked competitors",
        "map_rankings: customer has no location",
        "map_results: customer has no location",
        "grid: customer has no location",
        "directories: customer has no location",
        "paid: DataForSEO credentials are missing",
    ]
    assert raw.competitor_places == []
    assert raw.domain_first_capture is None
    assert "crawl" not in recorder.calls
    assert "first_capture" not in recorder.calls
    assert "ask_all" in recorder.calls


async def test_collect_audit_skips_assistants_switched_off(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings
) -> None:
    """With every assistant source off the assistants are not asked."""
    assistant_sources = {Source.CHATGPT, Source.GEMINI, Source.PERPLEXITY, Source.CLAUDE}
    sources = SourcesSettings.model_validate(
        {source.value: {"enabled": source not in assistant_sources} for source in Source}
    )
    gate = SourceGate(sources, lambda source, cap: True)

    raw = await run.collect_audit(AUDIT_ID, repositories, collector_settings, gate)

    assert raw.assistant_answers == []
    assert "ask_all" not in recorder.calls


async def test_collect_audit_without_keywords_warns_for_searches(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings, open_gate: SourceGate
) -> None:
    """Without keywords the searches are skipped with a warning each."""
    repositories.keywords.return_value = []
    repositories.locations.return_value = [_location(place_id="ChIJ-own")]

    raw = await run.collect_audit(AUDIT_ID, repositories, collector_settings, open_gate)

    assert raw.serps == []
    assert "serps: customer has no keywords" in raw.warnings
    assert "bing: customer has no keywords" in raw.warnings
    assert "images: customer has no keywords" in raw.warnings
    assert "map_rankings: customer has no keywords" in raw.warnings
    assert "grid: customer has no keywords" in raw.warnings
    own_target = recorder.calls["read_profile"][0][0]
    assert isinstance(own_target, ProfileTarget) and own_target.place_id == "ChIJ-own"
    assert raw.place is not None and raw.place.place_id == "ChIJ-own"


async def _blocked_after_first(browser: BrowserService, phrases: list[str]) -> serp.SearchBatch:
    return serp.SearchBatch([_serp(phrases[0])], phrases[1:], "www.google.de shows a bot check")


async def test_collect_audit_sends_blocked_searches_to_dataforseo(
    repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    open_gate: SourceGate,
) -> None:
    """Google is searched in the browser; what Google blocks goes to DataForSEO at once."""
    monkeypatch.setattr(serp, "search_all", _blocked_after_first)

    raw = await run.collect_audit(
        AUDIT_ID, repositories, _paid_settings(collector_settings), open_gate
    )

    assert recorder.calls["search_each"] == [
        (["Dachdecker Münster", "Dachdeckerei Hollenbeck GmbH"], 51.9, 7.63)
    ]
    google_web = [
        (result.phrase, result.route)
        for result in raw.serps
        if result.engine == "google" and result.vertical == "web"
    ]
    assert google_web == [
        ("Flachdach Münster", "browser"),
        ("Dachdecker Münster", "dataforseo"),
        ("Dachdeckerei Hollenbeck GmbH", "dataforseo"),
    ]
    # Images are not part of the DataForSEO fallback and wait for the retry, without a request.
    assert "search_images" not in recorder.calls
    assert raw.deferred_searches == [DeferredSearch(phrase="Dachdecker Münster", vertical="images")]
    assert raw.warnings == [
        "serps: Google blocked the browser, 2 of 2 searches measured through DataForSEO",
        "images: 1 searches deferred to a retry: Google blocked the web searches before",
    ]
    assert len(raw.grid) == GRID_POINTS


async def test_collect_audit_defers_what_dataforseo_cannot_search(
    repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    open_gate: SourceGate,
) -> None:
    """Phrases DataForSEO cannot search (cap reached) go to the serp_retry job."""
    monkeypatch.setattr(serp, "search_all", _blocked_after_first)

    async def capped(
        client: paid_rankings.DataForSeoClient,
        phrases: list[str],
        latitude: float | None,
        longitude: float | None,
    ) -> paid_rankings.PaidSearchBatch:
        return paid_rankings.PaidSearchBatch(
            [_serp(phrases[0], route="dataforseo")],
            phrases[1:],
            "dataforseo: monthly cost cap of 50 EUR reached",
        )

    monkeypatch.setattr(paid_rankings, "search_each", capped)

    raw = await run.collect_audit(
        AUDIT_ID, repositories, _paid_settings(collector_settings), open_gate
    )

    assert raw.deferred_searches == [
        DeferredSearch(phrase="Dachdeckerei Hollenbeck GmbH"),
        DeferredSearch(phrase="Dachdecker Münster", vertical="images"),
    ]
    assert (
        "serps: 1 searches deferred to a retry: www.google.de shows a bot check; "
        "DataForSEO: dataforseo: monthly cost cap of 50 EUR reached"
    ) in raw.warnings


async def test_collect_audit_defers_blocked_searches_when_dataforseo_is_off(
    repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With DataForSEO switched off in the app settings the blocked searches wait for the retry."""
    monkeypatch.setattr(serp, "search_all", _blocked_after_first)
    sources = SourcesSettings.model_validate({"dataforseo": {"enabled": False}})

    raw = await run.collect_audit(
        AUDIT_ID,
        repositories,
        _paid_settings(collector_settings),
        SourceGate(sources, lambda source, cap: True),
    )

    assert "search_each" not in recorder.calls
    assert [search.phrase for search in raw.deferred_searches if search.vertical == "web"] == [
        "Dachdecker Münster",
        "Dachdeckerei Hollenbeck GmbH",
    ]
    assert raw.paid is None
    assert len(raw.grid) == GRID_POINTS


async def test_collect_audit_reports_a_reached_monthly_cost_cap(
    repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gate whose DataForSEO spend reached the cap lists the source in cost_capped_sources."""

    class SpentLedger:
        def month_source_cost(self, source: str) -> Decimal:
            return Decimal(50)

        def add_source_cost(self, source: str, cost: Decimal) -> Decimal:
            raise AssertionError("no request may run")

    async def collect(
        client: paid_rankings.DataForSeoClient,
        request: paid_data.PaidRequest,
        notes: list[str],
        today: object = None,
    ) -> PaidSnapshot:
        await client.post(paid_data.SUMMARY_PATH, {"target": "x"})
        raise AssertionError("the cap must stop the request")

    monkeypatch.setattr(paid_data, "collect_paid", collect)
    gate = SourceGate(SourcesSettings(), lambda source, cap: True, SpentLedger())

    raw = await run.collect_audit(AUDIT_ID, repositories, _paid_settings(collector_settings), gate)

    assert raw.cost_capped_sources == [Source.DATAFORSEO]
    assert "paid: dataforseo: monthly cost cap of 50 EUR reached" in raw.warnings


async def test_collect_audit_measures_the_grid_in_the_browser(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings, open_gate: SourceGate
) -> None:
    """The 5x5 grid of j3 searches Google Maps from every point for the primary keyword."""
    raw = await run.collect_audit(AUDIT_ID, repositories, collector_settings, open_gate)

    assert [point.key for point in raw.grid][:3] == ["r1c1", "r1c2", "r1c3"]
    assert all(point.position == 1 for point in raw.grid)
    grid_searches = recorder.calls["search_listings"][-GRID_POINTS:]
    assert {phrase for phrase, _ in grid_searches} == {"Dachdecker Münster"}
    positions = {at for _, at in grid_searches}
    assert len(positions) == GRID_POINTS
    assert not any(warning.startswith("grid") for warning in raw.warnings)


async def test_collect_audit_warns_when_grid_lacks_coordinates(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings, open_gate: SourceGate
) -> None:
    """A location without coordinates leaves the grid and map positions empty with a warning."""
    repositories.locations.return_value = [
        _location().model_copy(update={"latitude": None, "longitude": None})
    ]

    raw = await run.collect_audit(AUDIT_ID, repositories, collector_settings, open_gate)

    assert raw.grid == []
    assert raw.map_rankings == []
    assert "grid: location has no coordinates for the map searches" in raw.warnings
    assert "map_rankings: location has no coordinates for the map searches" in raw.warnings


async def test_collect_audit_takes_profiles_from_places_when_maps_blocks(
    repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    open_gate: SourceGate,
) -> None:
    """After a Maps bot check, profile, competitors, positions and grid come from Places."""

    async def blocked(browser: BrowserService, target: ProfileTarget) -> PlaceSnapshot:
        raise SearchBlockedError("www.google.com shows a bot check")

    monkeypatch.setattr(maps, "read_profile", blocked)

    raw = await run.collect_audit(
        AUDIT_ID, repositories, _paid_settings(collector_settings), open_gate
    )

    assert raw.warnings == [
        "places: Google Maps blocked the browser (www.google.com shows a bot check), "
        "Places API used"
    ]
    assert raw.place is not None and raw.place.place_id == "id-Dachdeckerei Hollenbeck GmbH"
    assert [place.name for place in raw.competitor_places] == [
        "Bedachungen Overmann",
        "Dach Service Kemper",
        "Zimmerei Lüttke",
    ]
    assert {(item.position, item.route) for item in raw.map_rankings} == {(2, "places_api")}
    assert len(raw.grid) == GRID_POINTS
    assert {(point.position, point.route) for point in raw.grid} == {(2, "places_api")}
    assert "search_listings" not in recorder.calls
    assert len(recorder.calls["ranked_place_ids"]) == 2 + GRID_POINTS
    assert raw.map_results is not None and raw.map_results.route == "places_api"
    assert [
        (item.name, item.review_count, item.is_customer) for item in raw.map_results.businesses
    ] == [
        ("Bedachungen Overmann", 154, False),
        ("Dachdeckerei Hollenbeck GmbH", 86, True),
    ]


async def test_collect_audit_warns_when_paid_credentials_missing(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings, open_gate: SourceGate
) -> None:
    """DataForSEO switched on without credentials is a warning; Google runs in the browser."""
    raw = await run.collect_audit(AUDIT_ID, repositories, collector_settings, open_gate)

    assert "paid: DataForSEO credentials are missing" in raw.warnings
    assert raw.paid is None
    assert "search_all" in recorder.calls


async def test_collect_audit_does_not_repeat_a_brand_keyword(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings, open_gate: SourceGate
) -> None:
    """A keyword that already is the business name is not searched twice."""
    repositories.keywords.return_value = [_keyword("Dachdeckerei Hollenbeck GmbH", primary=True)]
    repositories.customer.return_value = _customer().model_copy(
        update={"name": "dachdeckerei hollenbeck gmbh münster"}
    )

    await run.collect_audit(AUDIT_ID, repositories, collector_settings, open_gate)

    assert recorder.calls["search_all"] == [(["Dachdeckerei Hollenbeck GmbH Münster"],)]


async def test_collect_audit_reads_question_templates_from_settings(
    repositories: MagicMock,
    recorder: Recorder,
    assistant_settings: Settings,
    tmp_path: Path,
    open_gate: SourceGate,
) -> None:
    """The assistant questions come from the configured templates file."""
    questions_file = tmp_path / "fragen.yaml"
    questions_file.write_text(
        "empfehlung: ['Bester {industry} in {city}?']\nproblem: ['{service} {city}']\n",
        encoding="utf-8",
    )
    settings = assistant_settings.model_copy(update={"questions_path": questions_file})

    await run.collect_audit(AUDIT_ID, repositories, settings, open_gate)

    questions, _ = recorder.calls["ask_all"][0]
    assert questions == ["Bester Dachdecker in Münster?", "Flachdach Münster"]


async def test_questions_name_the_trade_of_the_industry_group(
    repositories: MagicMock,
    recorder: Recorder,
    assistant_settings: Settings,
    tmp_path: Path,
    open_gate: SourceGate,
) -> None:
    """Without keywords, a group id or an older industry is asked about with the group noun."""
    questions_file = tmp_path / "fragen.yaml"
    questions_file.write_text(
        "empfehlung: ['Bester {industry} in {city}?']\nproblem: ['{service} {city}']\n",
        encoding="utf-8",
    )
    settings = assistant_settings.model_copy(update={"questions_path": questions_file})
    customer = repositories.customer.return_value
    repositories.customer.return_value = customer.model_copy(update={"industry": "handwerk"})
    repositories.keywords.return_value = []

    await run.collect_audit(AUDIT_ID, repositories, settings, open_gate)

    questions, _ = recorder.calls["ask_all"][0]
    assert questions[0] == "Bester Handwerksbetrieb in Münster?"


def _paid_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"dataforseo_login": "login", "dataforseo_password": SecretStr("secret")}
    )


def _fake_paid(
    requests: list[paid_data.PaidRequest], note: str | None = None
) -> Callable[..., Awaitable[PaidSnapshot]]:
    async def collect(
        client: paid_rankings.DataForSeoClient,
        request: paid_data.PaidRequest,
        notes: list[str],
        today: object = None,
    ) -> PaidSnapshot:
        requests.append(request)
        if note is not None:
            notes.append(note)
        return PaidSnapshot(
            customer_domain=request.customer_domain,
            volumes={"Dachdecker Münster": 880, "Flachdach Münster": 40},
            primary_phrases=request.primary_phrases,
            collected_at=datetime(2026, 10, 16, tzinfo=UTC),
        )

    return collect


async def test_collect_audit_measures_paid_data_and_stores_volumes(
    repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    open_gate: SourceGate,
) -> None:
    """Paid data covers customer and competitor domains; new volumes go back to the keywords."""
    requests: list[paid_data.PaidRequest] = []
    monkeypatch.setattr(paid_data, "collect_paid", _fake_paid(requests, "volumes: slow"))
    keywords = repositories.keywords.return_value
    repositories.keywords.return_value = [
        keywords[0].model_copy(update={"monthly_volume": 40}),
        keywords[1],
    ]
    repositories.competitors.return_value = [
        repositories.competitors.return_value[0].model_copy(
            update={"website": HttpUrl("https://www.overmann-dach.de/leistungen")}
        )
    ]

    raw = await run.collect_audit(
        AUDIT_ID, repositories, _paid_settings(collector_settings), open_gate
    )

    assert raw.paid is not None
    assert raw.paid.volumes["Dachdecker Münster"] == 880
    assert raw.warnings == ["paid: volumes: slow"]
    request = requests[0]
    assert request.customer_domain == "dachdecker-hollenbeck.de"
    assert request.competitor_domains == ["overmann-dach.de"]
    assert request.phrases == ["Flachdach Münster", "Dachdecker Münster"]
    assert request.primary_phrases == ["Dachdecker Münster"]
    assert request.city == "Münster"
    repositories.update_keyword_volume.assert_called_once_with(keywords[1].id, 880)


async def test_collect_audit_warns_when_volumes_cannot_be_saved(
    repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    open_gate: SourceGate,
) -> None:
    """A failed volume write is a warning, the paid data stays."""
    repositories.update_keyword_volume.side_effect = StorageError("write refused")

    raw = await run.collect_audit(
        AUDIT_ID, repositories, _paid_settings(collector_settings), open_gate
    )

    assert raw.paid is not None
    assert "paid: volume of Dachdecker not saved: write refused" in raw.warnings


async def test_collect_audit_has_no_paid_or_access_data_by_default(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings, open_gate: SourceGate
) -> None:
    """A remote audit without paid data leaves both snapshots empty and touches no access."""
    raw = await run.collect_audit(AUDIT_ID, repositories, collector_settings, open_gate)

    assert raw.paid is None
    assert raw.access is None
    repositories.customer_access.assert_not_called()


ACCESS_RECORD = CustomerAccess(
    customer_id=CUSTOMER_ID,
    google_business_profile_granted=True,
    search_console_property="sc-domain:dachdecker-hollenbeck.de",
    wordpress_url=None,
    wordpress_user=None,
    granted_at=datetime(2026, 10, 1, tzinfo=UTC),
    google_location_name="locations/123",
    analytics_property_id="987",
)


@pytest.fixture
def access_repositories(repositories: MagicMock) -> MagicMock:
    """Repositories for an audit with access and an access record."""
    repositories.audit.return_value = repositories.audit.return_value.model_copy(
        update={"kind": AuditKind.WITH_ACCESS}
    )
    repositories.customer_access.return_value = ACCESS_RECORD
    return repositories


async def test_collect_audit_reads_customer_data_with_access(
    access_repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    open_gate: SourceGate,
) -> None:
    """With access the token is refreshed and the access collector reads the recorded ids."""
    seen: list[tuple[str, CustomerAccess]] = []

    async def token(repositories: object, settings: Settings) -> str:
        return "access-token"

    async def collect(
        access_token: str, record: CustomerAccess, notes: list[str], today: object = None
    ) -> AccessSnapshot:
        seen.append((access_token, record))
        notes.append("analytics: no identifier recorded for the customer")
        return AccessSnapshot(
            profile_metrics={"CALL_CLICKS": 3}, collected_at=datetime(2026, 10, 16, tzinfo=UTC)
        )

    monkeypatch.setattr(google_auth, "access_token", token)
    monkeypatch.setattr(access, "collect_access", collect)

    settings = _paid_settings(collector_settings)

    raw = await run.collect_audit(AUDIT_ID, access_repositories, settings, open_gate)

    assert raw.access is not None
    assert raw.access.profile_metrics == {"CALL_CLICKS": 3}
    assert seen == [("access-token", ACCESS_RECORD)]
    assert raw.warnings == ["access: analytics: no identifier recorded for the customer"]
    access_repositories.customer_access.assert_called_once_with(CUSTOMER_ID)


async def test_collect_audit_warns_without_authorisation_or_record(
    access_repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    open_gate: SourceGate,
) -> None:
    """A missing grant or access record becomes a warning; the audit still completes."""

    async def no_grant(repositories: object, settings: Settings) -> str:
        raise AuthorizationError("no Google authorisation stored; run radarauditor google auth")

    monkeypatch.setattr(google_auth, "access_token", no_grant)
    settings = _paid_settings(collector_settings)

    raw = await run.collect_audit(AUDIT_ID, access_repositories, settings, open_gate)

    assert raw.access is None
    assert raw.warnings == ["access: no Google authorisation stored; run radarauditor google auth"]
    access_repositories.customer_access.return_value = None
    raw = await run.collect_audit(AUDIT_ID, access_repositories, settings, open_gate)
    assert raw.warnings == ["access: customer has recorded no access"]


class CountingQuota:
    """Daily quota per source that allows a fixed number of uses."""

    def __init__(self, allowed: dict[str, int]) -> None:
        """Start with the allowed uses per source; unlisted sources are unlimited."""
        self.allowed = allowed
        self.used: dict[str, int] = {}

    def consume(self, source: str, cap: int) -> bool:
        """Count one use and answer whether it was allowed."""
        self.used[source] = self.used.get(source, 0) + 1
        return self.used[source] <= self.allowed.get(source, cap)


async def test_collect_audit_by_default_reads_maps_and_leaves_places_api_alone(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings
) -> None:
    """With default app settings Google Maps answers in the browser and Places is not asked."""
    quota = CountingQuota({})

    raw = await run.collect_audit(
        AUDIT_ID, repositories, collector_settings, SourceGate(SourcesSettings(), quota.consume)
    )

    assert "find_place" not in recorder.calls
    assert "top_competitors" not in recorder.calls
    assert raw.place is not None
    assert "places" not in quota.used
    # Own profile, one recorded and two found competitors, two keyword searches, 25 grid points.
    assert quota.used["google_maps"] == 1 + 1 + 2 + 2 + GRID_POINTS
    assert quota.used["google_search"] == 4
    assert raw.capped_sources == []


async def test_collect_audit_stops_google_searches_at_the_daily_cap(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings
) -> None:
    """Searches beyond the cap are left out and the source is reported as capped."""
    quota = CountingQuota({"google_search": 2})

    raw = await run.collect_audit(
        AUDIT_ID, repositories, collector_settings, SourceGate(SourcesSettings(), quota.consume)
    )

    assert recorder.calls["search_all"] == [(["Flachdach Münster", "Dachdecker Münster"],)]
    assert recorder.calls["search_images"] == [([],)]
    assert "serps: daily cap reached, 1 of 3 not searched" in raw.warnings
    assert raw.capped_sources == [Source.GOOGLE_SEARCH]


async def test_collect_audit_keeps_competitors_found_before_the_cap(
    repositories: MagicMock, recorder: Recorder, collector_settings: Settings
) -> None:
    """A cap reached during competitor lookup keeps what was found and notes the cap."""
    sources = SourcesSettings.model_validate({"google_maps": {"enabled": False}})
    quota = CountingQuota({"places": 4})

    raw = await run.collect_audit(
        AUDIT_ID, repositories, collector_settings, SourceGate(sources, quota.consume)
    )

    assert raw.place is not None
    assert [place.name for place in raw.competitor_places] == ["Bedachungen Overmann"]
    assert "competitors: places: daily cap of 500 reached" in raw.warnings
    assert "grid: places: daily cap of 500 reached" in raw.warnings
    assert Source.PLACES in raw.capped_sources


async def test_questions_use_the_trade_of_the_primary_keyword(
    repositories: MagicMock,
    recorder: Recorder,
    assistant_settings: Settings,
    tmp_path: Path,
    open_gate: SourceGate,
) -> None:
    """The primary keyword without the city names the trade; a brand keyword does not."""
    questions_file = tmp_path / "fragen.yaml"
    questions_file.write_text(
        "empfehlung: ['Bester {industry} in {city}?']\nproblem: ['{service} {city}']\n",
        encoding="utf-8",
    )
    settings = assistant_settings.model_copy(update={"questions_path": questions_file})
    repositories.keywords.return_value = [_keyword("Elektriker Münster", primary=True)]

    await run.collect_audit(AUDIT_ID, repositories, settings, open_gate)

    questions, _ = recorder.calls["ask_all"][0]
    assert questions[0] == "Bester Elektriker in Münster?"


async def test_collect_audit_identifies_a_business_started_with_only_its_website(
    repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Name, city, industry and keywords are found first; the Google steps use them."""
    host_customer = _customer().model_copy(
        update={"name": "dachdecker-hollenbeck.de", "city": "", "industry": ""}
    )
    repositories.customer.return_value = host_customer
    repositories.locations.return_value = [
        _location().model_copy(
            update={"name": "dachdecker-hollenbeck.de", "city": "", "street": ""}
        )
    ]
    repositories.competitors.return_value = []
    repositories.keywords.return_value = []
    repositories.link_crm_customer.return_value = None

    def add(
        customer_id: object, phrases: list[str], city: str, *, first_primary: bool
    ) -> list[Keyword]:
        return [_keyword(phrase, primary=index == 0) for index, phrase in enumerate(phrases)]

    repositories.add_keywords.side_effect = add
    site = WEBSITE.model_copy(
        update={
            "legal_notice_url": None,
            "footer_nap": {
                "name": "Dachdeckerei Hollenbeck GmbH",
                "street": "Hiltruper Straße 210",
                "postal_code": "48165",
                "city": "Münster",
            },
        }
    )

    async def crawl(start_url: HttpUrl) -> WebsiteSnapshot:
        return site

    async def search_businesses(query: str, api_key: SecretStr) -> list[places.BusinessCandidate]:
        recorder.record("search_businesses", query)
        return []

    monkeypatch.setattr(website, "crawl_website", crawl)
    monkeypatch.setattr(places, "search_businesses", search_businesses)

    raw = await run.collect_audit(AUDIT_ID, repositories, collector_settings, open_gate)

    assert recorder.calls["search_businesses"] == [("Dachdeckerei Hollenbeck GmbH Münster",)]
    assert [(item.field, item.source) for item in raw.identified] == [
        ("name", "footer"),
        ("city", "footer"),
        ("industry", "name_words"),
        ("street", "footer"),
        ("keywords", "industry_services"),
    ]
    own = recorder.calls["read_profile"][0][0]
    assert isinstance(own, ProfileTarget)
    assert (own.name, own.address) == (
        "Dachdeckerei Hollenbeck GmbH",
        "Hiltruper Straße 210, 48165 Münster",
    )
    assert [result.phrase for result in raw.serps if result.engine == "google"][:3] == [
        "Dachdecker Münster",
        "Maler Münster",
        "Fliesenleger Münster",
    ]
    assert "identify: no Google profile clearly belongs to Dachdeckerei Hollenbeck GmbH" in (
        raw.warnings
    )


async def test_collect_audit_of_a_recorded_customer_identifies_nothing(
    repositories: MagicMock,
    recorder: Recorder,
    collector_settings: Settings,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A customer with name, city, industry and keywords is collected as before."""
    identify_customer = AsyncMock()
    monkeypatch.setattr(run.identify, "identify_customer", identify_customer)

    raw = await run.collect_audit(AUDIT_ID, repositories, collector_settings, open_gate)

    identify_customer.assert_not_awaited()
    assert raw.identified == []
    repositories.update_customer.assert_not_called()
