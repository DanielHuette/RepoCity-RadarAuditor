"""Tests for reading Google Maps in the shared browser.

Context: Unit tests on the fake browser with saved Maps pages; no Chromium, no network. |
Input: tests/fixtures/maps. | Output: Assertions.
"""

from datetime import date

import pytest
from conftest import CAPTCHA_PAGE, FIXTURES_DIR, BrowserFactory, FakeDocument
from playwright.async_api import Error as PlaywrightError

from radarauditor.collectors import maps
from radarauditor.collectors.browser import Geolocation
from radarauditor.collectors.maps import ProfileTarget
from radarauditor.errors import CollectionError, ProfileNotFoundError, SearchBlockedError

MAPS_DIR = FIXTURES_DIR / "maps"
OWN_PLACE_ID = "ChIJO_EKbh27uUcRf25dSz8uHIo"
PLACE_URL = (
    "https://www.google.com/maps/place/Dachdeckerei+Hollenbeck+GmbH/data=!4m7!3m6"
    "!1s0x47b9bb1d6e0a1f3b:0x8a1c2e3f4b5d6e7f!19sChIJO_EKbh27uUcRf25dSz8uHIo"
)


def _page(name: str) -> str:
    return (MAPS_DIR / name).read_text(encoding="utf-8")


async def test_read_profile_opens_reviews_sorted_by_newest_and_info(
    fake_browser: BrowserFactory,
) -> None:
    """A known place id opens the profile; reviews are sorted, scrolled and expanded."""
    reviews = FakeDocument(_page("place_hollenbeck_reviews.html"), url=PLACE_URL)
    fake = fake_browser(
        [
            FakeDocument(_page("place_hollenbeck.html"), url=PLACE_URL),
            reviews,
            reviews,
            FakeDocument(_page("place_hollenbeck_info.html"), url=PLACE_URL),
        ]
    )
    fake.page.advancing_clicks = {"Rezensionen", maps.NEWEST_SELECTOR, "Info"}

    async with fake.service as service:
        place = await maps.read_profile(service, ProfileTarget("Hollenbeck", place_id=OWN_PLACE_ID))

    assert fake.page.visited == [maps.PLACE_ID_URL.format(place_id=OWN_PLACE_ID)]
    assert fake.page.clicked == [
        "Rezensionen",
        "Rezensionen sortieren",
        maps.NEWEST_SELECTOR,
        "Info",
    ]
    assert fake.page.scripts == [
        maps.SCROLL_REVIEWS_SCRIPT,
        maps.SCROLL_REVIEWS_SCRIPT,
        maps.EXPAND_REVIEWS_SCRIPT,
    ]
    assert place.place_id == OWN_PLACE_ID
    assert len(place.recent_reviews) == 4
    assert place.attributes is not None and "Rechnung" in place.attributes
    assert str(place.google_maps_uri) == PLACE_URL


async def test_read_profile_picks_the_business_from_a_result_list(
    fake_browser: BrowserFactory,
) -> None:
    """A name search that lists several businesses opens the matching one."""
    overview = "<div role='main'><h1>Dachdeckerei Hollenbeck GmbH</h1></div>"
    fake = fake_browser(
        [FakeDocument(_page("search_dachdecker_muenster.html")), FakeDocument(overview)]
    )

    async with fake.service as service:
        place = await maps.read_profile(
            service, ProfileTarget("Dachdeckerei Hollenbeck GmbH", "Hiltruper Straße 210, Münster")
        )

    search, opened = fake.page.visited
    assert search.startswith(f"{maps.MAPS_URL}/search/Dachdeckerei+Hollenbeck+GmbH")
    assert OWN_PLACE_ID in opened
    assert fake.page.clicked == []
    assert place.name == "Dachdeckerei Hollenbeck GmbH"
    assert place.attributes is None


async def test_read_profile_reports_a_missing_business(fake_browser: BrowserFactory) -> None:
    fake = fake_browser([FakeDocument(_page("search_dachdecker_muenster.html"))])

    async with fake.service as service:
        with pytest.raises(ProfileNotFoundError, match="Unbekannte Firma"):
            await maps.read_profile(service, ProfileTarget("Unbekannte Firma"))


async def test_read_profile_rejects_a_page_without_profile(fake_browser: BrowserFactory) -> None:
    fake = fake_browser(
        [
            FakeDocument(_page("search_dachdecker_muenster.html")),
            FakeDocument("<div role='main'><p>Leer</p></div>"),
        ]
    )

    async with fake.service as service:
        with pytest.raises(CollectionError, match="no readable profile"):
            await maps.read_profile(service, ProfileTarget("Bedachungen Overmann"))


