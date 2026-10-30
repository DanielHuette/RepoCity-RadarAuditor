"""Check German business directories for a listing and compare the spelling of the NAP.

Context: Directory list and search patterns live in config/verzeichnisse.yaml.
Input: Location.
Output: DirectoryHit per directory.
"""

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx
import yaml
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, ConfigDict, HttpUrl, ValidationError

from radarauditor.collectors.patterns import (
    POSTAL_CITY_PATTERN,
    collapse_whitespace,
    find_address,
    find_phone,
    normalise_address,
    normalise_name,
    normalise_phone,
    parse_http_url,
)
from radarauditor.collectors.snapshot import DirectoryHit
from radarauditor.collectors.transport import USER_AGENT, send
from radarauditor.db.models import Location
from radarauditor.errors import CollectionError, ConfigurationError

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
CONCURRENT_DIRECTORIES = 4
MAX_LISTING_CLIMB = 8
INVISIBLE_TAGS = ("script", "style", "noscript", "template", "svg")


class DirectoryEntry(BaseModel):
    """One directory from the catalogue."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    name: str
    kind: str
    search_url: str | None = None


class DirectoryCatalogue(BaseModel):
    """The part of config/verzeichnisse.yaml this collector reads."""

    directories: list[DirectoryEntry]


def load_catalogue(path: Path) -> DirectoryCatalogue:
    """Read and validate the directory catalogue.

    Args:
        path: Location of verzeichnisse.yaml.

    Returns:
        The validated catalogue.

    Raises:
        ConfigurationError: If the file is missing or malformed.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return DirectoryCatalogue.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfigurationError(f"directory catalogue {path} is unreadable: {error}") from error


async def check_directories(location: Location, catalogue: Path) -> list[DirectoryHit]:
    """Query every directory from the catalogue by name and city, read the listing if found.

    Directories without a search URL are returned with found=False; they are checked by hand or
    by other collectors. A directory whose search fails after retries is left out of the result
    and logged, so a missing entry means "not measured" rather than "not listed".

    Args:
        location: The location whose listing is searched.
        catalogue: Path of verzeichnisse.yaml.

    Returns:
        One hit per directory that could be checked, in catalogue order.

    Raises:
        ConfigurationError: If the catalogue is missing or malformed.
    """
    entries = load_catalogue(catalogue).directories
    semaphore = asyncio.Semaphore(CONCURRENT_DIRECTORIES)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        hits = await asyncio.gather(
            *(_check_one(client, semaphore, entry, location) for entry in entries)
        )
    return [hit for hit in hits if hit is not None]


async def _check_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    entry: DirectoryEntry,
    location: Location,
) -> DirectoryHit | None:
    if entry.search_url is None:
        return _not_found(entry.name)
    url = entry.search_url.format(
        name=quote(location.name, safe=""), city=quote(location.city, safe="")
    )
    async with semaphore:
        try:
            response = await send(client, "GET", url, source=entry.name)
        except CollectionError as error:
            logger.warning("Directory %s could not be checked: %s", entry.name, error)
            return None
    return find_listing(entry.name, str(response.url), response.text, location.name)


def find_listing(directory: str, page_url: str, html: str, business_name: str) -> DirectoryHit:
    """Look for the business on a directory search page and read the first matching listing.

    Args:
        directory: Directory name.
        page_url: Address of the search page, used to resolve the listing link.
        html: Source of the search page.
        business_name: Name as the customer writes it.

    Returns:
        The hit; found=False when the name does not appear on the page.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(INVISIBLE_TAGS):
        tag.decompose()
    located = _locate_listing(soup, _name_variants(business_name))
    if located is None:
        return _not_found(directory)
    match, container = located
    lines = [
        line
        for line in (collapse_whitespace(part) for part in container.get_text("\n").splitlines())
        if line
    ]
    return DirectoryHit(
        directory=directory,
        found=True,
        listing_url=_listing_url(match, container, page_url),
        name=collapse_whitespace(match.get_text(" ")),
        address=_address_line(find_address(lines)),
        phone=find_phone("\n".join(lines)),
    )


def _address_line(parts: dict[str, str]) -> str | None:
    if not parts:
        return None
    town = f"{parts['postal_code']} {parts['city']}"
    return f"{parts['street']}, {town}" if "street" in parts else town


def _name_variants(business_name: str) -> list[str]:
    exact = collapse_whitespace(business_name).lower()
    variants = [exact]
    without_form = normalise_name(business_name)
    if without_form and without_form != exact:
        variants.append(without_form)
    return variants


def _locate_listing(soup: BeautifulSoup, variants: list[str]) -> tuple[Tag, Tag] | None:
    fallback: tuple[Tag, Tag] | None = None
    for match in _deepest_matches(soup, variants):
        container = _listing_container(match)
        if container is not None:
            return match, container
        if fallback is None:
            fallback = (match, match)
    return fallback


def _deepest_matches(soup: BeautifulSoup, variants: list[str]) -> Iterator[Tag]:
    root = soup.body or soup
    for variant in variants:
        for element in root.find_all(True):
            if variant not in collapse_whitespace(element.get_text(" ")).lower():
                continue
            if not any(
                variant in collapse_whitespace(child.get_text(" ")).lower()
                for child in element.find_all(True, recursive=False)
            ):
                yield element


def _listing_container(match: Tag) -> Tag | None:
    current = match
    for _ in range(MAX_LISTING_CLIMB):
        if POSTAL_CITY_PATTERN.search(collapse_whitespace(current.get_text(" "))):
            return current
        if not isinstance(current.parent, Tag) or current.parent.name in ("body", "html"):
            return None
        current = current.parent
    return None


def _listing_url(match: Tag, container: Tag, page_url: str) -> HttpUrl | None:
    anchor = match if match.name == "a" else match.find_parent("a") or container.find("a")
    if not isinstance(anchor, Tag):
        return None
    href = anchor.get("href")
    if not isinstance(href, str) or not href.strip():
        return None
    return parse_http_url(urljoin(page_url, href.strip()))


def _not_found(directory: str) -> DirectoryHit:
    return DirectoryHit(
        directory=directory, found=False, listing_url=None, name=None, address=None, phone=None
    )


def nap_matches(expected: Location, found: DirectoryHit) -> dict[str, bool]:
    """Compare name, address and phone after normalising spacing, case and phone format.

    Legal forms (GmbH, e.K., UG, KG and similar) are ignored in names; street suffixes
    ("Straße", "Str.") are unified in addresses; phone numbers compare as digits with +49 or 0049
    turned into a leading 0.

    Args:
        expected: The location as the customer writes it.
        found: The directory listing.

    Returns:
        Keys name, address and phone; False when the listing lacks the value.
    """
    expected_address = f"{expected.street} {expected.postal_code} {expected.city}"
    return {
        "name": found.name is not None
        and normalise_name(found.name) == normalise_name(expected.name),
        "address": found.address is not None
        and normalise_address(found.address) == normalise_address(expected_address),
        "phone": found.phone is not None
        and normalise_phone(found.phone) == normalise_phone(expected.phone),
    }
