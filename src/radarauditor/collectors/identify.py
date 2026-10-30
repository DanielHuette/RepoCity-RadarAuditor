"""Identify a business started with only its website: name, address, phone, industry, keywords.

Context: First step of a COLLECT job, right after the website crawl and before the Google profile,
competitor, ranking and search collectors, which need name and city. It only fills what is missing:
a name that is still the website host, an empty city, an empty industry, no keywords (or keywords
stored without a city). Sources per value, first one wins: (1) JSON-LD LocalBusiness or
Organization on the start page, (2) the legal notice (Impressum), (3) the footer contact data of the
crawl, (4) a Google Places text search for "<name> <city>" or the host, accepted only when its
website is the customer's or its name and city match. Street, postal code and city always come
together from one source. The industry group comes from the Google categories, else from the words
of the name and the category text; the keywords are "<service> <city>" for the services of the
group, the first one primary, like the suggestions of the app.
Input: Customer, location and keywords as stored, the crawled website, settings and source gate.
Output: Identification with the updated rows and what was filled from which source; the values are
written through the repositories.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import SecretStr

from radarauditor.collectors import places
from radarauditor.collectors.maps_pages import same_business
from radarauditor.collectors.patterns import (
    POSTAL_CITY_PATTERN,
    STREET_PATTERN,
    collapse_whitespace,
    find_address,
    find_phone,
    host_of,
)
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import (
    IdentificationSource,
    IdentifiedField,
    IdentifiedValue,
    WebsiteSnapshot,
)
from radarauditor.collectors.transport import USER_AGENT, send
from radarauditor.db.app_settings import Source
from radarauditor.db.models import Customer, Keyword, Location
from radarauditor.db.repositories import Repositories
from radarauditor.errors import CollectionError, IdentificationError, StorageError
from radarauditor.industries import IndustryCatalogue, IndustryGroup

logger = logging.getLogger(__name__)

CITY_NOT_FOUND = (
    "Ort nicht gefunden: Weder Impressum noch Google-Profil nennen eine Anschrift. "
    "Bitte Ort in der Auswertung ergänzen und neu prüfen."
)
REQUEST_TIMEOUT_SECONDS = 20
MAX_NAME_CHARS = 120
PHONE_LINES_AFTER_ADDRESS = 8
PLACES_ID_PREFIX = "ChIJ"
BUSINESS_TYPES = frozenset(
    {
        "LocalBusiness",
        "Organization",
        "Corporation",
        "ProfessionalService",
        "HomeAndConstructionBusiness",
        "AutomotiveBusiness",
        "HealthAndBeautyBusiness",
        "FoodEstablishment",
        "MedicalBusiness",
        "MedicalOrganization",
        "LegalService",
        "FinancialService",
        "Store",
        "SportsActivityLocation",
    }
)
NON_BUSINESS_TYPES = frozenset(
    {"WebSite", "WebPage", "Person", "Event", "Product", "Offer", "BreadcrumbList", "ImageObject"}
)
Mark = Callable[[IdentifiedField, str, IdentificationSource], None]
"""Records one identified value with its source."""
INVISIBLE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe")
CHROME_TAGS = ("header", "nav", "footer", "aside")
COUNTRY_PREFIX = re.compile(r"(?:^|[\s,])(?:D|DE)\s*-?\s*$")
NAME_PREFIX = re.compile(r"^(?:firma|anbieter|betreiber|unternehmen|name)\s*:\s*(.+)$", re.I)
NOT_A_NAME = re.compile(
    r"^(?:impressum|imprint|angaben|kontakt|anschrift|adresse|postanschrift|verantwortlich|"
    r"inhaber|vertreten|vertretungsberechtigt|geschäftsführ|telefon|tel\b|fax|e-?mail|ust|"
    r"umsatzsteuer|registergericht|handelsregister|steuernummer|postfach|so erreichen)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Address:
    """Postal address of the business; street and postal code may be missing, the city not."""

    street: str | None
    postal_code: str | None
    city: str


@dataclass(frozen=True)
class Facts:
    """What one source says about the business; any field may be missing."""

    name: str | None = None
    address: Address | None = None
    phone: str | None = None


@dataclass
class _Found:
    """Values collected so far, each with the source it came from."""

    name: tuple[str, IdentificationSource] | None = None
    address: tuple[Address, IdentificationSource] | None = None
    phone: tuple[str, IdentificationSource] | None = None
    place: places.BusinessCandidate | None = None

    def take(self, facts: Facts, source: IdentificationSource) -> None:
        """Keep every value of facts that no earlier source gave."""
        if self.name is None and facts.name:
            self.name = (facts.name, source)
        if self.address is None and facts.address is not None:
            self.address = (facts.address, source)
        if self.phone is None and facts.phone:
            self.phone = (facts.phone, source)


@dataclass(frozen=True)
class Identification:
    """Customer, location and keywords after the identify step, and what it filled in."""

    customer: Customer
    location: Location | None
    keywords: list[Keyword]
    identified: list[IdentifiedValue] = field(default_factory=list)


@dataclass(frozen=True)
class _Missing:
    name: bool
    city: bool
    industry: bool
    keywords: bool

    @property
    def any(self) -> bool:
        return self.name or self.city or self.industry or self.keywords


def needs_identification(customer: Customer, keywords: list[Keyword]) -> bool:
    """Whether the identify step has anything to fill in for this customer.

    Args:
        customer: Customer as stored.
        keywords: Tracked keywords of the customer.

    Returns:
        True when the name is still the website host, or city, industry or keywords (or the city
        of a keyword) are missing.
    """
    return _missing(customer, keywords).any


def _missing(customer: Customer, keywords: list[Keyword]) -> _Missing:
    host = host_of(customer.website)
    return _Missing(
        name=host is not None and customer.name.strip().lower() == host,
        city=not customer.city.strip(),
        industry=not customer.industry.strip(),
        keywords=not keywords or any(not keyword.city.strip() for keyword in keywords),
    )


async def identify_customer(
    customer: Customer,
    location: Location | None,
    keywords: list[Keyword],
    site: WebsiteSnapshot | None,
    *,
    repositories: Repositories,
    industries: IndustryCatalogue,
    gate: SourceGate,
    api_key: SecretStr,
    notes: list[str],
) -> Identification:
    """Fill in what an audit started with only the website lacks and store it.

    Args:
        customer: Customer as stored.
        location: First location of the customer, None if it has none.
        keywords: Tracked keywords of the customer.
        site: Crawled website, None when the crawl had nothing to crawl.
        repositories: Database access for writing the identified values.
        industries: Industry groups of config/branchen.yaml.
        gate: Source switches and caps of the job (Places API).
        api_key: Google Cloud key with the Places API (New) enabled.
        notes: Receives a line for every source that could not be read.

    Returns:
        The updated customer, location and keywords and the identified values with their sources;
        the unchanged rows and no values when nothing was missing.

    Raises:
        IdentificationError: If no source names the city of the business.
        StorageError: If the identified values cannot be written.
    """
    missing = _missing(customer, keywords)
    if not missing.any:
        return Identification(customer, location, keywords)
    found = _Found()
    if missing.name or missing.city:
        await _read_website(site, found, notes)
    await _ask_google(customer, location, found, missing, gate, api_key, notes)
    return _store(customer, location, keywords, found, missing, industries, repositories, notes)


async def _read_website(site: WebsiteSnapshot | None, found: _Found, notes: list[str]) -> None:
    if site is None:
        return
    if site.pages:
        found.take(facts_from_json_ld(site.pages[0].json_ld), "json_ld")
    if (found.name is None or found.address is None) and site.legal_notice_url is not None:
        html = await _fetch_legal_notice(str(site.legal_notice_url), notes)
        if html is not None:
            found.take(facts_from_legal_notice(html), "impressum")
    found.take(_facts_from_footer(site.footer_nap), "footer")


async def _fetch_legal_notice(url: str, notes: list[str]) -> str | None:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await send(client, "GET", url, source="legal notice")
    except CollectionError as error:
        notes.append(f"legal notice not readable: {error}")
        return None
    return response.text


def facts_from_json_ld(blocks: list[dict[str, object]]) -> Facts:
    """Name, address and phone of the first LocalBusiness or Organization block that has them.

    Blocks nested in "@graph" count as well. A block counts as a business when its type is a
    schema.org business type, or when it carries a postal address and is no page, person, event,
    product or offer. Street, postal code and city come from one block.

    Args:
        blocks: JSON-LD blocks of the start page.

    Returns:
        What the blocks say; empty fields when they say nothing.
    """
    name: str | None = None
    address: Address | None = None
    phone: str | None = None
    for block in _flatten(blocks):
        types = _types(block)
        block_address = _json_ld_address(block.get("address"))
        is_business = bool(types & BUSINESS_TYPES) or (
            block_address is not None and not types & NON_BUSINESS_TYPES
        )
        if not is_business:
            continue
        name = name or _text(block.get("name"))
        address = address or block_address
        phone = phone or _text(block.get("telephone"))
    return Facts(name=name, address=address, phone=phone)


def _flatten(blocks: list[dict[str, object]]) -> list[dict[str, object]]:
    flat: list[dict[str, object]] = []
    for block in blocks:
        graph = block.get("@graph")
        if isinstance(graph, list):
            flat.extend(item for item in graph if isinstance(item, dict))
        else:
            flat.append(block)
    return flat


def _types(block: dict[str, object]) -> set[str]:
    value = block.get("@type")
    values = value if isinstance(value, list) else [value]
    return {item.rsplit("/", 1)[-1] for item in values if isinstance(item, str)}


def _json_ld_address(value: object) -> Address | None:
    candidates = value if isinstance(value, list) else [value]
    for item in candidates:
        if isinstance(item, str):
            parsed = find_address([item])
            if parsed:
                return Address(parsed.get("street"), parsed["postal_code"], parsed["city"])
            continue
        if not isinstance(item, dict):
            continue
        city = _text(item.get("addressLocality"))
        if city:
            return Address(_text(item.get("streetAddress")), _text(item.get("postalCode")), city)
    return None


def _text(value: object) -> str | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    return collapse_whitespace(value) or None


def facts_from_legal_notice(html: str) -> Facts:
    """Company, address and phone from a legal notice (Impressum).

    German law requires company name and postal address there. The first line with a postal code
    and city is the address; the street stands before it on the same line or on the line above,
    and the company on the line above the street. Header, navigation, footer and side bars are left
    out. The company line is taken only when it reads like a name: no label such as "Inhaber" or
    "Angaben gemäß § 5 TMG", no postal code, no phone number; "Firma: X" gives X.

    Args:
        html: Source of the legal notice page.

    Returns:
        What the page says; empty fields when it says nothing usable.
    """
    lines = _content_lines(html)
    for index, line in enumerate(lines):
        match = POSTAL_CITY_PATTERN.search(line)
        if match is None:
            continue
        prefix = COUNTRY_PREFIX.sub("", line[: match.start()]).strip(" ,|·•–-")
        if prefix:
            street = _street(prefix)
            if street is None:
                continue
            company_index = index - 1
        else:
            street = _street(lines[index - 1]) if index > 0 else None
            company_index = index - 2 if street is not None else -1
        name = _company(lines[company_index]) if company_index >= 0 else None
        tail = "\n".join(lines[index : index + PHONE_LINES_AFTER_ADDRESS])
        return Facts(
            name=name,
            address=Address(street, match.group(1), match.group(2)),
            phone=find_phone(tail),
        )
    return Facts()


def _content_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(INVISIBLE_TAGS):
        tag.decompose()
    main = soup.find("main") or soup.find(True, attrs={"role": "main"})
    root: Tag | BeautifulSoup = main if isinstance(main, Tag) else (soup.body or soup)
    if root is not main:
        for tag in root.find_all(CHROME_TAGS):
            tag.decompose()
    lines = (collapse_whitespace(line) for line in root.get_text("\n").splitlines())
    return [line for line in lines if line]


def _street(text: str) -> str | None:
    segment = re.split(r"[,|·•]", text.strip(" ,|·•–"))[-1].strip()
    if segment.lower().startswith("postfach"):
        return None
    match = STREET_PATTERN.fullmatch(segment)
    return collapse_whitespace(match.group(1)) if match else None


def _company(line: str) -> str | None:
    labelled = NAME_PREFIX.match(line)
    candidate = (labelled.group(1) if labelled else line).strip(" ,;")
    if not candidate or len(candidate) > MAX_NAME_CHARS or candidate.endswith(":"):
        return None
    if "§" in candidate or NOT_A_NAME.match(candidate) or "@" in candidate:
        return None
    if POSTAL_CITY_PATTERN.search(candidate) or find_phone(candidate):
        return None
    return candidate


def _facts_from_footer(nap: dict[str, str]) -> Facts:
    city = nap.get("city")
    address = Address(nap.get("street"), nap.get("postal_code"), city) if city else None
    return Facts(name=nap.get("name"), address=address, phone=nap.get("phone"))


async def _ask_google(
    customer: Customer,
    location: Location | None,
    found: _Found,
    missing: _Missing,
    gate: SourceGate,
    api_key: SecretStr,
    notes: list[str],
) -> None:
    """Search the business on Google and keep the first candidate that is clearly the customer."""
    if not _google_needed(location, found, missing):
        return
    host = host_of(customer.website)
    name = found.name[0] if found.name else (None if missing.name else customer.name)
    city = found.address[0].city if found.address else (customer.city.strip() or None)
    query = f"{name} {city}" if name and city else (name or host)
    if not query:
        return
    try:
        gate.acquire(Source.PLACES)
        candidates = await places.search_businesses(query, api_key)
    except CollectionError as error:
        notes.append(f"Google profile not searched: {error}")
        return
    place = next((item for item in candidates if _is_customer(item, host, name, city)), None)
    if place is None:
        notes.append(f"no Google profile clearly belongs to {name or host}")
        return
    found.place = place
    found.take(_facts_from_place(place), "google_profile")


def _google_needed(location: Location | None, found: _Found, missing: _Missing) -> bool:
    needs_coordinates = location is None or location.latitude is None
    still_missing = (
        (missing.name and found.name is None)
        or (missing.city and found.address is None)
        or missing.industry
    )
    return still_missing or needs_coordinates


def _facts_from_place(place: places.BusinessCandidate) -> Facts:
    address = find_address([part.strip() for part in place.formatted_address.split(",")])
    if not address:
        address = find_address([place.formatted_address])
    return Facts(
        name=place.name or None,
        address=(
            Address(address.get("street"), address["postal_code"], address["city"])
            if address
            else None
        ),
        phone=place.phone,
    )


# Platforms many businesses share one host on: a Google profile linking the same host is no proof
# that it is this customer's profile, so only name and city can confirm it.
SHARED_HOSTS = frozenset(
    {
        "facebook.com",
        "m.facebook.com",
        "instagram.com",
        "linkedin.com",
        "xing.com",
        "google.com",
        "sites.google.com",
        "linktr.ee",
        "wa.me",
        "youtube.com",
        "tiktok.com",
    }
)


def _is_customer(
    place: places.BusinessCandidate, host: str | None, name: str | None, city: str | None
) -> bool:
    """The candidate links the customer's own website, or has its name and lies in its city."""
    website_host = host_of(place.website)
    if host is not None and host not in SHARED_HOSTS and website_host == host:
        return True
    if name is None or city is None or not same_business(place.name, name):
        return False
    return city.casefold() in place.formatted_address.casefold()


