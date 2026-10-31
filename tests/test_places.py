"""Tests for the Places API (New) collector.

Context: Unit tests with mocked external calls. | Input: Fixtures. | Output: Assertions.
"""

import json
from decimal import Decimal

import httpx
import pytest
import respx
from pydantic import SecretStr

from radarauditor.collectors import places
from radarauditor.errors import CollectionError, TransientCollectionError

KEY = SecretStr("google-key")

pytestmark = pytest.mark.usefixtures("fast_retries")


def _place(place_id: str, name: str, **extra: object) -> dict[str, object]:
    return {
        "id": place_id,
        "displayName": {"text": name, "languageCode": "de"},
        "formattedAddress": "Hiltruper Str. 210, 48165 Münster, Deutschland",
        **extra,
    }


FULL_PLACE = _place(
    "ChIJ-own",
    "Dachdeckerei Hollenbeck GmbH",
    nationalPhoneNumber="0251 123456",
    websiteUri="https://www.dachdecker-hollenbeck.de/",
    primaryType="roofing_contractor",
    types=["roofing_contractor", "point_of_interest"],
    rating=4.8,
    userRatingCount=127,
    regularOpeningHours={"weekdayDescriptions": ["Montag: 07:30–17:00"]},
    photos=[{"name": "p1"}, {"name": "p2"}],
    reviews=[
        {
            "rating": 5,
            "text": {"text": "Schnell und sauber.", "languageCode": "de"},
            "publishTime": "2026-09-01T10:00:00Z",
            "relativePublishTimeDescription": "vor einem Monat",
            "authorAttribution": {"displayName": "Kunde"},
        }
    ],
    businessStatus="OPERATIONAL",
    googleMapsUri="https://maps.google.com/?cid=42",
)


@respx.mock
async def test_find_place_returns_first_id_and_sends_field_mask() -> None:
    """`find_place` returns first id and sends field mask."""
    route = respx.post(places.SEARCH_TEXT_URL).mock(
        return_value=httpx.Response(200, json={"places": [{"id": "ChIJ-a"}, {"id": "ChIJ-b"}]})
    )

    place_id = await places.find_place("Dachdeckerei Hollenbeck", "48165 Münster", KEY)

    assert place_id == "ChIJ-a"
    request = route.calls[0].request
    assert request.headers["X-Goog-FieldMask"] == "places.id"
    assert request.headers["X-Goog-Api-Key"] == "google-key"
    assert json.loads(request.content)["textQuery"] == "Dachdeckerei Hollenbeck 48165 Münster"


@respx.mock
async def test_find_place_returns_none_without_results() -> None:
    """`find_place` returns none without results."""
    respx.post(places.SEARCH_TEXT_URL).mock(return_value=httpx.Response(200, json={}))

    assert await places.find_place("Unbekannt", "Münster", KEY) is None


@respx.mock
async def test_place_details_maps_every_field() -> None:
    """`place_details` maps every field."""
    route = respx.get(f"{places.PLACES_BASE_URL}/places/ChIJ-own").mock(
        return_value=httpx.Response(200, json=FULL_PLACE)
    )

    snapshot = await places.place_details("ChIJ-own", KEY)

    assert snapshot.place_id == "ChIJ-own"
    assert snapshot.name == "Dachdeckerei Hollenbeck GmbH"
    assert snapshot.phone == "0251 123456"
    assert str(snapshot.website) == "https://www.dachdecker-hollenbeck.de/"
    assert snapshot.primary_type == "roofing_contractor"
    assert snapshot.rating == 4.8
    assert snapshot.review_count == 127
    assert snapshot.has_opening_hours is True
    assert snapshot.photo_count == 2
    assert snapshot.business_status == "OPERATIONAL"
    assert str(snapshot.google_maps_uri) == "https://maps.google.com/?cid=42"
    assert snapshot.recent_reviews == [
        {
            "publishTime": "2026-09-01T10:00:00Z",
            "rating": 5.0,
            "text": "Schnell und sauber.",
            "relativePublishTimeDescription": "vor einem Monat",
        }
    ]
    assert route.calls[0].request.headers["X-Goog-FieldMask"] == places.DETAIL_FIELD_MASK
    assert "googleMapsUri" in places.DETAIL_FIELD_MASK.split(",")


