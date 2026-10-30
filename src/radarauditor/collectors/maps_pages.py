"""Read Google Maps pages: a business profile with its review and info tabs, and a result list.

Context: Pure parsers behind the browser Maps collector, kept apart so they run on saved pages in
tests. Google Maps renders obfuscated class names, so the parsers lean on what stays stable:
data-item-id attributes, ARIA roles and labels, headings and German labels.
Input: HTML of a Maps place page, its "Rezensionen" and "Info" tabs, its photo viewer, or a
search result list.
Output: PlaceSnapshot with every read field marked as read in the browser, MapListings and the
capture dates of the photos shown in the viewer.
"""

import calendar
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from pydantic import HttpUrl

from radarauditor.collectors.patterns import collapse_whitespace, normalise_name, parse_http_url
from radarauditor.collectors.snapshot import DataRoute, PlaceSnapshot

BROWSER: DataRoute = "browser"
DAYS_PER_UNIT = {
    "minute": 1 / 1440,
    "stunde": 1 / 24,
    "hour": 1 / 24,
    "tag": 1.0,
    "day": 1.0,
    "woche": 7.0,
    "week": 7.0,
    "monat": 30.44,
    "month": 30.44,
    "jahr": 365.25,
    "year": 365.25,
}
RELATIVE_GERMAN = re.compile(
    r"vor\s+(einer|einem|eine|ein|\d+)\s+(Minute|Stunde|Tag|Woche|Monat|Jahr)[a-z]*",
    re.IGNORECASE,
)
RELATIVE_ENGLISH = re.compile(
    r"\b(an?|\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE
)
STARS_PATTERN = re.compile(r"^\s*(\d(?:[,.]\d)?)\s+(?:Sterne?|stars?)\b", re.IGNORECASE)
REVIEW_COUNT_PATTERN = re.compile(r"([\d.]+)\s+(?:Rezensionen|Rezension|reviews?)\b", re.IGNORECASE)
PHOTO_COUNT_PATTERN = re.compile(r"(\d[\d.]*)\s+(?:Fotos|Foto|photos?)\b", re.IGNORECASE)
QUESTION_COUNT_PATTERN = re.compile(r"(\d+)\s+(?:Fragen|Frage|questions?)\b", re.IGNORECASE)
CHIJ_PATTERN = re.compile(r"ChIJ[0-9A-Za-z_-]{16,}")
FEATURE_ID_PATTERN = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)")
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
LABEL_PREFIXES = ("Adresse:", "Telefon:", "Address:", "Phone:")
SERVICE_AREA_PREFIXES = ("Einzugsgebiet:", "Servicegebiet:", "Service area:")
DESCRIPTION_HEADINGS = ("Von ", "Vom Inhaber", "Beschreibung", "From ")
SERVICE_HEADINGS = ("Leistungen", "Dienstleistungen", "Services")
PRODUCT_HEADINGS = ("Produkte", "Products")
POST_HEADINGS = ("Neuigkeiten", "Updates vom Inhaber", "Updates from the owner")
QUESTION_HEADINGS = ("Fragen und Antworten", "Questions & answers")
OWNER_REPLY_LABELS = ("Antwort vom Inhaber", "Response from the owner")
SPONSORED_LABELS = ("Gesponsert", "Anzeige", "Sponsored")
NO_REVIEWS_LABELS = ("Keine Rezensionen", "No reviews")
CLOSED_LABELS = {
    "Dauerhaft geschlossen": "CLOSED_PERMANENTLY",
    "Permanently closed": "CLOSED_PERMANENTLY",
    "Vorübergehend geschlossen": "CLOSED_TEMPORARILY",
    "Temporarily closed": "CLOSED_TEMPORARILY",
}
OPERATIONAL = "OPERATIONAL"
GERMAN_MONTHS = {
    "januar": 1,
    "jan": 1,
    "februar": 2,
    "feb": 2,
    "märz": 3,
    "mär": 3,
    "mrz": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "dez": 12,
}
# Longest names first, so "Juni" is not read as "Jun" followed by a stray letter.
MONTH_YEAR_PATTERN = re.compile(
    r"(?<![\wäöüÄÖÜ])("
    + "|".join(sorted(GERMAN_MONTHS, key=len, reverse=True))
    + r")\.?\s+((?:19|20)\d{2})(?!\d)",
    re.IGNORECASE,
)
# A photo date stands alone or with a short label; longer texts are captions or reviews.
MAX_DATE_TEXT_CHARS = 40
PHOTO_BUTTONS = ("Fotos ansehen", "Alle Fotos ansehen", "See photos", "Foto von ")
GALLERY_TABS = (("Vom Inhaber", "By owner"), ("Alle", "All"))
NEXT_PHOTO_BUTTONS = ("Nächstes Foto", "Nächstes Bild", "Weiter", "Next photo", "Next")
STREET_VIEW_MARKERS = ("street view", "360°", "360 °", "fotokugel", "photo sphere")
# The gallery tab "Street View & 360°" names the category, not the photo on display.
GALLERY_TAB_LABEL = re.compile(r"street view\s*(?:&|und|and)\s*360", re.IGNORECASE)
VIEWER_EXCLUDED = '[role="tab"], [role="tablist"], [data-review-id]'
REVIEW_TEXT_CLASS = "wiI7pd"
REVIEW_DATE_CLASS = "rsqaWe"
REPLY_CLASS = "CDe7pd"
MAX_REPLY_CLIMB = 3