async def test_read_profile_raises_on_a_bot_check(fake_browser: BrowserFactory) -> None:
    fake = fake_browser([FakeDocument(CAPTCHA_PAGE)])

    async with fake.service as service:
        with pytest.raises(SearchBlockedError):
            await maps.read_profile(service, ProfileTarget("Hollenbeck"))


async def test_search_listings_searches_from_the_given_point(fake_browser: BrowserFactory) -> None:
    """The map is centred on the point and the tab reports it as the device location."""
    fake = fake_browser([FakeDocument(_page("search_dachdecker_muenster.html"))])

    async with fake.service as service:
        listings = await maps.search_listings(
            service, "Dachdecker Münster", Geolocation(51.95, 7.62)
        )

    assert fake.page.visited == [
        f"{maps.MAPS_URL}/search/Dachdecker%20M%C3%BCnster/@51.9500000,7.6200000,14z?hl=de&gl=de"
    ]
    assert fake.context.geolocations[0] == {"latitude": 51.95, "longitude": 7.62, "accuracy": 50.0}
    assert [listing.name for listing in listings][:2] == [
        "Bedachungen Overmann",
        "Dach Service Kemper",
    ]


async def test_search_listings_that_open_one_profile_list_it(fake_browser: BrowserFactory) -> None:
    fake = fake_browser([FakeDocument(_page("place_hollenbeck.html"), url=PLACE_URL)])

    async with fake.service as service:
        listings = await maps.search_listings(service, "Dachdeckerei Hollenbeck")

    assert fake.page.visited == [f"{maps.MAPS_URL}/search/Dachdeckerei%20Hollenbeck?hl=de&gl=de"]
    assert [(item.name, item.place_id) for item in listings] == [
        ("Dachdeckerei Hollenbeck GmbH", OWN_PLACE_ID)
    ]


def test_profile_target_start_urls() -> None:
    assert ProfileTarget("A", url="https://maps/x").start_url() == "https://maps/x"
    assert (
        ProfileTarget("A", place_id="0x1:0x2").start_url().startswith(f"{maps.MAPS_URL}/search/A")
    )
    assert "place_id:ChIJ-x" in ProfileTarget("A", place_id="ChIJ-x").start_url()


class _BrokenControl:
    """Locator whose click times out, as a control Google renamed would."""

    @property
    def first(self) -> "_BrokenControl":
        """Return the locator itself."""
        return self

    async def click(self, timeout: int | None = None) -> None:
        """Fail like Playwright after the timeout."""
        raise PlaywrightError("Timeout 5000ms exceeded")