@respx.mock
async def test_place_details_tolerates_sparse_profile() -> None:
    """`place_details` tolerates sparse profile."""
    respx.get(f"{places.PLACES_BASE_URL}/places/ChIJ-x").mock(
        return_value=httpx.Response(200, json={"id": "ChIJ-x", "websiteUri": "kein link"})
    )

    snapshot = await places.place_details("ChIJ-x", KEY)

    assert snapshot.name == ""
    assert snapshot.website is None
    assert snapshot.review_count == 0
    assert snapshot.has_opening_hours is False
    assert snapshot.recent_reviews == []
    assert snapshot.google_maps_uri is None


@respx.mock
async def test_place_details_raises_on_unknown_place() -> None:
    """`place_details` raises on unknown place."""
    respx.get(f"{places.PLACES_BASE_URL}/places/missing").mock(
        return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
    )

    with pytest.raises(CollectionError, match="404"):
        await places.place_details("missing", KEY)


@respx.mock
async def test_place_details_raises_on_malformed_answer() -> None:
    """`place_details` raises on malformed answer."""
    respx.get(f"{places.PLACES_BASE_URL}/places/x").mock(
        return_value=httpx.Response(200, json={"displayName": {"text": "ohne id"}})
    )

    with pytest.raises(CollectionError, match="malformed"):
        await places.place_details("x", KEY)


@respx.mock
async def test_top_competitors_sends_location_bias_and_keeps_order() -> None:
    """`top_competitors` sends location bias and keeps order."""
    route = respx.post(places.SEARCH_TEXT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "places": [
                    _place("ChIJ-1", "Bedachungen Overmann"),
                    _place("ChIJ-2", "Dach Service Kemper"),
                    _place("ChIJ-3", "Zimmerei Lüttke"),
                ]
            },
        )
    )

    result = await places.top_competitors("Dachdecker Münster", 51.96, 7.63, KEY, limit=2)

    assert [place.name for place in result] == ["Bedachungen Overmann", "Dach Service Kemper"]
    body = json.loads(route.calls[0].request.content)
    assert body["pageSize"] == 2
    assert body["locationBias"]["circle"] == {
        "center": {"latitude": 51.96, "longitude": 7.63},
        "radius": 10000.0,
    }
    assert route.calls[0].request.headers["X-Goog-FieldMask"].startswith("places.id,")


async def test_top_competitors_rejects_invalid_limit() -> None:
    """`top_competitors` rejects invalid limit."""
    with pytest.raises(ValueError, match="limit"):
        await places.top_competitors("Dachdecker Münster", 51.96, 7.63, KEY, limit=0)


@respx.mock
async def test_rate_limit_is_retried_then_transient() -> None:
    """Rate limit is retried then transient."""
    route = respx.post(places.SEARCH_TEXT_URL).mock(return_value=httpx.Response(429))

    with pytest.raises(TransientCollectionError):
        await places.find_place("x", "y", KEY)

    assert route.call_count == 3


@respx.mock
async def test_forbidden_key_is_permanent() -> None:
    """Forbidden key is permanent."""
    route = respx.post(places.SEARCH_TEXT_URL).mock(return_value=httpx.Response(403))

    with pytest.raises(CollectionError) as raised:
        await places.find_place("x", "y", KEY)

    assert not isinstance(raised.value, TransientCollectionError)
    assert route.call_count == 1


@respx.mock
async def test_ranked_place_ids_asks_for_identifiers_only() -> None:
    """The grid fallback requests only place ids, biased to the grid point."""
    route = respx.post(places.SEARCH_TEXT_URL).mock(
        return_value=httpx.Response(200, json={"places": [{"id": "ChIJ-a"}, {"id": "ChIJ-own"}]})
    )

    ranked = await places.ranked_place_ids("Dachdecker Münster", 51.9, 7.6, KEY, 1000.0)

    assert ranked == ["ChIJ-a", "ChIJ-own"]
    request = route.calls[0].request
    assert request.headers["X-Goog-FieldMask"] == "places.id"
    body = json.loads(request.content)
    assert body["pageSize"] == places.MAX_PAGE_SIZE
    assert body["locationBias"]["circle"] == {
        "center": {"latitude": 51.9, "longitude": 7.6},
        "radius": 1000.0,
    }