@dataclass(frozen=True)
class MapListing:
    """One entry of a Maps result list; place_id is a ChIJ id or a Maps feature id when shown."""

    name: str
    url: str
    place_id: str | None
    rating: float | None
    review_count: int | None


@dataclass(frozen=True)
class ViewerPhoto:
    """The photo the Maps viewer shows: its capture date if shown, and whether it is Street View."""

    taken: date | None
    street_view: bool


@dataclass(frozen=True)
class PhotoDates:
    """Capture dates read in the photo viewer: the newest and how many dated photos were seen."""

    newest: date | None
    dated_count: int


def photo_date(text: str, now: datetime) -> date | None:
    """Return the capture date a Maps photo label stands for.

    A month and year such as "Apr. 2022" or "März 2025" counts as the last day of that month, at
    most today, so a photo is never judged older than it can be; a relative date such as
    "vor 3 Monaten" counts back from now.

    Args:
        text: Label of a photo, e.g. "Apr. 2022", "vor einem Jahr", "vor 2 Wochen".
        now: Moment the viewer was read.

    Returns:
        The date, or None when the text holds no date or is too long to be a date label.
    """
    text = collapse_whitespace(text)
    if not text or len(text) > MAX_DATE_TEXT_CHARS:
        return None
    match = MONTH_YEAR_PATTERN.search(text)
    if match is not None:
        month = GERMAN_MONTHS[match.group(1).casefold()]
        year = int(match.group(2))
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        return min(last_day, now.date())
    age = relative_age_days(text)
    return (now - timedelta(days=age)).date() if age is not None else None


def page_texts(html: str) -> frozenset[str]:
    """Short texts and labels a page shows outside tabs and reviews, e.g. before the viewer opens.

    The viewer keeps the place panel in the page, so texts already there (post dates or a
    "Street View" tile, for example) say nothing about the displayed photo.

    Args:
        html: Page source before the photo viewer opens.

    Returns:
        The texts, for viewer_photo to ignore.
    """
    return frozenset(_viewer_texts(html))


def viewer_photo(html: str, before: frozenset[str], now: datetime) -> ViewerPhoto:
    """Read the date and kind of the photo the opened Maps viewer displays.

    Args:
        html: Page source with the photo viewer open.
        before: page_texts of the page before the viewer opened; those texts are ignored.
        now: Moment the viewer was read; relative dates count back from it.

    Returns:
        The first new date text of the viewer, and whether the photo is a Street View or 360°
        image, which the viewer names in its text outside the gallery tabs.
    """
    texts = [text for text in _viewer_texts(html) if text not in before]
    taken = next((found for text in texts if (found := photo_date(text, now)) is not None), None)
    folded = " ".join(text for text in texts if not GALLERY_TAB_LABEL.search(text)).casefold()
    street_view = any(marker in folded for marker in STREET_VIEW_MARKERS)
    return ViewerPhoto(taken=taken, street_view=street_view)


def photo_button(html: str) -> str | None:
    """Accessible name of the control that opens the photo viewer of a place page, if shown.

    Args:
        html: Source of a place page.

    Returns:
        The first name of PHOTO_BUTTONS some control's label starts with, else None.
    """
    labels = [_label(element) for element in BeautifulSoup(html, "lxml").select("[aria-label]")]
    return next(
        (name for name in PHOTO_BUTTONS if any(label.startswith(name) for label in labels)), None
    )