async def test_read_profile_keeps_the_profile_when_review_sorting_fails(
    fake_browser: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sort menu that cannot be operated leaves Google's order; the reviews are still read."""
    reviews = FakeDocument(_page("place_hollenbeck_reviews.html"), url=PLACE_URL)
    fake = fake_browser([FakeDocument(_page("place_hollenbeck.html"), url=PLACE_URL), reviews])
    fake.page.advancing_clicks = {"Rezensionen"}
    monkeypatch.setattr(fake.page, "locator", lambda selector: _BrokenControl())
    monkeypatch.setattr(maps, "ABOUT_TABS", ())

    async with fake.service as service:
        place = await maps.read_profile(service, ProfileTarget("Hollenbeck", place_id=OWN_PLACE_ID))

    assert len(place.recent_reviews) == 4
    assert place.attributes is None


async def test_read_profile_keeps_the_overview_when_a_tab_fails(
    fake_browser: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tab that cannot be opened leaves its fields unread."""
    fake = fake_browser([FakeDocument(_page("place_hollenbeck.html"), url=PLACE_URL)])
    monkeypatch.setattr(fake.page, "get_by_role", lambda role, name, exact=False: _BrokenControl())

    async with fake.service as service:
        place = await maps.read_profile(service, ProfileTarget("Hollenbeck", place_id=OWN_PLACE_ID))

    assert place.recent_reviews == []
    assert place.route_of("recent_reviews") is None
    assert place.review_count == 86


async def test_read_profile_reads_photo_dates_from_the_owner_tab(
    fake_browser: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The viewer opens, switches to "Vom Inhaber" and steps on until no next control is left.

    The fixtures are built from the labels of a real Maps viewer; the live Google page itself is
    not part of the test.
    """
    monkeypatch.setattr(maps, "REVIEW_TABS", ())
    monkeypatch.setattr(maps, "ABOUT_TABS", ())
    fake = fake_browser(
        [
            FakeDocument(_page("photos_overview.html"), url=PLACE_URL),
            FakeDocument(_page("photos_viewer_all.html"), url=PLACE_URL),
            FakeDocument(_page("photos_owner_1.html"), url=PLACE_URL),
            FakeDocument(_page("photos_owner_2.html"), url=PLACE_URL),
            FakeDocument(_page("photos_owner_3.html"), url=PLACE_URL),
            FakeDocument(_page("photos_owner_4.html"), url=PLACE_URL),
        ]
    )
    fake.page.advancing_clicks = {"Fotos ansehen", "Vom Inhaber", "Nächstes Foto"}
    target = ProfileTarget("Hollenbeck", place_id=OWN_PLACE_ID, with_photos=True)

    async with fake.service as service:
        place = await maps.read_profile(service, target)

    assert fake.page.clicked == [
        "Fotos ansehen",
        "Vom Inhaber",
        "Nächstes Foto",
        "Nächstes Foto",
        "Nächstes Foto",
    ]
    # Okt. 2025 is a Street View image, Apr. 2022 the "Alle" tab before switching.
    assert place.photo_newest_date == date(2025, 8, 31)
    assert place.photo_dated_count == 2
    assert place.route_of("photo_newest_date") == "browser"


async def test_read_profile_reads_all_photos_without_owner_photos(
    fake_browser: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maps, "REVIEW_TABS", ())
    monkeypatch.setattr(maps, "ABOUT_TABS", ())
    fake = fake_browser(
        [
            FakeDocument(_page("photos_overview.html"), url=PLACE_URL),
            FakeDocument(_page("photos_viewer_no_owner.html"), url=PLACE_URL),
            FakeDocument(_page("photos_viewer_no_owner.html"), url=PLACE_URL),
        ]
    )
    fake.page.advancing_clicks = {"Fotos ansehen", "Alle"}
    target = ProfileTarget("Hollenbeck", place_id=OWN_PLACE_ID, with_photos=True)

    async with fake.service as service:
        place = await maps.read_profile(service, target)

    assert fake.page.clicked == ["Fotos ansehen", "Alle"]
    assert place.photo_dated_count == 1
    assert place.photo_newest_date is not None


async def test_read_profile_leaves_photos_unread_without_a_viewer(
    fake_browser: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A photo control that opens nothing, or a viewer that fails, leaves the dates unread."""
    monkeypatch.setattr(maps, "REVIEW_TABS", ())
    monkeypatch.setattr(maps, "ABOUT_TABS", ())
    fake = fake_browser([FakeDocument(_page("photos_overview.html"), url=PLACE_URL)])
    target = ProfileTarget("Hollenbeck", place_id=OWN_PLACE_ID, with_photos=True)

    async with fake.service as service:
        place = await maps.read_profile(service, target)

    assert fake.page.clicked == ["Fotos ansehen"]
    assert (place.photo_newest_date, place.photo_dated_count) == (None, None)

    broken = fake_browser([FakeDocument(_page("photos_overview.html"), url=PLACE_URL)])
    monkeypatch.setattr(
        broken.page, "get_by_role", lambda role, name, exact=False: _BrokenControl()
    )
    async with broken.service as service:
        place = await maps.read_profile(service, target)
    assert place.photo_dated_count is None
    assert place.name == "Dachdeckerei Hollenbeck GmbH"


async def test_read_profile_opens_no_viewer_for_competitors(
    fake_browser: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maps, "REVIEW_TABS", ())
    monkeypatch.setattr(maps, "ABOUT_TABS", ())
    fake = fake_browser([FakeDocument(_page("photos_overview.html"), url=PLACE_URL)])

    async with fake.service as service:
        place = await maps.read_profile(service, ProfileTarget("Hollenbeck", place_id=OWN_PLACE_ID))

    assert fake.page.clicked == []
    assert place.photo_dated_count is None


async def test_read_profile_steps_through_at_most_the_photo_limit(
    fake_browser: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maps, "REVIEW_TABS", ())
    monkeypatch.setattr(maps, "ABOUT_TABS", ())
    monkeypatch.setattr(maps, "PHOTO_LIMIT", 3)
    owner = [_page(f"photos_owner_{number}.html") for number in (1, 2, 3, 1, 2)]
    fake = fake_browser(
        [
            FakeDocument(_page("photos_overview.html"), url=PLACE_URL),
            FakeDocument(_page("photos_viewer_all.html"), url=PLACE_URL),
            *(FakeDocument(html, url=PLACE_URL) for html in owner),
        ]
    )
    fake.page.advancing_clicks = {"Fotos ansehen", "Vom Inhaber", "Nächstes Foto"}
    target = ProfileTarget("Hollenbeck", place_id=OWN_PLACE_ID, with_photos=True)

    async with fake.service as service:
        place = await maps.read_profile(service, target)

    # Three photos need two steps; Okt. 2025 among them is Street View.
    assert fake.page.clicked.count("Nächstes Foto") == 2
    assert place.photo_dated_count == 2
