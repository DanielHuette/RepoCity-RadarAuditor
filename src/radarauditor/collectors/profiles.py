"""Choose the route for Google profile data: Google Maps in the browser first, Places API second.

Context: Source order of the spec. Every profile, competitor search, map position and grid point
is read in the shared browser while Google Maps lets it; once Maps shows a bot check, or the
google_maps switch or cap stops the browser, the same data comes from the Places API (New) at
once, within the places cap. A page the browser cannot read falls back for that one value.
Input: The shared BrowserService, the job's SourceGate and the Google API key.
Output: PlaceSnapshots, competitor snapshots, MapRankings, the MapResultList of a phrase and
GridPoints, each marked with the route that delivered it.
"""

import enum
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from playwright.async_api import Error as PlaywrightError
from pydantic import SecretStr

from radarauditor.collectors import maps, places
from radarauditor.collectors.browser import BrowserService, Geolocation
from radarauditor.collectors.maps_pages import MapListing, listing_position, same_business
from radarauditor.collectors.paid_rankings import grid_key, grid_points
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import (
    DataRoute,
    GridPoint,
    ListedBusiness,
    MapRanking,
    MapResultList,
    PlaceSnapshot,
)
from radarauditor.db.app_settings import Source
from radarauditor.errors import CollectionError, ProfileNotFoundError, SearchBlockedError

logger = logging.getLogger(__name__)

RANKING_RADIUS_METERS = 10000.0
GRID_RADIUS_METERS = 1000.0
PLACES_ID_PREFIX = "ChIJ"
SEARCH_SPARE = 2


class _Fallback(enum.Enum):
    USE_PLACES = enum.auto()


USE_PLACES = _Fallback.USE_PLACES


@dataclass(frozen=True)
class Business:
    """The customer as the routes recognise it in result lists.

    address is the full address for a Places lookup, city the place competitors are looked up in.
    """

    name: str
    address: str
    city: str
    place_ids: frozenset[str] = frozenset()