def gallery_tab(html: str) -> str | None:
    """The gallery tab to read: "Vom Inhaber" when the owner uploaded photos, else "Alle".

    Args:
        html: Source of the page with the photo viewer open.

    Returns:
        The tab name to click, or None when the viewer offers neither tab.
    """
    offered = tab_labels(html)
    for names in GALLERY_TABS:
        for name in names:
            if any(tab == name or tab.startswith(f"{name} ") for tab in offered):
                return name
    return None


def next_photo_button(html: str) -> str | None:
    """Accessible name of the viewer's control for the next photo, if the viewer shows one.

    Args:
        html: Source of the page with the photo viewer open.

    Returns:
        The name of NEXT_PHOTO_BUTTONS the viewer shows, else None.
    """
    labels = {_label(element) for element in BeautifulSoup(html, "lxml").select("[aria-label]")}
    return next((name for name in NEXT_PHOTO_BUTTONS if name in labels), None)


def newest_photo(photos: Iterable[ViewerPhoto]) -> PhotoDates:
    """Newest capture date and count of the dated photos, Street View and 360° images left out.

    Args:
        photos: Photos read in the viewer.

    Returns:
        The newest date (None without any dated photo) and the number of dated photos.
    """
    dates = [photo.taken for photo in photos if photo.taken is not None and not photo.street_view]
    return PhotoDates(newest=max(dates, default=None), dated_count=len(dates))


def relative_age_days(text: str) -> float | None:
    """Return the age in days that a relative date such as "vor 2 Wochen" stands for.

    Args:
        text: Text containing a German or English relative date.

    Returns:
        Days, or None when the text holds no relative date.
    """
    match = RELATIVE_GERMAN.search(text) or RELATIVE_ENGLISH.search(text)
    if match is None:
        return None
    amount_text, unit = match.group(1).lower(), match.group(2).lower()
    amount = int(amount_text) if amount_text.isdigit() else 1
    return amount * DAYS_PER_UNIT[unit]


def is_place_page(html: str) -> bool:
    """Tell a single business profile from a result list or an empty search.

    Args:
        html: Page source of a Google Maps page.

    Returns:
        True for a single business profile, False for a result list or an empty search.
    """
    soup = BeautifulSoup(html, "lxml")
    return soup.select_one('div[role="feed"]') is None and _main(soup).select_one("h1") is not None


def tab_labels(html: str) -> list[str]:
    """Read the tab labels of a place page, e.g. "Übersicht", "Rezensionen".

    Args:
        html: Page source of a Google Maps place page.

    Returns:
        The labels in page order; empty when the page has no tabs.
    """
    soup = BeautifulSoup(html, "lxml")
    return [
        label
        for element in soup.select('[role="tab"]')
        if (label := _label(element) or collapse_whitespace(element.get_text(" ")))
    ]


def place_id_from(url: str, html: str = "") -> str | None:
    """Find the place id of a Maps address or page.

    Args:
        url: Address of the Maps page.
        html: Page source, searched when the address carries no id.

    Returns:
        The ChIJ place id, else the feature id, else None.
    """
    match = CHIJ_PATTERN.search(url) or CHIJ_PATTERN.search(html)
    if match is not None:
        return match.group(0)
    feature = FEATURE_ID_PATTERN.search(url) or FEATURE_ID_PATTERN.search(html)
    return feature.group(1) if feature is not None else None


def parse_listings(html: str) -> list[MapListing]:
    """Return the organic entries of a Maps result list in order; sponsored entries are dropped.

    Args:
        html: Source of a Maps search page.

    Returns:
        Listings in Google's order, each business once; a business marked "Keine Rezensionen"
        has a review count of 0, one whose count is not shown None.
    """
    soup = BeautifulSoup(html, "lxml")
    feed = soup.select_one('div[role="feed"]')
    if feed is None:
        return []
    listings: list[MapListing] = []
    seen: set[str] = set()
    for anchor in feed.select('a[href*="/maps/place/"]'):
        url = _attribute(anchor, "href")
        container = anchor.find_parent(attrs={"role": "article"}) or anchor.parent or anchor
        name = _label(anchor) or _label(container)
        if not name or url in seen or _is_sponsored(container):
            continue
        seen.add(url)
        labels = list(_labels(container))
        review_count = _first_count(labels, REVIEW_COUNT_PATTERN)
        if review_count is None and any(
            collapse_whitespace(str(node)) in NO_REVIEWS_LABELS for node in _nodes(container)
        ):
            review_count = 0
        listings.append(
            MapListing(
                name=name,
                url=url,
                place_id=place_id_from(url),
                rating=_first_number(labels, STARS_PATTERN),
                review_count=review_count,
            )
        )
    return listings