def _store(
    customer: Customer,
    location: Location | None,
    keywords: list[Keyword],
    found: _Found,
    missing: _Missing,
    industries: IndustryCatalogue,
    repositories: Repositories,
    notes: list[str],
) -> Identification:
    identified: list[IdentifiedValue] = []

    def mark(name: IdentifiedField, value: str, source: IdentificationSource) -> None:
        identified.append(IdentifiedValue(field=name, value=value, source=source))

    name = _identified_name(customer, found, missing, mark, notes)
    city = _identified_city(customer, name, found, missing, repositories, mark)
    group = _industry(customer, name, found, missing, industries, mark, notes)
    industry = group.id if missing.industry and group is not None else None
    repositories.update_customer(
        customer.id,
        name=name if name != customer.name else None,
        city=city if city != customer.city else None,
        industry=industry,
    )
    updated = customer.model_copy(
        update={"name": name, "city": city, "industry": industry or customer.industry}
    )
    if updated.crm_customer_id is None and (missing.name or missing.city):
        _link_crm(repositories, updated, notes)
    stored_location = _store_location(updated, location, found, repositories, mark)
    stored_keywords = _store_keywords(updated, keywords, group, repositories, mark, notes)
    return Identification(updated, stored_location, stored_keywords, identified)


def _identified_name(
    customer: Customer, found: _Found, missing: _Missing, mark: Mark, notes: list[str]
) -> str:
    if not missing.name:
        return customer.name
    if found.name is None:
        notes.append("business name not found, the website host stays the name")
        return customer.name
    name, source = found.name
    mark("name", name, source)
    return name