class ProfileRoute:
    """Reads Google profile data for one job, browser first, Places API on a block."""

    def __init__(self, browser: BrowserService, gate: SourceGate, api_key: SecretStr) -> None:
        """Bind the route to the job's browser, gate and Places key.

        Args:
            browser: Shared browser of the job.
            gate: Source switches and caps of the job.
            api_key: Google Cloud key for the Places API (New).
        """
        self.browser = browser
        self.gate = gate
        self.api_key = api_key
        self._blocked: str | None = None
        self._browser_used = False
        self._listings: dict[tuple[str, float, float], list[MapListing]] = {}
        self._places_ids: dict[str, str | None] = {}

    async def profile(self, target: maps.ProfileTarget, notes: list[str]) -> PlaceSnapshot | None:
        """Read one business profile.

        Args:
            target: Business to read.
            notes: Receives a line when the browser had to give way to the Places API.

        Returns:
            The profile, or None when neither route finds one.

        Raises:
            SourceUnavailableError: If the Places API is needed and switched off or capped.
            CollectionError: If the Places API fails.
        """
        try:
            snapshot = await self._in_browser(
                lambda: maps.read_profile(self.browser, target), notes, target.name
            )
        except ProfileNotFoundError:
            return None
        if snapshot is not USE_PLACES:
            return snapshot
        return await self._places_profile(target)

    async def competitors(
        self,
        phrase: str,
        centre: tuple[float, float],
        exclude: list[PlaceSnapshot],
        own: Business,
        count: int,
        notes: list[str],
    ) -> list[PlaceSnapshot]:
        """Return the strongest businesses for a phrase near a point, skipping known ones.

        Args:
            phrase: Main keyword with the city.
            centre: Latitude and longitude of the business.
            exclude: Profiles already compared.
            own: The customer, which is never its own competitor.
            count: Number of competitors wanted.
            notes: Receives route changes and profiles that could not be read.

        Returns:
            Up to count profiles in Google's order.

        Raises:
            SourceUnavailableError: If the Places API is needed and switched off or capped.
            CollectionError: If the Places API fails.
        """
        listings = await self._search(phrase, centre, notes)
        if listings is USE_PLACES:
            return await self._places_competitors(phrase, centre, exclude, own, count)
        chosen = [item for item in listings if not _is_known(item, exclude, own)][:count]
        found: list[PlaceSnapshot] = []
        for listing in chosen:
            target = maps.ProfileTarget(
                listing.name, own.city, place_id=listing.place_id, url=listing.url
            )
            snapshot = await self.profile(target, notes)
            if snapshot is None:
                notes.append(f"{listing.name}: profile not readable")
                continue
            found.append(snapshot)
        return found

    async def map_ranking(
        self, phrase: str, centre: tuple[float, float], own: Business, notes: list[str]
    ) -> MapRanking:
        """Return the customer's position in the Google Maps results for one phrase.

        Raises:
            SourceUnavailableError: If the Places API is needed and switched off or capped.
            CollectionError: If the Places API fails or does not know the customer.
        """
        listings = await self._search(phrase, centre, notes)
        if listings is not USE_PLACES:
            return MapRanking(
                phrase=phrase,
                position=listing_position(listings, own.name, own.place_ids),
                listed=[listing.name for listing in listings],
                route="browser",
            )
        position = await self._places_position(phrase, centre, own, RANKING_RADIUS_METERS)
        return MapRanking(phrase=phrase, position=position, route="places_api")

    async def result_list(
        self, phrase: str, centre: tuple[float, float], own: Business, notes: list[str]
    ) -> MapResultList:
        """Return every business Google Maps lists for a phrase, with its review count.

        The browser search is the one the competitors and the map position already ran, so no
        further page is opened; when Maps blocks, the Places text search lists the businesses.

        Args:
            phrase: Main keyword with the city.
            centre: Latitude and longitude of the business.
            own: The customer, marked in the list where it appears.
            notes: Receives route changes.

        Returns:
            The listed businesses in Google's order.

        Raises:
            SourceUnavailableError: If the Places API is needed and switched off or capped.
            CollectionError: If the Places API fails.
        """
        listings = await self._search(phrase, centre, notes)
        if listings is not USE_PLACES:
            return _result_list(phrase, listings, own, route="browser")
        self.gate.acquire(Source.PLACES)
        found = await places.listed_businesses(phrase, *centre, self.api_key, RANKING_RADIUS_METERS)
        listed = [
            MapListing(item.name, "", item.place_id, item.rating, item.review_count)
            for item in found
        ]
        return _result_list(phrase, listed, own, route="places_api")

    async def grid(
        self, phrase: str, centre: tuple[float, float], own: Business, size: int, notes: list[str]
    ) -> list[GridPoint]:
        """Measure the customer's map position at size x size points one kilometre apart.

        Each point is searched with the map centred there and the device location set to it.

        Returns:
            Points ordered row by row from the north-west, keyed "r{row}c{column}".

        Raises:
            SourceUnavailableError: If the Places API is needed and switched off or capped.
            CollectionError: If the Places API fails or does not know the customer.
        """
        points: list[GridPoint] = []
        for index, (latitude, longitude) in enumerate(grid_points(*centre, size)):
            position, route = await self._point(phrase, latitude, longitude, own, notes)
            key = grid_key(index, size)
            points.append(
                GridPoint(
                    key=key, latitude=latitude, longitude=longitude, position=position, route=route
                )
            )
        return sorted(points, key=lambda point: _row_and_column(point.key))

    async def _point(
        self, phrase: str, latitude: float, longitude: float, own: Business, notes: list[str]
    ) -> tuple[int | None, DataRoute]:
        position = Geolocation(latitude, longitude)
        listings = await self._in_browser(
            lambda: maps.search_listings(self.browser, phrase, position), notes, phrase
        )
        if listings is not USE_PLACES:
            return listing_position(listings, own.name, own.place_ids), "browser"
        found = await self._places_position(phrase, (latitude, longitude), own, GRID_RADIUS_METERS)
        return found, "places_api"

    async def _search(
        self, phrase: str, centre: tuple[float, float], notes: list[str]
    ) -> list[MapListing] | _Fallback:
        """Result list at the business, searched once per phrase in the job."""
        key = (phrase, *centre)
        if key in self._listings:
            return self._listings[key]
        listings = await self._in_browser(
            lambda: maps.search_listings(self.browser, phrase, Geolocation(*centre)), notes, phrase
        )
        if listings is not USE_PLACES:
            self._listings[key] = listings
        return listings

    async def _in_browser[T](
        self, action: Callable[[], Awaitable[T]], notes: list[str], subject: str
    ) -> T | _Fallback:
        """Run a browser read, or say that the Places API has to answer instead."""
        if self._blocked is not None or not self.gate.try_acquire(Source.GOOGLE_MAPS):
            return USE_PLACES
        if self._browser_used:
            await self.browser.pause()
        self._browser_used = True
        try:
            return await action()
        except SearchBlockedError as error:
            self._blocked = str(error)
            logger.warning("Google Maps blocked the browser: %s", error)
            notes.append(f"Google Maps blocked the browser ({error}), Places API used")
            return USE_PLACES
        except ProfileNotFoundError:
            raise
        except (CollectionError, PlaywrightError) as error:
            reason = error.message if isinstance(error, PlaywrightError) else str(error)
            notes.append(f"Google Maps not readable for {subject} ({reason}), Places API used")
            return USE_PLACES

    async def _places_profile(self, target: maps.ProfileTarget) -> PlaceSnapshot | None:
        place_id = await self._places_id(target.name, target.address, target.place_id)
        if place_id is None:
            return None
        self.gate.acquire(Source.PLACES)
        return await places.place_details(place_id, self.api_key)

    async def _places_competitors(
        self,
        phrase: str,
        centre: tuple[float, float],
        exclude: list[PlaceSnapshot],
        own: Business,
        count: int,
    ) -> list[PlaceSnapshot]:
        self.gate.acquire(Source.PLACES)
        candidates = await places.top_competitors(
            phrase, *centre, self.api_key, limit=min(count + SEARCH_SPARE, places.MAX_PAGE_SIZE)
        )
        return [place for place in candidates if not _is_known_place(place, exclude, own)][:count]

    async def _places_position(
        self, phrase: str, centre: tuple[float, float], own: Business, radius: float
    ) -> int | None:
        own_id = await self._places_id(own.name, own.address, *own.place_ids)
        if own_id is None:
            raise CollectionError(f"the Places API knows no profile of {own.name} to rank")
        self.gate.acquire(Source.PLACES)
        ranked = await places.ranked_place_ids(phrase, *centre, self.api_key, radius)
        return ranked.index(own_id) + 1 if own_id in ranked else None

    async def _places_id(self, name: str, address: str, *known: str | None) -> str | None:
        """A ChIJ place id: a known one, else one looked up once per business."""
        found = next((item for item in known if item and item.startswith(PLACES_ID_PREFIX)), None)
        if found is not None:
            return found
        key = f"{name}|{address}"
        if key not in self._places_ids:
            self.gate.acquire(Source.PLACES)
            self._places_ids[key] = await places.find_place(name, address, self.api_key)
        return self._places_ids[key]


def _is_known(listing: MapListing, exclude: list[PlaceSnapshot], own: Business) -> bool:
    if listing.place_id is not None and (
        listing.place_id in own.place_ids or any(p.place_id == listing.place_id for p in exclude)
    ):
        return True
    names = [own.name, *(place.name for place in exclude)]
    return any(same_business(listing.name, name) for name in names)


def _is_known_place(place: PlaceSnapshot, exclude: list[PlaceSnapshot], own: Business) -> bool:
    listing = MapListing(place.name, "", place.place_id, place.rating, place.review_count)
    return _is_known(listing, exclude, own)


def _row_and_column(key: str) -> tuple[int, int]:
    row, column = key[1:].split("c")
    return int(row), int(column)


def _result_list(
    phrase: str,
    listings: list[MapListing],
    own: Business,
    route: Literal["browser", "places_api"],
) -> MapResultList:
    businesses = [
        ListedBusiness(
            name=listing.name,
            review_count=listing.review_count,
            rating=listing.rating,
            place_id=listing.place_id,
            is_customer=_is_known(listing, [], own),
        )
        for listing in listings
    ]
    return MapResultList(phrase=phrase, businesses=businesses, route=route)
