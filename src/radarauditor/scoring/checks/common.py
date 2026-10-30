"""Shared outcome rules and snapshot helpers for the area check modules.

Context: Keeps the pass/partial/fail arithmetic and the text, URL and address normalisation in one
place so every area judges the same way.
Input: Snapshot parts, measured counts and catalogue thresholds.
Output: CheckOutcome values and normalised keys.
"""

import re
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from urllib.parse import urlsplit

from pydantic import HttpUrl

from radarauditor.collectors.patterns import host_of
from radarauditor.collectors.snapshot import (
    PageSnapshot,
    PlaceSnapshot,
    RawAudit,
    SerpResult,
    WebsiteSnapshot,
)
from radarauditor.db.models import CheckOutcome
from radarauditor.errors import ScoringError

CheckReturn = tuple[CheckOutcome, dict[str, object]]
Thresholds = Mapping[str, float | int | str]
CheckFunction = Callable[[RawAudit, Thresholds], CheckReturn]

TOLERANCE = 0.2
MOST_SHARE = 0.5
MAX_LISTED = 10
NO_PAID_DATA = "no_paid_data"

POSTAL_CODE_PATTERN = re.compile(r"\b(\d{5})\b")
LEGAL_FORM_PATTERN = re.compile(
    r"\b(gmbh|ug|ag|kg|ohg|gbr|e\s?k|e\s?v|mbh|co|haftungsbeschraenkt|inh)\b"
)
UTILITY_PATH_PATTERN = re.compile(
    r"(impressum|datenschutz|privacy|agb|kontakt|contact|ueber-uns|uber-uns|about|team|"
    r"blog|ratgeber|magazin|news|aktuelles|sitemap|login|cookie)"
)
NAP_KEYS: dict[str, tuple[str, ...]] = {
    "name": ("name", "company", "firma"),
    "street": ("street", "strasse", "address", "adresse"),
    "postal_code": ("postal_code", "postal", "zip", "plz"),
    "phone": ("phone", "telephone", "telefon", "tel"),
}
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
GOOGLE_PROFILE_MARKERS = (
    "google.com/maps",
    "google.de/maps",
    "maps.google",
    "g.page",
    "maps.app.goo.gl",
    "goo.gl/maps",
    "cid=",
)


def not_measurable(reason: str, **measured: object) -> CheckReturn:
    """Return NOT_MEASURABLE with the reason and any partial measurements."""
    return CheckOutcome.NOT_MEASURABLE, {"reason": reason, **measured}


def route_entry(routes: Iterable[str | None]) -> dict[str, object]:
    """Measured entry naming how the data was read, for the route note of the app.

    Args:
        routes: Route of every value the check used; None for values without a recorded route.

    Returns:
        {"route": id} for one route, {"routes": [ids]} for several, {} for none.
    """
    found = sorted({route for route in routes if route})
    if not found:
        return {}
    if len(found) == 1:
        return {"route": found[0]}
    return {"routes": found}


def place_routes(places: Iterable[PlaceSnapshot], field: str) -> dict[str, object]:
    """route_entry for one field of one or more Google profiles."""
    return route_entry(place.route_of(field) for place in places)


def dispatch(
    checks: Mapping[str, CheckFunction], check_id: str, raw: RawAudit, thresholds: Thresholds
) -> CheckReturn:
    """Run the check function registered for check_id.

    Args:
        checks: Check functions of one area by id.
        check_id: Catalogue id.
        raw: Collected audit data.
        thresholds: Catalogue thresholds of the check.

    Returns:
        Outcome and measured values.

    Raises:
        ScoringError: If the area has no function for check_id.
    """
    check = checks.get(check_id)
    if check is None:
        raise ScoringError(f"No check function for {check_id}")
    return check(raw, thresholds)


def number(thresholds: Thresholds, key: str) -> float:
    """Read a numeric threshold.

    Raises:
        ScoringError: If the threshold is missing or not a number.
    """
    value = thresholds.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ScoringError(f"Threshold {key!r} is missing or not numeric")
    return float(value)