def _identified_city(
    customer: Customer,
    name: str,
    found: _Found,
    missing: _Missing,
    repositories: Repositories,
    mark: Mark,
) -> str:
    """The stored city, or the one found when it is missing.

    Raises:
        IdentificationError: If the city is missing and no source names it.
        StorageError: If the name found so far cannot be stored.
    """
    if not missing.city:
        return customer.city.strip()
    if found.address is None:
        # The name found so far is kept, so the app shows it next to the missing city.
        if name != customer.name:
            repositories.update_customer(customer.id, name=name)
        raise IdentificationError(CITY_NOT_FOUND)
    address, source = found.address
    mark("city", address.city, source)
    return address.city


def _industry(
    customer: Customer,
    name: str,
    found: _Found,
    missing: _Missing,
    industries: IndustryCatalogue,
    mark: Mark,
    notes: list[str],
) -> IndustryGroup | None:
    """The industry group of the customer; a missing one is recognised and marked."""
    if not missing.industry:
        return industries.resolve(customer.industry)
    place = found.place
    source: IdentificationSource = "google_types"
    group = (
        industries.by_google_types([*filter(None, [place.primary_type]), *place.types])
        if place is not None
        else None
    )
    if group is None:
        source = "name_words"
        texts = [name, *(item for item in [place.category if place else None] if item)]
        group = industries.by_words(texts)
    if group is None:
        notes.append("industry not recognised, checks that need it are not measurable")
        return None
    mark("industry", group.id, source)
    return group