def listing_position(listings: list[MapListing], name: str, place_ids: Iterable[str]) -> int | None:
    """Return the 1-based position of a business in a result list, matched by id, then by name.

    Args:
        listings: Organic entries in order.
        name: Business name; matches when one normalised name contains the other.
        place_ids: Known ChIJ or feature ids of the business.

    Returns:
        The position, or None when the business is not listed.
    """
    known = set(place_ids)
    for position, listing in enumerate(listings, start=1):
        if listing.place_id is not None and listing.place_id in known:
            return position
    for position, listing in enumerate(listings, start=1):
        if same_business(listing.name, name):
            return position
    return None


def same_business(left: str, right: str) -> bool:
    """Compare two business names after normalisation.

    Args:
        left: One business name.
        right: The other business name.

    Returns:
        True if both are non-empty and one normalised name contains the other.
    """
    first, second = normalise_name(left), normalise_name(right)
    return bool(first) and bool(second) and (first in second or second in first)


def parse_place(
    overview_html: str,
    url: str,
    now: datetime,
    *,
    reviews_html: str | None = None,
    about_html: str | None = None,
    photos: PhotoDates | None = None,
) -> PlaceSnapshot:
    """Build a snapshot from a place page and, where they were opened, its review and info tabs.

    Args:
        overview_html: Source of the place page ("Übersicht").
        url: Address of the place page.
        now: Moment the pages were read; relative review dates count back from it.
        reviews_html: Source after opening "Rezensionen" sorted by newest, or None.
        about_html: Source after opening "Info", or None.
        photos: Capture dates read in the photo viewer, or None when it was not read.

    Returns:
        The snapshot; fields that none of the given pages show stay unread (None) and get no
        route.
    """
    soup = BeautifulSoup(overview_html, "lxml")
    main = _main(soup)
    about = _main(BeautifulSoup(about_html, "lxml")) if about_html is not None else None
    values = _overview_values(soup, main, overview_html, url)
    values["description"] = _place_description(main, about)
    if reviews_html is not None:
        values["recent_reviews"] = parse_reviews(reviews_html, now)
    if about is not None:
        values["attributes"] = _attributes(about)
    if photos is not None:
        values["photo_newest_date"] = photos.newest
        values["photo_dated_count"] = photos.dated_count
    read = {field for field, value in values.items() if value is not None}
    return PlaceSnapshot.model_validate(
        {
            "types": [values["primary_type"]] if values["primary_type"] else [],
            "recent_reviews": [],
            **values,
            "value_routes": dict.fromkeys(sorted(read), BROWSER),
        }
    )


def _overview_values(
    soup: BeautifulSoup, main: Tag, overview_html: str, url: str
) -> dict[str, object]:
    canonical = soup.select_one('link[rel="canonical"]')
    page_address = _attribute(canonical, "href") if canonical is not None else ""
    labels = list(_labels(main))
    return {
        "place_id": place_id_from(f"{url} {page_address}", overview_html) or url,
        "name": _text(main.select_one("h1")),
        "formatted_address": _item_value(main, '[data-item-id="address"]'),
        "phone": _item_value(main, '[data-item-id^="phone:tel:"]') or None,
        "website": _website(main),
        "primary_type": _text(main.select_one('button[jsaction*="category"]')) or None,
        "rating": _first_number(labels, STARS_PATTERN),
        "review_count": _first_count(labels, REVIEW_COUNT_PATTERN) or 0,
        "has_opening_hours": _has_opening_hours(main),
        "photo_count": _photo_count(main, labels),
        "business_status": _status(main),
        "google_maps_uri": parse_http_url(url),
        "services": _section_items(main, SERVICE_HEADINGS),
        "products": _section_items(main, PRODUCT_HEADINGS),
        "service_areas": _service_areas(labels),
        "post_ages_days": _post_ages(main),
        "question_count": _question_count(main),
    }


def _place_description(main: Tag, about: Tag | None) -> str | None:
    """The owner's description; an opened info tab without one gives "" (read, but empty)."""
    description = _description(main) or (_description(about) if about is not None else None)
    if description is None and about is not None:
        return ""
    return description