def share_outcome(good: int, total: int) -> CheckOutcome:
    """PASSED if all items are good, PARTIAL if at least half are, FAILED otherwise."""
    if total <= 0 or good >= total:
        return CheckOutcome.PASSED
    if good / total >= MOST_SHARE:
        return CheckOutcome.PARTIAL
    return CheckOutcome.FAILED


def at_most_outcome(value: float, limit: float) -> CheckOutcome:
    """PASSED if value <= limit, PARTIAL within 20 % above, FAILED beyond."""
    if value <= limit:
        return CheckOutcome.PASSED
    if value <= round(limit * (1 + TOLERANCE), 9):
        return CheckOutcome.PARTIAL
    return CheckOutcome.FAILED


def at_least_outcome(value: float, minimum: float) -> CheckOutcome:
    """PASSED if value >= minimum, PARTIAL within 20 % below, FAILED beyond."""
    if value >= minimum:
        return CheckOutcome.PASSED
    if value >= round(minimum * (1 - TOLERANCE), 9):
        return CheckOutcome.PARTIAL
    return CheckOutcome.FAILED


def band_outcome(value: float, pass_from: float, partial_from: float) -> CheckOutcome:
    """PASSED if value >= pass_from, PARTIAL if value >= partial_from, FAILED below."""
    if value >= pass_from:
        return CheckOutcome.PASSED
    if value >= partial_from:
        return CheckOutcome.PARTIAL
    return CheckOutcome.FAILED


def ceiling_outcome(value: float, pass_to: float, partial_to: float) -> CheckOutcome:
    """PASSED if value <= pass_to, PARTIAL if value <= partial_to, FAILED above."""
    if value <= pass_to:
        return CheckOutcome.PASSED
    if value <= partial_to:
        return CheckOutcome.PARTIAL
    return CheckOutcome.FAILED


def share_of_best(own: float, rivals: Mapping[str, float]) -> tuple[float, str | None]:
    """Own value as a share of the best rival and that rival; 1.0 when no rival has any."""
    if not rivals:
        return 1.0, None
    leader = max(rivals, key=lambda name: rivals[name])
    best = rivals[leader]
    if best <= 0:
        return 1.0, leader
    return round(own / best, 3), leader


def combine(outcomes: Iterable[CheckOutcome]) -> CheckOutcome:
    """All PASSED gives PASSED, all FAILED gives FAILED, any mix gives PARTIAL."""
    judged = [outcome for outcome in outcomes if outcome is not CheckOutcome.NOT_MEASURABLE]
    if not judged:
        return CheckOutcome.NOT_MEASURABLE
    if all(outcome is CheckOutcome.PASSED for outcome in judged):
        return CheckOutcome.PASSED
    if all(outcome is CheckOutcome.FAILED for outcome in judged):
        return CheckOutcome.FAILED
    return CheckOutcome.PARTIAL


def fold(text: str) -> str:
    """Lower-case and transliterate German umlauts so slugs and text compare equal."""
    return text.casefold().translate(UMLAUTS)


def tokens(text: str) -> list[str]:
    """Split folded text into alphanumeric words."""
    return re.findall(r"[a-z0-9]+", fold(text))


def same_site(url: str, host: str) -> bool:
    """True if url lives on host or one of its subdomains."""
    candidate = host_of(url)
    return candidate is not None and (candidate == host or candidate.endswith(f".{host}"))


def is_google_profile_link(link: str, profile_url: str | None) -> bool:
    """True for a Google Maps or profile short link, or the place's own Google address."""
    if any(marker in link for marker in GOOGLE_PROFILE_MARKERS):
        return True
    return profile_url is not None and url_key(link) == url_key(profile_url)


def url_key(url: str | HttpUrl) -> str:
    """Normalised URL for comparisons: host without www, path without trailing slash."""
    parts = urlsplit(str(url))
    host = (parts.hostname or "").removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


def url_path(url: str | HttpUrl) -> str:
    """Folded path of a URL."""
    return fold(urlsplit(str(url)).path)


def listed(items: Iterable[str]) -> list[str]:
    """First MAX_LISTED items, enough for a finding without bloating the record."""
    return list(items)[:MAX_LISTED]


def content_pages(website: WebsiteSnapshot) -> list[PageSnapshot]:
    """Pages that answered with a 2xx status."""
    return [page for page in website.pages if 200 <= page.status_code < 300]