@respx.mock
async def test_place_details_marks_every_field_as_read_by_the_places_api() -> None:
    respx.get(f"{places.PLACES_BASE_URL}/places/ChIJ-own").mock(
        return_value=httpx.Response(200, json=FULL_PLACE)
    )

    snapshot = await places.place_details("ChIJ-own", KEY)

    assert set(snapshot.value_routes.values()) == {"places_api"}
    assert snapshot.route_of("types") == "places_api"
    assert snapshot.route_of("description") is None


@respx.mock
async def test_listed_businesses_reads_names_and_review_counts() -> None:
    """The review comparison asks only for id, name, rating and review count."""
    payload = {
        "places": [
            {
                "id": "ChIJ-a",
                "displayName": {"text": "Bedachungen Overmann"},
                "rating": 4.8,
                "userRatingCount": 154,
            },
            {"id": "ChIJ-b", "displayName": {"text": "Dach Neu"}},
        ]
    }
    route = respx.post(places.SEARCH_TEXT_URL).mock(return_value=httpx.Response(200, json=payload))

    listed = await places.listed_businesses("Dachdecker Münster", 51.9, 7.6, KEY, 10000.0)

    assert listed == [
        places.ListedPlace("ChIJ-a", "Bedachungen Overmann", 154, 4.8),
        places.ListedPlace("ChIJ-b", "Dach Neu", 0, None),
    ]
    request = route.calls[0].request
    assert request.headers["X-Goog-FieldMask"] == (
        "places.id,places.displayName,places.rating,places.userRatingCount"
    )
    body = json.loads(request.content)
    assert (body["textQuery"], body["pageSize"]) == ("Dachdecker Münster", places.MAX_PAGE_SIZE)
    assert body["locationBias"]["circle"]["radius"] == 10000.0


@respx.mock
async def test_search_businesses_reads_category_and_position() -> None:
    """The identify search asks for few fields and keeps the German category and the position."""
    found = _place(
        "ChIJ-own",
        "Dachdeckerei Hollenbeck GmbH",
        websiteUri="https://www.dachdecker-hollenbeck.de/",
        primaryType="roofing_contractor",
        primaryTypeDisplayName={"text": "Dachdecker", "languageCode": "de"},
        types=["roofing_contractor", "point_of_interest"],
        location={"latitude": 51.92, "longitude": 7.63},
    )
    route = respx.post(places.SEARCH_TEXT_URL).mock(
        return_value=httpx.Response(200, json={"places": [found, _place("ChIJ-b", "Andere")]})
    )

    candidates = await places.search_businesses(" dachdecker-hollenbeck.de ", KEY)

    request = route.calls[0].request
    assert request.headers["X-Goog-FieldMask"] == places.IDENTIFY_FIELD_MASK
    assert "places.primaryTypeDisplayName" in places.IDENTIFY_FIELD_MASK.split(",")
    assert json.loads(request.content)["textQuery"] == "dachdecker-hollenbeck.de"
    own = candidates[0]
    assert (own.place_id, own.category, own.primary_type) == (
        "ChIJ-own",
        "Dachdecker",
        "roofing_contractor",
    )
    assert own.types == ("roofing_contractor", "point_of_interest")
    assert (own.latitude, own.longitude) == (51.92, 7.63)
    assert candidates[1].category is None and candidates[1].latitude is None


@respx.mock
async def test_search_businesses_without_results() -> None:
    """No place found gives an empty list."""
    respx.post(places.SEARCH_TEXT_URL).mock(return_value=httpx.Response(200, json={}))

    assert await places.search_businesses("unbekannt.de", KEY) == []


