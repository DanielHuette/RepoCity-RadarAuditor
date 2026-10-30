"""Tests for the Google Maps page parsers.

Context: Pure parser tests on saved Maps pages. | Input: tests/fixtures/maps. | Output: Assertions.
"""

from datetime import UTC, date, datetime

import pytest
from conftest import FIXTURES_DIR

from radarauditor.collectors import maps_pages
from radarauditor.collectors.maps_pages import MapListing
from radarauditor.collectors.snapshot import PlaceSnapshot

MAPS_DIR = FIXTURES_DIR / "maps"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PLACE_URL = (
    "https://www.google.com/maps/place/Dachdeckerei+Hollenbeck+GmbH/data=!4m7!3m6"
    "!1s0x47b9bb1d6e0a1f3b:0x8a1c2e3f4b5d6e7f"
)
OWN_PLACE_ID = "ChIJO_EKbh27uUcRf25dSz8uHIo"


def _page(name: str) -> str:
    return (MAPS_DIR / name).read_text(encoding="utf-8")


def test_parse_place_reads_every_tab() -> None:
    """Overview, newest reviews and info tab fill every field and mark it as read in the browser."""
    place = maps_pages.parse_place(
        _page("place_hollenbeck.html"),
        PLACE_URL,
        NOW,
        reviews_html=_page("place_hollenbeck_reviews.html"),
        about_html=_page("place_hollenbeck_info.html"),
    )

    assert place.place_id == OWN_PLACE_ID
    assert place.name == "Dachdeckerei Hollenbeck GmbH"
    assert place.formatted_address == "Hiltruper Straße 210, 48165 Münster"
    assert place.phone == "0251 123456"
    assert str(place.website) == "https://www.dachdecker-hollenbeck.de/"
    assert place.primary_type == "Dachdecker"
    assert (place.rating, place.review_count, place.photo_count) == (4.7, 86, 37)
    assert place.has_opening_hours is True
    assert place.business_status == "OPERATIONAL"
    assert place.description is not None
    assert place.description.startswith("Dachdecker in Münster seit 1987")
    assert place.services == [
        "Dachsanierung",
        "Flachdachabdichtung",
        "Dachfenster einbauen",
        "Dachrinnenreinigung",
    ]
    assert place.products == []
    assert place.service_areas == ["Münster", "Telgte", "Greven", "Senden", "Everswinkel"]
    assert place.post_ages_days == [14, 61]
    assert place.question_count == 4
    assert place.attributes == [
        "Rollstuhlgerechter Eingang",
        "Rollstuhlgerechter Parkplatz",
        "Debitkarten",
        "Rechnung",
        "Termin erforderlich",
    ]
    assert set(place.value_routes.values()) == {"browser"}
    assert "types" not in place.value_routes
    assert place.route_of("recent_reviews") == "browser"


def test_parse_reviews_keeps_owner_replies_and_dates() -> None:
    """Every review carries reviewReply; dates count back from the moment of reading."""
    reviews = maps_pages.parse_reviews(_page("place_hollenbeck_reviews.html"), NOW)

    assert [review["rating"] for review in reviews] == [5.0, 2.0, 5.0, 4.0]
    first = reviews[0]
    assert first["publishTime"] == "2026-10-16T12:00:00+00:00"
    assert first["relativePublishTimeDescription"] == "vor einer Woche"
    assert str(first["text"]).startswith("Dachsanierung in Münster-Hiltrup")
    assert first["reviewReply"] == {
        "text": "Vielen Dank, Frau Vennemann! Gerne wieder.",
        "publishTime": "2026-10-18T12:00:00+00:00",
        "relativePublishTimeDescription": "vor 5 Tagen",
    }
    assert reviews[2]["reviewReply"] is None
    assert reviews[3]["text"] == ""
    assert reviews[3]["relativePublishTimeDescription"] == "Bearbeitet: vor 2 Monaten"


def test_parse_place_without_tabs_leaves_tab_fields_unread() -> None:
    """Without the review and info tabs, reviews and attributes have no route."""
    place = maps_pages.parse_place(_page("place_hollenbeck.html"), PLACE_URL, NOW)

    assert place.recent_reviews == []
    assert place.attributes is None
    assert place.route_of("recent_reviews") is None
    assert place.route_of("attributes") is None
    assert place.description is not None