def parse_reviews(html: str, now: datetime) -> list[dict[str, object]]:
    """Return the reviews of an opened "Rezensionen" tab in page order.

    Every review carries reviewReply, None when the owner did not answer, so the reply checks
    can tell an unanswered review from a source without replies.

    Args:
        html: Source of the review tab.
        now: Moment the page was read.

    Returns:
        Reviews with publishTime, relativePublishTimeDescription, rating, text and reviewReply.
    """
    soup = BeautifulSoup(html, "lxml")
    reviews: list[dict[str, object]] = []
    seen: set[str] = set()
    for block in soup.select("div[data-review-id][aria-label]"):
        review_id = _attribute(block, "data-review-id")
        if review_id in seen or block.find_parent(attrs={"data-review-id": True}) is not None:
            continue
        seen.add(review_id)
        reviews.append(_review(block, now))
    return reviews


def _review(block: Tag, now: datetime) -> dict[str, object]:
    reply_block = _reply_block(block)
    inside_reply = set(reply_block.find_all(True)) if reply_block is not None else set()
    own_parts = [element for element in block.find_all(True) if element not in inside_reply]
    relative = _relative_text(
        element for element in own_parts if REVIEW_DATE_CLASS in _classes(element)
    ) or _relative_text(own_parts)
    text = next(
        (_text(element) for element in own_parts if REVIEW_TEXT_CLASS in _classes(element)), ""
    )
    return {
        "publishTime": _moment(now, relative),
        "relativePublishTimeDescription": relative,
        "rating": _first_number(_labels(block), STARS_PATTERN),
        "text": text,
        "reviewReply": _reply(reply_block, now) if reply_block is not None else None,
    }


def _reply(block: Tag, now: datetime) -> dict[str, object]:
    relative = _relative_text(block.find_all(True))
    texts = [_text(element) for element in block.find_all(class_=REVIEW_TEXT_CLASS)]
    return {
        "text": next((text for text in texts if text), ""),
        "publishTime": _moment(now, relative),
        "relativePublishTimeDescription": relative,
    }


def _reply_block(block: Tag) -> Tag | None:
    explicit = block.find(class_=REPLY_CLASS)
    if isinstance(explicit, Tag):
        return explicit
    for node in block.find_all(string=True):
        if collapse_whitespace(str(node)) not in OWNER_REPLY_LABELS:
            continue
        current = node.parent
        for _ in range(MAX_REPLY_CLIMB):
            if current is None or current is block:
                break
            if current.find(class_=REVIEW_TEXT_CLASS) is not None:
                return current
            current = current.parent
    return None


def _relative_text(elements: Iterable[Tag]) -> str | None:
    for element in elements:
        for node in element.find_all(string=True, recursive=False):
            text = collapse_whitespace(str(node))
            if text and relative_age_days(text) is not None:
                return text
    return None


def _moment(now: datetime, relative: str | None) -> str | None:
    age = relative_age_days(relative) if relative else None
    return (now - timedelta(days=age)).isoformat() if age is not None else None


def _viewer_texts(html: str) -> list[str]:
    """Short texts and labels of a page in order, without tabs, reviews, scripts and styles."""
    soup = BeautifulSoup(html, "lxml")
    for element in soup.select(VIEWER_EXCLUDED):
        element.decompose()
    texts: list[str] = []
    for element in soup.find_all(True):
        if element.name in ("script", "style"):
            continue
        label = _label(element)
        if label and len(label) <= MAX_DATE_TEXT_CHARS:
            texts.append(label)
        for node in element.find_all(string=True, recursive=False):
            text = collapse_whitespace(str(node))
            if text and len(text) <= MAX_DATE_TEXT_CHARS:
                texts.append(text)
    return texts


def _main(soup: BeautifulSoup) -> Tag:
    main = soup.select_one('div[role="main"]')
    return main if main is not None else soup


def _description(scope: Tag) -> str | None:
    for heading, container in _sections(scope):
        if heading.startswith(DESCRIPTION_HEADINGS) and not heading.startswith(POST_HEADINGS):
            text = collapse_whitespace(container.get_text(" "))
            return collapse_whitespace(text.removeprefix(heading))
    return None


def _section_items(scope: Tag, headings: tuple[str, ...]) -> list[str]:
    for heading, container in _sections(scope):
        if heading in headings:
            return _items(container)
    return []


def _attributes(scope: Tag) -> list[str]:
    skipped = (*DESCRIPTION_HEADINGS, *SERVICE_HEADINGS, *PRODUCT_HEADINGS)
    attributes: list[str] = []
    for heading, container in _sections(scope):
        if heading.startswith(skipped):
            continue
        attributes.extend(item for item in _items(container) if item not in attributes)
    return attributes