@respx.mock
async def test_search_prospects_reads_profile_gaps_and_the_next_page() -> None:
    """One page of a customer search with the fields that show a neglected profile."""
    bare = _place("ChIJ-bare", "Dachdecker Ohne", businessStatus="OPERATIONAL")
    route = respx.post(places.SEARCH_TEXT_URL).mock(
        return_value=httpx.Response(
            200, json={"places": [FULL_PLACE, bare], "nextPageToken": "seite-2"}
        )
    )

    page = await places.search_prospects("Dachdecker in Münster", KEY)

    request = route.calls[0].request
    mask = request.headers["X-Goog-FieldMask"].split(",")
    assert "nextPageToken" in mask
    assert "places.reviews" not in mask
    assert {"places.websiteUri", "places.regularOpeningHours", "places.photos"} <= set(mask)
    body = json.loads(request.content)
    assert body["textQuery"] == "Dachdecker in Münster"
    assert body["pageSize"] == places.MAX_PAGE_SIZE
    assert "pageToken" not in body
    assert page.next_page_token == "seite-2"
    full, empty = page.places
    assert (full.website, full.photo_count, full.has_opening_hours) == (
        "https://www.dachdecker-hollenbeck.de/",
        2,
        True,
    )
    assert (full.review_count, full.rating, full.phone) == (127, 4.8, "0251 123456")
    assert full.maps_url == "https://maps.google.com/?cid=42"
    assert (empty.website, empty.phone, empty.photo_count, empty.review_count) == (
        None,
        None,
        0,
        0,
    )
    assert empty.has_opening_hours is False
    assert empty.business_status == "OPERATIONAL"


@respx.mock
async def test_search_prospects_sends_the_page_token() -> None:
    """A later page repeats the query with the token Google returned."""
    route = respx.post(places.SEARCH_TEXT_URL).mock(return_value=httpx.Response(200, json={}))

    page = await places.search_prospects("Dachdecker in Münster", KEY, page_token="seite-2")

    assert json.loads(route.calls[0].request.content)["pageToken"] == "seite-2"
    assert page.places == [] and page.next_page_token is None


def test_every_request_has_the_price_of_its_tier_from_the_google_price_list() -> None:
    """USD per 1,000 requests, first volume tier, price list updated 2026-10-28."""
    per_thousand = {
        places.PlacesSku.TEXT_SEARCH_IDS_ONLY: "0",
        places.PlacesSku.TEXT_SEARCH_ENTERPRISE: "35",
        places.PlacesSku.TEXT_SEARCH_ENTERPRISE_ATMOSPHERE: "40",
        places.PlacesSku.PLACE_DETAILS_ENTERPRISE_ATMOSPHERE: "25",
    }
    for sku, price in per_thousand.items():
        assert places.request_cost_eur(sku, Decimal(1)) * 1000 == Decimal(price)
    assert places.request_cost_eur(places.PlacesSku.TEXT_SEARCH_ENTERPRISE, Decimal("0.9")) == (
        Decimal("0.0315")
    )


def test_the_tier_of_each_request_follows_its_field_mask() -> None:
    """Reviews make a request Enterprise + Atmosphere, phone or website Enterprise, ids free."""
    assert "places.reviews" not in places.PROSPECT_FIELD_MASK
    assert "places.websiteUri" in places.PROSPECT_FIELD_MASK
    assert places.SEARCH_PROSPECTS_SKU is places.PlacesSku.TEXT_SEARCH_ENTERPRISE
    assert "places.reviews" in places.SEARCH_FIELD_MASK
    assert places.TOP_COMPETITORS_SKU is places.PlacesSku.TEXT_SEARCH_ENTERPRISE_ATMOSPHERE
    assert "reviews" in places.DETAIL_FIELD_MASK.split(",")
    assert places.PLACE_DETAILS_SKU is places.PlacesSku.PLACE_DETAILS_ENTERPRISE_ATMOSPHERE
    assert "places.nationalPhoneNumber" in places.IDENTIFY_FIELD_MASK
    assert places.SEARCH_BUSINESSES_SKU is places.PlacesSku.TEXT_SEARCH_ENTERPRISE
    assert "places.userRatingCount" in places.LISTING_FIELD_MASK
    assert places.LISTED_BUSINESSES_SKU is places.PlacesSku.TEXT_SEARCH_ENTERPRISE
    assert places.RANKED_PLACE_IDS_SKU is places.PlacesSku.TEXT_SEARCH_IDS_ONLY
    assert places.FIND_PLACE_SKU is places.PlacesSku.TEXT_SEARCH_IDS_ONLY
