"""Tests for the route choice between Google Maps in the browser and the Places API.

Context: Unit tests with the Maps reader and the Places API replaced by fakes. | Input: Fakes. |
Output: Assertions.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from radarauditor.collectors import maps, places, profiles
from radarauditor.collectors.browser import BrowserService, Geolocation
from radarauditor.collectors.maps import ProfileTarget
from radarauditor.collectors.maps_pages import MapListing
from radarauditor.collectors.profiles import Business, ProfileRoute
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import PlaceSnapshot
from radarauditor.db.app_settings import Source, SourcesSettings
from radarauditor.errors import (
    CollectionError,
    ProfileNotFoundError,
    SearchBlockedError,
    SourceDisabledError,
)

KEY = SecretStr("google-key")
OWN = Business(
    "Dachdeckerei Hollenbeck GmbH",
    "Hiltruper Straße 210, 48165 Münster",
    "Münster",
    frozenset({"ChIJ-own"}),
)
CENTRE = (51.93, 7.63)


def _place(place_id: str, name: str, route: str = "browser") -> PlaceSnapshot:
    return PlaceSnapshot(
        place_id=place_id,
        name=name,
        formatted_address="Münster",
        phone=None,
        website=None,
        primary_type="Dachdecker",
        types=[],
        rating=4.5,
        review_count=10,
        has_opening_hours=True,
        photo_count=3,
        recent_reviews=[],
        business_status="OPERATIONAL",
        value_routes={"name": route},
    )


def _listing(name: str, place_id: str | None = None) -> MapListing:
    return MapListing(name, f"https://www.google.com/maps/place/{name}", place_id, 4.5, 10)


LISTINGS = [
    _listing("Bedachungen Overmann", "ChIJ-overmann"),
    _listing("Dachdeckerei Hollenbeck", "0x1:0x2"),
    _listing("Dach Service Kemper", "0x3:0x4"),
    _listing("Zimmerei Lüttke"),
    _listing("Bedachung Rüschhaus"),
]


@dataclass
class FakeMaps:
    """Google Maps reader double; a reader raises the queued error before answering."""

    errors: list[Exception] = field(default_factory=list)
    profiles: list[ProfileTarget] = field(default_factory=list)
    searches: list[tuple[str, Geolocation | None]] = field(default_factory=list)
    listings: list[MapListing] = field(default_factory=lambda: list(LISTINGS))

    async def read_profile(self, browser: BrowserService, target: ProfileTarget) -> PlaceSnapshot:
        """Record the target and answer with a browser profile."""
        self.profiles.append(target)
        self._raise_queued()
        return _place(target.place_id or f"id-{target.name}", target.name)

    async def search_listings(
        self, browser: BrowserService, phrase: str, at: Geolocation | None = None
    ) -> list[MapListing]:
        """Record the search and answer with the prepared listings."""
        self.searches.append((phrase, at))
        self._raise_queued()
        return self.listings

    def _raise_queued(self) -> None:
        if self.errors:
            raise self.errors.pop(0)


@dataclass
class FakePlaces:
    """Places API double recording every call."""

    calls: list[tuple[object, ...]] = field(default_factory=list)
    ranked: list[str] = field(default_factory=lambda: ["ChIJ-a", "ChIJ-own", "ChIJ-b"])
    found_id: str | None = "ChIJ-found"

    async def find_place(self, name: str, address: str, api_key: SecretStr) -> str | None:
        """Record the lookup and answer with the prepared id."""
        self.calls.append(("find_place", name, address))
        return self.found_id

    async def place_details(self, place_id: str, api_key: SecretStr) -> PlaceSnapshot:
        """Record the request and answer with a Places profile."""
        self.calls.append(("place_details", place_id))
        return _place(place_id, place_id, route="places_api")

    async def top_competitors(
        self, phrase: str, latitude: float, longitude: float, api_key: SecretStr, limit: int = 3
    ) -> list[PlaceSnapshot]:
        """Record the request and answer with the customer and two rivals."""
        self.calls.append(("top_competitors", phrase, limit))
        return [
            _place("ChIJ-own", "Dachdeckerei Hollenbeck GmbH", "places_api"),
            _place("ChIJ-x", "Bedachungen Overmann", "places_api"),
            _place("ChIJ-y", "Dach Service Kemper", "places_api"),
        ]

    async def ranked_place_ids(
        self, phrase: str, latitude: float, longitude: float, api_key: SecretStr, radius: float
    ) -> list[str]:
        """Record the request and answer with the prepared ranking."""
        self.calls.append(("ranked_place_ids", phrase, latitude, longitude, radius))
        return self.ranked


@pytest.fixture
def fake_maps(monkeypatch: pytest.MonkeyPatch) -> FakeMaps:
    fake = FakeMaps()
    monkeypatch.setattr(maps, "read_profile", fake.read_profile)
    monkeypatch.setattr(maps, "search_listings", fake.search_listings)
    return fake


@pytest.fixture
def fake_places(monkeypatch: pytest.MonkeyPatch) -> FakePlaces:
    fake = FakePlaces()
    for name in ("find_place", "place_details", "top_competitors", "ranked_place_ids"):
        monkeypatch.setattr(places, name, getattr(fake, name))
    return fake


class Quota:
    """Counts uses per source; listed sources allow only the given number."""

    def __init__(self, allowed: dict[str, int] | None = None) -> None:
        """Start without uses."""
        self.allowed = allowed or {}
        self.used: dict[str, int] = {}

    def consume(self, source: str, cap: int) -> bool:
        """Count one use and answer whether it was allowed."""
        self.used[source] = self.used.get(source, 0) + 1
        return self.used[source] <= self.allowed.get(source, cap)


RouteFactory = Callable[..., tuple[ProfileRoute, Quota, AsyncMock]]


@pytest.fixture
def make_route() -> RouteFactory:
    def build(
        sources: SourcesSettings | None = None, allowed: dict[str, int] | None = None
    ) -> tuple[ProfileRoute, Quota, AsyncMock]:
        quota = Quota(allowed)
        browser = AsyncMock(spec=BrowserService)
        gate = SourceGate(sources or SourcesSettings(), quota.consume)
        return ProfileRoute(browser, gate, KEY), quota, browser.pause

    return build


async def test_profile_comes_from_the_browser(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    route, quota, pause = make_route()
    notes: list[str] = []

    place = await route.profile(ProfileTarget("Hollenbeck", place_id="ChIJ-own"), notes)

    assert place is not None and place.place_id == "ChIJ-own"
    assert fake_places.calls == []
    assert quota.used == {"google_maps": 1}
    assert notes == []
    pause.assert_not_awaited()


async def test_a_block_switches_the_rest_of_the_job_to_the_places_api(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    route, quota, pause = make_route()
    fake_maps.errors = [SearchBlockedError("www.google.com shows a bot check")]
    notes: list[str] = []

    first = await route.profile(ProfileTarget("Hollenbeck", "Münster", "ChIJ-own"), notes)
    second = await route.profile(ProfileTarget("Overmann", "Münster"), notes)

    assert first is not None and first.route_of("name") == "places_api"
    assert second is not None and second.place_id == "ChIJ-found"
    assert len(fake_maps.profiles) == 1
    assert fake_places.calls == [
        ("place_details", "ChIJ-own"),
        ("find_place", "Overmann", "Münster"),
        ("place_details", "ChIJ-found"),
    ]
    assert notes == [
        "Google Maps blocked the browser (www.google.com shows a bot check), Places API used"
    ]
    assert quota.used == {"google_maps": 1, "places": 3}


async def test_an_unreadable_page_falls_back_for_that_value_only(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    route, _, pause = make_route()
    fake_maps.errors = [CollectionError("layout changed")]
    notes: list[str] = []

    await route.profile(ProfileTarget("Hollenbeck", place_id="ChIJ-own"), notes)
    again = await route.profile(ProfileTarget("Hollenbeck", place_id="ChIJ-own"), notes)

    assert again is not None and again.route_of("name") == "browser"
    assert notes == ["Google Maps not readable for Hollenbeck (layout changed), Places API used"]
    pause.assert_awaited_once()


async def test_switched_off_maps_go_to_places_and_switched_off_places_raise(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    maps_off = SourcesSettings.model_validate({"google_maps": {"enabled": False}})
    route, _, _ = make_route(maps_off)
    place = await route.profile(ProfileTarget("Hollenbeck", place_id="ChIJ-own"), [])
    assert place is not None and fake_maps.profiles == []

    both_off = SourcesSettings.model_validate(
        {"google_maps": {"enabled": False}, "places": {"enabled": False}}
    )
    route, _, _ = make_route(both_off)
    with pytest.raises(SourceDisabledError):
        await route.profile(ProfileTarget("Hollenbeck", place_id="ChIJ-own"), [])


async def test_a_capped_maps_source_falls_back_to_places(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    route, quota, _ = make_route(allowed={"google_maps": 0})

    place = await route.profile(ProfileTarget("Hollenbeck", place_id="ChIJ-own"), [])

    assert place is not None and place.route_of("name") == "places_api"
    assert route.gate.exhausted == frozenset({Source.GOOGLE_MAPS})


async def test_a_business_maps_does_not_list_is_not_found(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    route, _, _ = make_route()
    fake_maps.errors = [ProfileNotFoundError("Google Maps lists no profile for X")]

    assert await route.profile(ProfileTarget("X"), []) is None
    assert fake_places.calls == []


async def test_competitors_are_the_top_listings_without_own_and_known(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    route, quota, _ = make_route()
    known = [_place("ChIJ-overmann", "Bedachungen Overmann")]

    found = await route.competitors("Dachdecker Münster", CENTRE, known, OWN, 2, [])

    assert [place.name for place in found] == ["Dach Service Kemper", "Zimmerei Lüttke"]
    assert fake_maps.searches == [("Dachdecker Münster", Geolocation(*CENTRE))]
    assert [target.url for target in fake_maps.profiles] == [
        LISTINGS[2].url,
        LISTINGS[3].url,
    ]
    assert fake_maps.profiles[0].address == "Münster"
    assert quota.used == {"google_maps": 3}


async def test_competitors_from_places_when_maps_blocks(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    route, _, _ = make_route()
    fake_maps.errors = [SearchBlockedError("blocked")]

    found = await route.competitors("Dachdecker Münster", CENTRE, [], OWN, 3, [])

    assert [place.name for place in found] == ["Bedachungen Overmann", "Dach Service Kemper"]
    assert fake_places.calls == [("top_competitors", "Dachdecker Münster", 5)]


async def test_map_ranking_reuses_the_competitor_search(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    route, _, _ = make_route()
    await route.competitors("Dachdecker Münster", CENTRE, [], OWN, 1, [])

    ranking = await route.map_ranking("Dachdecker Münster", CENTRE, OWN, [])

    assert len(fake_maps.searches) == 1
    assert (ranking.position, ranking.route) == (2, "browser")
    assert ranking.listed[0] == "Bedachungen Overmann"


async def test_map_ranking_from_places_identifier_search(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    route, _, _ = make_route()
    fake_maps.errors = [SearchBlockedError("blocked")]

    ranking = await route.map_ranking("Dachdecker Münster", CENTRE, OWN, [])

    assert (ranking.position, ranking.route, ranking.listed) == (2, "places_api", [])
    assert fake_places.calls == [
        ("ranked_place_ids", "Dachdecker Münster", *CENTRE, profiles.RANKING_RADIUS_METERS)
    ]


async def test_places_ranking_looks_the_customer_up_once(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    route, _, _ = make_route()
    fake_maps.errors = [SearchBlockedError("blocked")]
    without_id = Business(OWN.name, OWN.address, OWN.city, frozenset({"0x1:0x2"}))
    fake_places.ranked = ["ChIJ-found"]

    first = await route.map_ranking("Dachdecker Münster", CENTRE, without_id, [])
    second = await route.map_ranking("Flachdach Münster", CENTRE, without_id, [])

    assert first.position == second.position == 1
    assert [call[0] for call in fake_places.calls].count("find_place") == 1

    fake_places.found_id = None
    unknown = Business("Neu", "Weg 1", "Münster")
    with pytest.raises(CollectionError, match="knows no profile of Neu"):
        await route.map_ranking("Dachdecker Münster", CENTRE, unknown, [])


async def test_grid_searches_every_point_and_marks_the_route(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    """Every point is searched from its position; one the browser cannot read uses Places."""
    route, _, pause = make_route()
    fake_maps.errors = [
        CollectionError("slow"),
    ]
    notes: list[str] = []

    points = await route.grid("Dachdecker Münster", CENTRE, OWN, 2, notes)

    assert [point.key for point in points] == ["r1c1", "r1c2", "r2c1", "r2c2"]
    by_key = {point.key: (point.position, point.route) for point in points}
    # The south-west point (r2c1) was searched first and fell back to the Places API.
    assert by_key["r2c1"] == (2, "places_api")
    assert by_key["r1c1"] == (2, "browser")
    assert all(at is not None for _, at in fake_maps.searches)
    assert fake_places.calls[0][-1] == profiles.GRID_RADIUS_METERS
    assert pause.await_count == 3


async def test_result_list_reuses_the_browser_search_and_marks_the_customer(
    make_route: RouteFactory, fake_maps: FakeMaps, fake_places: FakePlaces
) -> None:
    """The list comes from the cached Maps search; no further page is opened."""
    route, _, _ = make_route()
    await route.map_ranking("Dachdecker Münster", CENTRE, OWN, [])

    results = await route.result_list("Dachdecker Münster", CENTRE, OWN, [])

    assert len(fake_maps.searches) == 1
    assert results.route == "browser"
    assert [(item.name, item.review_count, item.is_customer) for item in results.businesses] == [
        ("Bedachungen Overmann", 10, False),
        ("Dachdeckerei Hollenbeck", 10, True),
        ("Dach Service Kemper", 10, False),
        ("Zimmerei Lüttke", 10, False),
        ("Bedachung Rüschhaus", 10, False),
    ]
    assert fake_places.calls == []


async def test_result_list_from_places_when_maps_blocks(
    make_route: RouteFactory,
    fake_maps: FakeMaps,
    fake_places: FakePlaces,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def listed_businesses(
        phrase: str, latitude: float, longitude: float, api_key: SecretStr, radius: float
    ) -> list[places.ListedPlace]:
        fake_places.calls.append(("listed_businesses", phrase, radius))
        return [
            places.ListedPlace("ChIJ-x", "Bedachungen Overmann", 154, 4.8),
            places.ListedPlace("ChIJ-own", "Hollenbeck", 86, 4.7),
        ]

    monkeypatch.setattr(places, "listed_businesses", listed_businesses)
    route, quota, _ = make_route()
    fake_maps.errors = [SearchBlockedError("blocked")]
    notes: list[str] = []

    results = await route.result_list("Dachdecker Münster", CENTRE, OWN, notes)

    assert results.route == "places_api"
    assert [(item.name, item.review_count, item.is_customer) for item in results.businesses] == [
        ("Bedachungen Overmann", 154, False),
        ("Hollenbeck", 86, True),
    ]
    assert fake_places.calls == [
        ("listed_businesses", "Dachdecker Münster", profiles.RANKING_RADIUS_METERS)
    ]
    assert quota.used[Source.PLACES.value] == 1
    assert notes == ["Google Maps blocked the browser (blocked), Places API used"]