def start_page(website: WebsiteSnapshot) -> PageSnapshot | None:
    """The crawled page matching the start URL, else the first successful page."""
    start = url_key(website.start_url)
    pages = content_pages(website)
    for page in pages:
        if url_key(page.url) == start:
            return page
    return pages[0] if pages else None


def find_page(website: WebsiteSnapshot, url: str | HttpUrl | None) -> PageSnapshot | None:
    """The crawled page for url, if it was crawled."""
    if url is None:
        return None
    wanted = url_key(url)
    return next((page for page in website.pages if url_key(page.url) == wanted), None)


def click_depths(website: WebsiteSnapshot) -> dict[str, int]:
    """Breadth-first link distance from the start page for every reachable crawled page."""
    links = {
        url_key(page.url): {url_key(link) for link in page.internal_links} for page in website.pages
    }
    root = url_key(website.start_url)
    depths = {root: 0}
    queue = deque([root])
    while queue:
        current = queue.popleft()
        for target in links.get(current, set()):
            if target not in depths:
                depths[target] = depths[current] + 1
                queue.append(target)
    return depths


def is_utility_page(page: PageSnapshot, website: WebsiteSnapshot) -> bool:
    """True for legal, contact, about, blog and similar pages that are not service pages."""
    key = url_key(page.url)
    for legal in (website.legal_notice_url, website.privacy_url):
        if legal is not None and url_key(legal) == key:
            return True
    return UTILITY_PATH_PATTERN.search(url_path(page.url)) is not None


def h1_texts(page: PageSnapshot) -> list[str]:
    """Texts of all level-one headings."""
    return [text for level, text in page.headings if level == 1]


def page_text(page: PageSnapshot) -> str:
    """Folded title, headings and visible text excerpt of a page."""
    parts = [page.title or "", *(text for _, text in page.headings), page.text_excerpt]
    return fold(" ".join(parts))


def postal_code(text: str | None) -> str | None:
    """First five-digit German postal code in text."""
    if not text:
        return None
    match = POSTAL_CODE_PATTERN.search(text)
    return match.group(1) if match else None


def phone_key(phone: str | None) -> str | None:
    """Digits of a phone number in national format, so +49 251 1 and 0251-1 compare equal."""
    if not phone:
        return None
    stripped = phone.strip()
    digits = re.sub(r"\D", "", stripped)
    if stripped.startswith("+49"):
        digits = "0" + digits[2:]
    elif digits.startswith("0049"):
        digits = "0" + digits[4:]
    return digits or None


def street_key(street: str | None) -> str | None:
    """Street and house number folded, with every Straße spelling reduced to str."""
    if not street:
        return None
    folded = fold(street)
    folded = re.sub(r"(strasse|str\.|str\b)", "str", folded)
    key = re.sub(r"[^a-z0-9]", "", folded)
    return key or None


def address_key(address: str | None) -> str | None:
    """Street plus postal code of a one-line address, independent of separators and country."""
    if not address:
        return None
    code = postal_code(address)
    street = address.split(code)[0] if code else address.split(",")[0]
    street_part = street_key(street.strip(" ,"))
    if street_part is None and code is None:
        return None
    return f"{street_part or ''}|{code or ''}"


def normalise_name(name: str | None) -> str:
    """Business name without legal form, punctuation and case."""
    if not name:
        return ""
    words = " ".join(tokens(name))
    return " ".join(LEGAL_FORM_PATTERN.sub(" ", words).split())


def names_match(left: str | None, right: str | None) -> bool:
    """True if one normalised business name contains the other."""
    first, second = normalise_name(left), normalise_name(right)
    return bool(first) and bool(second) and (first in second or second in first)


def business_name(raw: RawAudit) -> str | None:
    """Name from the website footer, else from the Google profile.

    The footer comes first because a keyword-stuffed profile name would make service searches
    look like searches for the brand.
    """
    if raw.website is not None:
        name = nap_value(raw.website.footer_nap, "name")
        if name:
            return name
    return raw.place.name if raw.place is not None else None