def _link_crm(repositories: Repositories, customer: Customer, notes: list[str]) -> None:
    try:
        repositories.link_crm_customer(customer.id)
    except StorageError as error:
        notes.append(f"customer not linked to the CRM: {error}")


def _store_location(
    customer: Customer,
    location: Location | None,
    found: _Found,
    repositories: Repositories,
    mark: Mark,
) -> Location:
    """Fill empty location fields; a missing location is created.

    Street and postal code are taken only from an address in the customer's city; coordinates and
    the place id only from the Google profile accepted as the customer's.
    """
    current = location or _empty_location(customer)
    texts = _location_texts(customer, current, found, mark)
    coordinates = _coordinates(current, found.place)
    updated = current.model_copy(update={**texts, **coordinates})
    if location is None:
        return repositories.create_location(updated)
    if texts or coordinates:
        repositories.update_location(
            location.id,
            name=texts.get("name"),
            street=texts.get("street"),
            postal_code=texts.get("postal_code"),
            city=texts.get("city"),
            phone=texts.get("phone"),
            google_place_id=texts.get("google_place_id"),
            latitude=coordinates.get("latitude"),
            longitude=coordinates.get("longitude"),
        )
    return updated


def _empty_location(customer: Customer) -> Location:
    return Location(
        id=uuid4(),
        customer_id=customer.id,
        name="",
        street="",
        postal_code="",
        city="",
        phone="",
        google_place_id=None,
        latitude=None,
        longitude=None,
    )