def test_parse_place_with_info_tab_but_no_description_reads_an_empty_one() -> None:
    """An opened info tab without an owner text means the profile has no description."""
    overview = "<div role='main'><h1>Betrieb</h1></div>"
    about = "<div role='main'><h2>Zahlungen</h2><ul><li>Rechnung</li></ul></div>"

    place = maps_pages.parse_place(overview, PLACE_URL, NOW, about_html=about)

    assert place.description == ""
    assert place.attributes == ["Rechnung"]
    assert place.question_count is None
    assert place.review_count == 0


@pytest.mark.parametrize(
    ("label", "status"),
    [
        ("Dauerhaft geschlossen", "CLOSED_PERMANENTLY"),
        ("Vorübergehend geschlossen", "CLOSED_TEMPORARILY"),
    ],
)
def test_parse_place_reads_closed_status(label: str, status: str) -> None:
    html = f"<div role='main'><h1>Betrieb</h1><span>{label}</span></div>"
    assert maps_pages.parse_place(html, PLACE_URL, NOW).business_status == status


def test_parse_listings_skips_sponsored_entries() -> None:
    """The sponsored first entry does not count; ids come from the place addresses."""
    listings = maps_pages.parse_listings(_page("search_dachdecker_muenster.html"))

    assert [listing.name for listing in listings] == [
        "Bedachungen Overmann",
        "Dach Service Kemper",
        "Dachdeckerei Hollenbeck GmbH",
        "Zimmerei Lüttke",
        "Bedachung Rüschhaus",
    ]
    assert listings[0].place_id == "ChIJAAAAAAAAAAAiIiIiIiIiIg"
    assert listings[1].place_id == "0x47b9bc0000000003:0x3333333333333333"
    assert (listings[0].rating, listings[0].review_count) == (4.8, 154)


def test_parse_listings_of_a_place_page_is_empty() -> None:
    assert maps_pages.parse_listings(_page("place_hollenbeck.html")) == []


def test_is_place_page_tells_profile_from_list() -> None:
    assert maps_pages.is_place_page(_page("place_hollenbeck.html")) is True
    assert maps_pages.is_place_page(_page("search_dachdecker_muenster.html")) is False
    assert maps_pages.is_place_page("<div role='main'><p>Keine Ergebnisse</p></div>") is False


def test_listing_position_prefers_ids_and_falls_back_to_names() -> None:
    listings = maps_pages.parse_listings(_page("search_dachdecker_muenster.html"))

    assert maps_pages.listing_position(listings, "Unbekannt", {OWN_PLACE_ID}) == 3
    assert maps_pages.listing_position(listings, "Dachdeckerei Hollenbeck", set()) == 3
    assert maps_pages.listing_position(listings, "Nicht gelistet", {"ChIJ-other"}) is None


def test_listing_position_matches_feature_ids() -> None:
    listing = MapListing("Kemper", "u", "0x1:0x2", None, None)
    assert maps_pages.listing_position([listing], "Anders", {"0x1:0x2"}) == 1


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("vor einer Woche", 7.0),
        ("vor 3 Wochen", 21.0),
        ("vor einem Monat", 30.44),
        ("Bearbeitet: vor 2 Jahren", 730.5),
        ("vor 5 Tagen", 5.0),
        ("vor 3 Stunden", 0.125),
        ("2 weeks ago", 14.0),
        ("a year ago", 365.25),
        ("gestern", None),
    ],
)
def test_relative_age_days(text: str, days: float | None) -> None:
    assert maps_pages.relative_age_days(text) == days


def test_tab_labels_and_place_ids() -> None:
    assert maps_pages.tab_labels(_page("place_hollenbeck.html"))[1].startswith("Rezensionen")
    assert maps_pages.place_id_from("https://maps.google.com/?q=x") is None
    assert maps_pages.place_id_from(PLACE_URL) == "0x47b9bb1d6e0a1f3b:0x8a1c2e3f4b5d6e7f"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Jan. 2026", date(2026, 1, 31)),
        ("Feb. 2024", date(2024, 2, 29)),
        ("März 2025", date(2025, 3, 31)),
        ("Apr. 2022", date(2022, 4, 30)),
        ("Mai 2023", date(2023, 5, 31)),
        ("Juni 2025", date(2025, 6, 30)),
        ("Juli 2025", date(2025, 7, 31)),
        ("Aug. 2025", date(2025, 8, 31)),
        ("Sept. 2025", date(2025, 9, 30)),
        ("Nov. 2024", date(2024, 11, 30)),
        ("Dez. 2019", date(2019, 12, 31)),
        ("Okt. 2026", date(2026, 10, 23)),
        ("vor 3 Monaten", date(2026, 7, 24)),
        ("vor einem Jahr", date(2025, 10, 23)),
        ("vor 2 Wochen", date(2026, 10, 9)),
        ("Maimarkt 2022", None),
        ("Mitglied der Innung", None),
        ("Im März 2025 haben wir das Dach am Hafen in Münster komplett neu gedeckt.", None),
    ],
)
def test_photo_date_reads_german_month_and_relative_dates(text: str, expected: date | None) -> None:
    """A month-year counts as the month's last day, never after today; relative dates count back."""
    assert maps_pages.photo_date(text, NOW) == expected