def business_city(raw: RawAudit) -> str | None:
    """City from the website footer, else from the Google profile address."""
    if raw.website is not None:
        city = raw.website.footer_nap.get("city")
        if city:
            return city
    if raw.place is not None:
        match = re.search(r"\b\d{5}\s+([^,]+)", raw.place.formatted_address)
        if match:
            return match.group(1).strip()
    return None


def customer_host(raw: RawAudit) -> str | None:
    """Host of the customer's website from the crawl, else from the Google profile."""
    if raw.website is not None:
        return host_of(raw.website.start_url)
    if raw.place is not None:
        return host_of(raw.place.website)
    return None


def google_web_serps(raw: RawAudit) -> list[SerpResult]:
    """Google web searches, without Bing and image searches."""
    return [serp for serp in raw.serps if serp.engine == "google" and serp.vertical == "web"]


def brand_serps(raw: RawAudit) -> list[SerpResult]:
    """Google web searches for the business name."""
    name = business_name(raw)
    if name is None:
        return []
    return [serp for serp in google_web_serps(raw) if names_match(serp.phrase, name)]


def service_terms(raw: RawAudit) -> dict[str, list[str]]:
    """Service words of every tracked search phrase except brand searches, city removed.

    The phrases are those searched on Google and on Google Maps, so a blocked Google search
    still leaves the keywords of the Maps positions.
    """
    city_tokens = set(tokens(business_city(raw) or ""))
    name = business_name(raw)
    phrases = [serp.phrase for serp in google_web_serps(raw)]
    phrases.extend(ranking.phrase for ranking in raw.map_rankings)
    terms: dict[str, list[str]] = {}
    for phrase in phrases:
        if (name is not None and names_match(phrase, name)) or phrase in terms:
            continue
        words = [word for word in tokens(phrase) if word not in city_tokens]
        if words:
            terms[phrase] = words
    return terms


def page_matches_terms(page: PageSnapshot, words: list[str]) -> bool:
    """True if the URL, title or main heading of the page contains every service word."""
    focus = " ".join([url_path(page.url), page.title or "", *h1_texts(page)])
    present = set(tokens(focus))
    return all(word in present or word in fold(focus) for word in words)


def service_pages(raw: RawAudit) -> list[PageSnapshot]:
    """Pages dedicated to a tracked service, or all non-utility subpages if no phrase is tracked."""
    if raw.website is None:
        return []
    website = raw.website
    pages = [page for page in content_pages(website) if not is_utility_page(page, website)]
    terms = service_terms(raw)
    if not terms:
        start = start_page(website)
        return [page for page in pages if start is None or page.url != start.url]
    return [
        page for page in pages if any(page_matches_terms(page, words) for words in terms.values())
    ]


def schema_objects(json_ld: Iterable[object]) -> Iterator[dict[str, object]]:
    """Every JSON-LD object, flattening lists, @graph containers and nested objects."""
    stack: list[object] = list(json_ld)
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            if "@type" in item:
                yield item
            for value in item.values():
                if isinstance(value, dict | list):
                    stack.append(value)


def schema_types(item: Mapping[str, object]) -> set[str]:
    """The @type values of one JSON-LD object."""
    value = item.get("@type")
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {entry for entry in value if isinstance(entry, str)}
    return set()


def page_schema_types(page: PageSnapshot) -> set[str]:
    """All @type values on a page."""
    return {kind for item in schema_objects(page.json_ld) for kind in schema_types(item)}


def string_values(value: object) -> list[str]:
    """Strings of a JSON-LD property that may be a string, an object with @id/url, or a list."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [entry for key in ("@id", "url") if isinstance(entry := value.get(key), str)]
    if isinstance(value, list):
        return [text for entry in value for text in string_values(entry)]
    return []


def nap_value(nap: Mapping[str, str], field: str) -> str | None:
    """Value of a footer NAP field, tolerant of the key spelling the crawler used."""
    filled = {
        key.casefold(): value.strip() for key, value in nap.items() if value and value.strip()
    }
    aliases = NAP_KEYS[field]
    exact = next((filled[alias] for alias in aliases if alias in filled), None)
    if exact is not None:
        return exact
    return next(
        (value for key, value in filled.items() if any(alias in key for alias in aliases)), None
    )
