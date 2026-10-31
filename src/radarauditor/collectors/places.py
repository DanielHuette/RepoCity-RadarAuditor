"""Read public Google Business Profile data through the Places API (New).

Context: Paid fallback when Google Maps blocks the server browser; works for any business without
owner access. The API returns at most five reviews and never the owner's replies, so reply rate
and reply time cannot be measured from this source; the review checks report that limitation
instead of a value. The map grid uses Text Search with the identifier-only field mask, the
cheapest request.
Input: Business name and address, place_id, or a phrase and a point.
The customer search (search_prospects) asks one page of a Text Search with exactly the fields that
show a neglected profile; reviews are not requested, so the request stays in the Enterprise SKU.
Output: PlaceSnapshot with every field marked as read by the Places API, ranked place ids, or one
page of a customer search.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError
from pydantic.alias_generators import to_camel

from radarauditor.collectors.patterns import parse_http_url
from radarauditor.collectors.snapshot import PLACES_API_FIELDS, PlaceSnapshot
from radarauditor.collectors.transport import send
from radarauditor.errors import CollectionError

PLACES_BASE_URL = "https://places.googleapis.com/v1"
SEARCH_TEXT_URL = f"{PLACES_BASE_URL}/places:searchText"
REQUEST_TIMEOUT_SECONDS = 20
LANGUAGE_CODE = "de"
REGION_CODE = "DE"
COMPETITOR_RADIUS_METERS = 10000.0
MAX_PAGE_SIZE = 20
DETAIL_FIELDS = (
    "id",
    "displayName",
    "formattedAddress",
    "nationalPhoneNumber",
    "websiteUri",
    "primaryType",
    "types",
    "rating",
    "userRatingCount",
    "regularOpeningHours",
    "photos",
    "reviews",
    "businessStatus",
    "googleMapsUri",
)
DETAIL_FIELD_MASK = ",".join(DETAIL_FIELDS)
IDENTIFY_FIELDS = (
    "id",
    "displayName",
    "formattedAddress",
    "nationalPhoneNumber",
    "websiteUri",
    "primaryType",
    "primaryTypeDisplayName",
    "types",
    "location",
)
IDENTIFY_FIELD_MASK = ",".join(f"places.{name}" for name in IDENTIFY_FIELDS)
IDENTIFY_PAGE_SIZE = 5
SEARCH_FIELD_MASK = ",".join(f"places.{name}" for name in DETAIL_FIELDS)
LISTING_FIELD_MASK = ",".join(
    f"places.{name}" for name in ("id", "displayName", "rating", "userRatingCount")
)
PROSPECT_FIELDS = (
    "id",
    "displayName",
    "formattedAddress",
    "nationalPhoneNumber",
    "websiteUri",
    "rating",
    "userRatingCount",
    "regularOpeningHours",
    "photos",
    "businessStatus",
    "googleMapsUri",
)
PROSPECT_FIELD_MASK = ",".join([*(f"places.{name}" for name in PROSPECT_FIELDS), "nextPageToken"])


class PlacesSku(StrEnum):
    """Billing tiers (SKUs) of the requests this module sends; the field mask decides the tier."""

    TEXT_SEARCH_IDS_ONLY = "text_search_essentials_ids_only"
    TEXT_SEARCH_ENTERPRISE = "text_search_enterprise"
    TEXT_SEARCH_ENTERPRISE_ATMOSPHERE = "text_search_enterprise_atmosphere"
    PLACE_DETAILS_ENTERPRISE_ATMOSPHERE = "place_details_enterprise_atmosphere"


# USD per 1,000 requests, first volume tier, from the Google Maps Platform core services price
# list (developers.google.com/maps/billing-and-pricing/pricing, updated 2026-10-28). The free
# monthly calls of each tier are not subtracted, so the booked spend is never below the bill.
PRICE_USD_PER_THOUSAND: dict[PlacesSku, Decimal] = {
    PlacesSku.TEXT_SEARCH_IDS_ONLY: Decimal(0),
    PlacesSku.TEXT_SEARCH_ENTERPRISE: Decimal(35),
    PlacesSku.TEXT_SEARCH_ENTERPRISE_ATMOSPHERE: Decimal(40),
    PlacesSku.PLACE_DETAILS_ENTERPRISE_ATMOSPHERE: Decimal(25),
}
# Tier of each request: reviews make it Enterprise + Atmosphere, phone, website, rating or opening
# hours Enterprise, the place id alone is free.
FIND_PLACE_SKU = PlacesSku.TEXT_SEARCH_IDS_ONLY
SEARCH_BUSINESSES_SKU = PlacesSku.TEXT_SEARCH_ENTERPRISE
PLACE_DETAILS_SKU = PlacesSku.PLACE_DETAILS_ENTERPRISE_ATMOSPHERE
TOP_COMPETITORS_SKU = PlacesSku.TEXT_SEARCH_ENTERPRISE_ATMOSPHERE
RANKED_PLACE_IDS_SKU = PlacesSku.TEXT_SEARCH_IDS_ONLY
LISTED_BUSINESSES_SKU = PlacesSku.TEXT_SEARCH_ENTERPRISE
SEARCH_PROSPECTS_SKU = PlacesSku.TEXT_SEARCH_ENTERPRISE


def request_cost_eur(sku: PlacesSku, eur_per_usd: Decimal) -> Decimal:
    """Price of one request of a tier in euros.

    Args:
        sku: Billing tier of the request.
        eur_per_usd: Euros per US dollar.

    Returns:
        The list price of one request converted to euros.
    """
    return PRICE_USD_PER_THOUSAND[sku] / 1000 * eur_per_usd


class _ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class _LocalizedText(_ApiModel):
    text: str = ""


class _Review(_ApiModel):
    rating: float | None = None
    text: _LocalizedText | None = None
    publish_time: str | None = None
    relative_publish_time_description: str | None = None


class _LatLng(_ApiModel):
    latitude: float
    longitude: float


class _Place(_ApiModel):
    id: str
    location: _LatLng | None = None
    display_name: _LocalizedText | None = None
    primary_type_display_name: _LocalizedText | None = None
    formatted_address: str = ""
    national_phone_number: str | None = None
    website_uri: str | None = None
    primary_type: str | None = None
    types: list[str] = []
    rating: float | None = None
    user_rating_count: int = 0
    regular_opening_hours: dict[str, Any] | None = None
    photos: list[dict[str, Any]] = []
    reviews: list[_Review] = []
    business_status: str | None = None
    google_maps_uri: str | None = None


@dataclass(frozen=True)
class BusinessCandidate:
    """A business Text Search found for a name or website, before it is accepted as the customer.

    category is the German text of the primary category, for example "Dachdecker"; latitude and
    longitude are the map position of the profile.
    """

    place_id: str
    name: str
    formatted_address: str
    phone: str | None
    website: str | None
    primary_type: str | None
    category: str | None
    types: tuple[str, ...]
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class ListedPlace:
    """One business of a Text Search result list, with what the review comparison needs."""

    place_id: str
    name: str
    review_count: int
    rating: float | None


@dataclass(frozen=True)
class ProspectPlace:
    """One business of a customer search with the facts that show a neglected profile.

    photo_count is capped at 10 by Google; has_opening_hours is False when the profile has none.
    """

    place_id: str
    name: str
    formatted_address: str
    phone: str | None
    website: str | None
    maps_url: str | None
    rating: float | None
    review_count: int
    photo_count: int
    has_opening_hours: bool
    business_status: str | None


@dataclass(frozen=True)
class ProspectPage:
    """One page of a customer search; next_page_token is None on the last page."""

    places: list[ProspectPlace]
    next_page_token: str | None


class _PlaceId(_ApiModel):
    id: str


class _IdSearchResponse(_ApiModel):
    places: list[_PlaceId] = []


class _SearchResponse(_ApiModel):
    places: list[_Place] = []


class _ProspectResponse(_SearchResponse):
    next_page_token: str | None = None


async def find_place(name: str, address: str, api_key: SecretStr) -> str | None:
    """Resolve a business to a place_id with Text Search; None if not found.

    Args:
        name: Business name as written on the website or profile.
        address: Address or city that narrows the search.
        api_key: Google Cloud key with the Places API (New) enabled.

    Returns:
        The place_id of the best match, or None.

    Raises:
        TransientCollectionError: On rate limits or 5xx responses.
        CollectionError: On an invalid key or a malformed answer.
    """
    body = {
        "textQuery": f"{name} {address}".strip(),
        "languageCode": LANGUAGE_CODE,
        "regionCode": REGION_CODE,
    }
    payload = await _request(
        "POST", SEARCH_TEXT_URL, api_key, "places.id", source="Places text search", json=body
    )
    result = _validate(_IdSearchResponse, payload, "Places text search")
    return result.places[0].id if result.places else None


async def search_businesses(query: str, api_key: SecretStr) -> list[BusinessCandidate]:
    """Find businesses for a name with city, or for a website host, to identify a customer.

    Args:
        query: For example "Dachdeckerei Hollenbeck Münster" or "dachdecker-hollenbeck.de".
        api_key: Google Cloud key with the Places API (New) enabled.

    Returns:
        Up to IDENTIFY_PAGE_SIZE candidates in Google's order.

    Raises:
        TransientCollectionError: On rate limits or 5xx responses.
        CollectionError: On an invalid key or a malformed answer.
    """
    body = {
        "textQuery": query.strip(),
        "languageCode": LANGUAGE_CODE,
        "regionCode": REGION_CODE,
        "pageSize": IDENTIFY_PAGE_SIZE,
    }
    payload = await _request(
        "POST", SEARCH_TEXT_URL, api_key, IDENTIFY_FIELD_MASK, source="Places identify", json=body
    )
    result = _validate(_SearchResponse, payload, "Places identify")
    return [_candidate(place) for place in result.places[:IDENTIFY_PAGE_SIZE]]


def _candidate(place: _Place) -> BusinessCandidate:
    category = place.primary_type_display_name
    return BusinessCandidate(
        place_id=place.id,
        name=place.display_name.text if place.display_name else "",
        formatted_address=place.formatted_address,
        phone=place.national_phone_number,
        website=place.website_uri,
        primary_type=place.primary_type,
        category=category.text if category is not None and category.text else None,
        types=tuple(place.types),
        latitude=place.location.latitude if place.location else None,
        longitude=place.location.longitude if place.location else None,
    )


async def place_details(place_id: str, api_key: SecretStr) -> PlaceSnapshot:
    """Fetch the public details of one place with a fixed field mask.

    Args:
        place_id: Google place identifier.
        api_key: Google Cloud key with the Places API (New) enabled.

    Returns:
        Public profile facts.

    Raises:
        TransientCollectionError: On rate limits or 5xx responses.
        CollectionError: On an unknown place, an invalid key or a malformed answer.
    """
    url = f"{PLACES_BASE_URL}/places/{quote(place_id, safe='')}"
    payload = await _request(
        "GET",
        url,
        api_key,
        DETAIL_FIELD_MASK,
        source="Places details",
        params={"languageCode": LANGUAGE_CODE, "regionCode": REGION_CODE},
    )
    return _to_snapshot(_validate(_Place, payload, "Places details"))


async def top_competitors(
    phrase: str, latitude: float, longitude: float, api_key: SecretStr, limit: int = 3
) -> list[PlaceSnapshot]:
    """Return the best-ranked businesses for a phrase near a point, as Google lists them.

    Args:
        phrase: Search phrase, for example "Dachdecker Münster".
        latitude: Centre of the search area.
        longitude: Centre of the search area.
        api_key: Google Cloud key with the Places API (New) enabled.
        limit: Number of places to return, 1 to 20.

    Returns:
        Up to `limit` places in Google's order.

    Raises:
        ValueError: If limit is outside 1..20.
        TransientCollectionError: On rate limits or 5xx responses.
        CollectionError: On an invalid key or a malformed answer.
    """
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}, got {limit}")
    body = {
        "textQuery": phrase,
        "languageCode": LANGUAGE_CODE,
        "regionCode": REGION_CODE,
        "pageSize": limit,
        "locationBias": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": COMPETITOR_RADIUS_METERS,
            }
        },
    }
    payload = await _request(
        "POST", SEARCH_TEXT_URL, api_key, SEARCH_FIELD_MASK, source="Places nearby", json=body
    )
    result = _validate(_SearchResponse, payload, "Places nearby")
    return [_to_snapshot(place) for place in result.places[:limit]]


async def ranked_place_ids(
    phrase: str, latitude: float, longitude: float, api_key: SecretStr, radius_meters: float
) -> list[str]:
    """Return the ids of the places Google ranks for a phrase around a point, best first.

    Only place ids are requested (field mask places.id), which Google bills as the free
    identifier-only Text Search.

    Args:
        phrase: Search phrase, for example "Dachdecker Münster".
        latitude: Point the search is biased to.
        longitude: Point the search is biased to.
        api_key: Google Cloud key with the Places API (New) enabled.
        radius_meters: Radius of the location bias around the point.

    Returns:
        Up to MAX_PAGE_SIZE place ids in Google's order.

    Raises:
        TransientCollectionError: On rate limits or 5xx responses.
        CollectionError: On an invalid key or a malformed answer.
    """
    body = {
        "textQuery": phrase,
        "languageCode": LANGUAGE_CODE,
        "regionCode": REGION_CODE,
        "pageSize": MAX_PAGE_SIZE,
        "locationBias": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": radius_meters,
            }
        },
    }
    payload = await _request(
        "POST", SEARCH_TEXT_URL, api_key, "places.id", source="Places ranking", json=body
    )
    return [place.id for place in _validate(_IdSearchResponse, payload, "Places ranking").places]


async def listed_businesses(
    phrase: str, latitude: float, longitude: float, api_key: SecretStr, radius_meters: float
) -> list[ListedPlace]:
    """Return the businesses Google lists for a phrase around a point with their review counts.

    Only id, name, rating and review count are requested, the fields the review comparison needs.

    Args:
        phrase: Search phrase, for example "Dachdecker Münster".
        latitude: Point the search is biased to.
        longitude: Point the search is biased to.
        api_key: Google Cloud key with the Places API (New) enabled.
        radius_meters: Radius of the location bias around the point.

    Returns:
        Up to MAX_PAGE_SIZE listed places in Google's order.

    Raises:
        TransientCollectionError: On rate limits or 5xx responses.
        CollectionError: On an invalid key or a malformed answer.
    """
    body = {
        "textQuery": phrase,
        "languageCode": LANGUAGE_CODE,
        "regionCode": REGION_CODE,
        "pageSize": MAX_PAGE_SIZE,
        "locationBias": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": radius_meters,
            }
        },
    }
    payload = await _request(
        "POST", SEARCH_TEXT_URL, api_key, LISTING_FIELD_MASK, source="Places listing", json=body
    )
    result = _validate(_SearchResponse, payload, "Places listing")
    return [
        ListedPlace(
            place_id=place.id,
            name=place.display_name.text if place.display_name else "",
            review_count=place.user_rating_count,
            rating=place.rating,
        )
        for place in result.places
    ]


async def search_prospects(
    phrase: str, api_key: SecretStr, page_token: str | None = None
) -> ProspectPage:
    """Return one page of the businesses Google lists for a trade in a city.

    Args:
        phrase: For example "Dachdecker in Münster".
        api_key: Google Cloud key with the Places API (New) enabled.
        page_token: Token of the previous page; None for the first page.

    Returns:
        Up to MAX_PAGE_SIZE businesses in Google's order and the token of the next page.

    Raises:
        TransientCollectionError: On rate limits or 5xx responses.
        CollectionError: On an invalid key or a malformed answer.
    """
    body: dict[str, object] = {
        "textQuery": phrase.strip(),
        "languageCode": LANGUAGE_CODE,
        "regionCode": REGION_CODE,
        "pageSize": MAX_PAGE_SIZE,
    }
    if page_token is not None:
        body["pageToken"] = page_token
    payload = await _request(
        "POST", SEARCH_TEXT_URL, api_key, PROSPECT_FIELD_MASK, source="Places prospects", json=body
    )
    result = _validate(_ProspectResponse, payload, "Places prospects")
    return ProspectPage(
        places=[_prospect(place) for place in result.places],
        next_page_token=result.next_page_token or None,
    )


def _prospect(place: _Place) -> ProspectPlace:
    return ProspectPlace(
        place_id=place.id,
        name=place.display_name.text if place.display_name else "",
        formatted_address=place.formatted_address,
        phone=place.national_phone_number or None,
        website=place.website_uri or None,
        maps_url=place.google_maps_uri or None,
        rating=place.rating,
        review_count=place.user_rating_count,
        photo_count=len(place.photos),
        has_opening_hours=bool(place.regular_opening_hours),
        business_status=place.business_status,
    )


async def _request(
    method: str,
    url: str,
    api_key: SecretStr,
    field_mask: str,
    *,
    source: str,
    **request_arguments: Any,
) -> object:
    headers = {
        "X-Goog-Api-Key": api_key.get_secret_value(),
        "X-Goog-FieldMask": field_mask,
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await send(
            client, method, url, source=source, headers=headers, **request_arguments
        )
    try:
        payload: object = response.json()
    except ValueError as error:
        raise CollectionError(f"{source} answer is not JSON") from error
    return payload


def _validate[ModelT: BaseModel](model: type[ModelT], payload: object, source: str) -> ModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise CollectionError(f"{source} answer is malformed") from error


def _to_snapshot(place: _Place) -> PlaceSnapshot:
    return PlaceSnapshot(
        place_id=place.id,
        name=place.display_name.text if place.display_name else "",
        formatted_address=place.formatted_address,
        phone=place.national_phone_number,
        website=parse_http_url(place.website_uri) if place.website_uri else None,
        primary_type=place.primary_type,
        types=place.types,
        rating=place.rating,
        review_count=place.user_rating_count,
        has_opening_hours=bool(place.regular_opening_hours),
        photo_count=len(place.photos),
        recent_reviews=[_review(review) for review in place.reviews],
        business_status=place.business_status,
        google_maps_uri=parse_http_url(place.google_maps_uri) if place.google_maps_uri else None,
        value_routes=dict.fromkeys(sorted(PLACES_API_FIELDS), "places_api"),
    )


def _review(review: _Review) -> dict[str, object]:
    """Keep the review under the API's own key names, which the review checks read."""
    return {
        "publishTime": review.publish_time,
        "rating": review.rating,
        "text": review.text.text if review.text else "",
        "relativePublishTimeDescription": review.relative_publish_time_description,
    }