def test_viewer_photo_ignores_panel_texts_and_gallery_tabs() -> None:
    """Dates of the place panel and the "Street View & 360°" tab say nothing about the photo."""
    before = maps_pages.page_texts(_page("photos_overview.html"))

    owner = maps_pages.viewer_photo(_page("photos_owner_1.html"), before, NOW)
    street_view = maps_pages.viewer_photo(_page("photos_viewer_all.html"), before, NOW)
    undated = maps_pages.viewer_photo(_page("photos_owner_4.html"), before, NOW)

    assert owner == maps_pages.ViewerPhoto(taken=date(2025, 8, 31), street_view=False)
    assert street_view == maps_pages.ViewerPhoto(taken=date(2022, 4, 30), street_view=True)
    assert undated == maps_pages.ViewerPhoto(taken=None, street_view=False)


def test_viewer_controls_are_found_by_their_labels() -> None:
    assert maps_pages.photo_button(_page("photos_overview.html")) == "Fotos ansehen"
    assert maps_pages.photo_button(_page("place_hollenbeck.html")) == "Foto von "
    assert maps_pages.photo_button("<div aria-label='Route'></div>") is None
    assert maps_pages.gallery_tab(_page("photos_viewer_all.html")) == "Vom Inhaber"
    assert maps_pages.gallery_tab(_page("photos_viewer_no_owner.html")) == "Alle"
    assert maps_pages.gallery_tab(_page("photos_overview.html")) is None
    assert maps_pages.next_photo_button(_page("photos_owner_1.html")) == "Nächstes Foto"
    assert maps_pages.next_photo_button(_page("photos_owner_4.html")) is None


def test_newest_photo_leaves_out_street_view_and_undated_photos() -> None:
    photos = [
        maps_pages.ViewerPhoto(date(2025, 8, 31), street_view=False),
        maps_pages.ViewerPhoto(date(2026, 10, 1), street_view=True),
        maps_pages.ViewerPhoto(None, street_view=False),
        maps_pages.ViewerPhoto(date(2025, 6, 30), street_view=False),
    ]
    assert maps_pages.newest_photo(photos) == maps_pages.PhotoDates(date(2025, 8, 31), 2)
    assert maps_pages.newest_photo([]) == maps_pages.PhotoDates(None, 0)


def test_parse_place_stores_photo_dates_with_the_browser_route() -> None:
    photos = maps_pages.PhotoDates(date(2025, 8, 31), 2)
    place = maps_pages.parse_place(_page("place_hollenbeck.html"), PLACE_URL, NOW, photos=photos)
    unread = maps_pages.parse_place(_page("place_hollenbeck.html"), PLACE_URL, NOW)

    assert (place.photo_newest_date, place.photo_dated_count) == (date(2025, 8, 31), 2)
    assert place.route_of("photo_newest_date") == "browser"
    assert (unread.photo_newest_date, unread.photo_dated_count) == (None, None)
    assert unread.route_of("photo_newest_date") is None


def test_place_snapshot_without_photo_fields_still_loads() -> None:
    """Snapshots stored before the photo dates existed load with both fields unread."""
    stored = maps_pages.parse_place(_page("place_hollenbeck.html"), PLACE_URL, NOW).model_dump(
        mode="json", exclude={"photo_newest_date", "photo_dated_count"}
    )
    place = PlaceSnapshot.model_validate(stored)
    assert (place.photo_newest_date, place.photo_dated_count) == (None, None)


def test_listing_without_reviews_counts_zero() -> None:
    html = (
        "<div role='feed'><div role='article'>"
        "<a href='https://www.google.com/maps/place/Neu' aria-label='Dach Neu'></a>"
        "<span>Keine Rezensionen</span></div>"
        "<div role='article'><a href='https://www.google.com/maps/place/Alt' aria-label='Dach Alt'>"
        "</a></div></div>"
    )
    assert [(item.name, item.review_count) for item in maps_pages.parse_listings(html)] == [
        ("Dach Neu", 0),
        ("Dach Alt", None),
    ]