def _location_texts(
    customer: Customer, current: Location, found: _Found, mark: Mark
) -> dict[str, str]:
    texts: dict[str, str] = {}
    if not current.name.strip() or current.name.strip().lower() == host_of(customer.website):
        texts["name"] = customer.name
    if not current.city.strip():
        texts["city"] = customer.city
    texts.update(_address_texts(customer.city, current, found, mark))
    if not current.phone.strip() and found.phone is not None:
        texts["phone"] = found.phone[0]
        mark("phone", found.phone[0], found.phone[1])
    place = found.place
    if (
        place is not None
        and not current.google_place_id
        and place.place_id.startswith(PLACES_ID_PREFIX)
    ):
        texts["google_place_id"] = place.place_id
        mark("google_place_id", place.place_id, "google_profile")
    return texts


def _address_texts(city: str, current: Location, found: _Found, mark: Mark) -> dict[str, str]:
    """Street and postal code for empty fields, only from an address in the customer's city."""
    if found.address is None or found.address[0].city.casefold() != city.casefold():
        return {}
    address, source = found.address
    texts: dict[str, str] = {}
    if not current.street.strip() and address.street:
        texts["street"] = address.street
        mark("street", address.street, source)
    if not current.postal_code.strip() and address.postal_code:
        texts["postal_code"] = address.postal_code
        mark("postal_code", address.postal_code, source)
    return texts