def _post_ages(scope: Tag) -> list[int]:
    for heading, container in _sections(scope):
        if heading in POST_HEADINGS:
            ages = [
                round(age)
                for node in container.find_all(string=True)
                if (age := relative_age_days(collapse_whitespace(str(node)))) is not None
            ]
            return sorted(ages)
    return []


def _question_count(scope: Tag) -> int | None:
    for heading, container in _sections(scope):
        if heading in QUESTION_HEADINGS:
            counted = _first_count(_strings(container), QUESTION_COUNT_PATTERN)
            return counted if counted is not None else len(container.select("li"))
    return None


def _service_areas(labels: list[str]) -> list[str]:
    for label in labels:
        for prefix in SERVICE_AREA_PREFIXES:
            if label.startswith(prefix):
                return [
                    area for part in label.removeprefix(prefix).split(",") if (area := part.strip())
                ]
    return []


def _sections(scope: Tag) -> Iterator[tuple[str, Tag]]:
    for heading in scope.select("h2, h3, [role='heading']"):
        text = collapse_whitespace(heading.get_text(" "))
        if text and isinstance(heading.parent, Tag):
            yield text, heading.parent


def _items(container: Tag) -> list[str]:
    items: list[str] = []
    for element in container.select("li, [role='listitem']"):
        labelled = element.select_one("[aria-label]")
        text = (_label(labelled) if labelled is not None else "") or _text(element)
        if text and text not in items:
            items.append(text)
    return items


def _website(main: Tag) -> HttpUrl | None:
    link = main.select_one('a[data-item-id="authority"]')
    href = _attribute(link, "href") if link is not None else ""
    return parse_http_url(href) if href else None


def _has_opening_hours(main: Tag) -> bool:
    for row in main.select("tr"):
        first = row.find(["td", "th"])
        if first is not None and _text(first).startswith(WEEKDAYS):
            return True
    return any("Öffnungszeiten" in label and "Montag" in label for label in _labels(main))


def _photo_count(main: Tag, labels: list[str]) -> int:
    counted = _first_count([*labels, *_strings(main)], PHOTO_COUNT_PATTERN)
    if counted is not None:
        return counted
    return len({_label(button) for button in main.select('button[aria-label^="Foto"]')})


def _status(main: Tag) -> str:
    text = collapse_whitespace(main.get_text(" "))
    return next((status for label, status in CLOSED_LABELS.items() if label in text), OPERATIONAL)


def _item_value(main: Tag, selector: str) -> str:
    element = main.select_one(selector)
    if element is None:
        return ""
    value = _label(element) or _text(element)
    for prefix in LABEL_PREFIXES:
        value = value.removeprefix(prefix)
    return value.strip()


def _is_sponsored(container: Tag) -> bool:
    return any(collapse_whitespace(str(node)) in SPONSORED_LABELS for node in _nodes(container))


def _first_number(labels: Iterable[str], pattern: re.Pattern[str]) -> float | None:
    for label in labels:
        match = pattern.search(label)
        if match is not None:
            return float(match.group(1).replace(",", "."))
    return None


def _first_count(labels: Iterable[str], pattern: re.Pattern[str]) -> int | None:
    for label in labels:
        match = pattern.search(label)
        if match is not None:
            return int(match.group(1).replace(".", ""))
    return None


def _labels(scope: Tag) -> Iterator[str]:
    for element in [scope, *scope.select("[aria-label]")]:
        label = _label(element)
        if label:
            yield label


def _strings(scope: Tag) -> list[str]:
    return [text for node in _nodes(scope) if (text := collapse_whitespace(str(node)))]


def _nodes(scope: Tag) -> Iterator[NavigableString]:
    for node in scope.find_all(string=True):
        if isinstance(node, NavigableString) and not (
            isinstance(node.parent, Tag) and node.parent.name in ("script", "style")
        ):
            yield node


def _label(element: Tag | None) -> str:
    return collapse_whitespace(_attribute(element, "aria-label")) if element is not None else ""


def _text(element: Tag | None) -> str:
    return collapse_whitespace(element.get_text(" ")) if element is not None else ""


def _classes(element: Tag) -> list[str]:
    value = element.get("class")
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _attribute(tag: Tag | None, name: str) -> str:
    if tag is None:
        return ""
    value = tag.get(name)
    if value is None:
        return ""
    return value if isinstance(value, str) else " ".join(value)