def _coordinates(current: Location, place: places.BusinessCandidate | None) -> dict[str, float]:
    if place is None or current.latitude is not None:
        return {}
    if place.latitude is None or place.longitude is None:
        return {}
    return {"latitude": place.latitude, "longitude": place.longitude}


def keyword_suggestions(group: IndustryGroup | None, city: str) -> list[str]:
    """Phrases "<service> <city>" for the typical services of the group, as the app suggests.

    The services are the phrases the identify step stores, so the noted suggestions match them.

    Args:
        group: Industry group of the customer, None when it is unknown.
        city: City of the customer; surrounding spaces are ignored.

    Returns:
        One phrase per distinct service in the group's order; none without group or city.
    """
    place = city.strip()
    if not place:
        return []
    return [f"{service} {place}" for service in _services(group)]


def _services(group: IndustryGroup | None) -> list[str]:
    return list(dict.fromkeys(service.strip() for service in group.services)) if group else []


def _store_keywords(
    customer: Customer,
    keywords: list[Keyword],
    group: IndustryGroup | None,
    repositories: Repositories,
    mark: Mark,
    notes: list[str],
) -> list[Keyword]:
    if keywords:
        return _with_city(keywords, customer.city, repositories, notes)
    services = _services(group)
    if not services:
        notes.append("no keywords: the industry is not known")
        return []
    # The phrase is the service; every search adds the keyword's city, giving "<service> <city>".
    added = repositories.add_keywords(customer.id, services, customer.city, first_primary=True)
    mark("keywords", ", ".join(keyword_suggestions(group, customer.city)), "industry_services")
    return added


def _with_city(
    keywords: list[Keyword], city: str, repositories: Repositories, notes: list[str]
) -> list[Keyword]:
    """The keywords with the identified city, written in one update.

    A keyword without city whose phrase is already tracked in that city is left out, since a
    phrase is tracked once per city; when the update fails, every keyword without city is left out.
    """
    pending = _cityless(keywords, city, notes)
    stored = {keyword.id: keyword for keyword in _store_city(pending, city, repositories, notes)}
    return [
        stored.get(keyword.id, keyword)
        for keyword in keywords
        if keyword.city.strip() or keyword.id in stored
    ]


def _cityless(keywords: list[Keyword], city: str, notes: list[str]) -> list[Keyword]:
    tracked = {keyword.phrase for keyword in keywords if keyword.city == city}
    pending: list[Keyword] = []
    for keyword in keywords:
        if keyword.city.strip():
            continue
        if keyword.phrase in tracked:
            notes.append(f"keyword {keyword.phrase} keeps no city: it is already tracked in {city}")
        else:
            pending.append(keyword)
    return pending


def _store_city(
    keywords: list[Keyword], city: str, repositories: Repositories, notes: list[str]
) -> list[Keyword]:
    if not keywords:
        return []
    try:
        repositories.update_keywords_city([keyword.id for keyword in keywords], city)
    except StorageError as error:
        phrases = ", ".join(keyword.phrase for keyword in keywords)
        notes.append(f"keywords {phrases} keep no city: {error}")
        return []
    return [keyword.model_copy(update={"city": city}) for keyword in keywords]
