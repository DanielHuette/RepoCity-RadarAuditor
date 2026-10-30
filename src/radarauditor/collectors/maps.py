"""Read Google Maps in the shared server browser: business profiles and result lists.

Context: Free source for the Google profile checks (areas f and g), competitor discovery, the map
position per keyword and the map grid. One call opens one tab of the shared BrowserService; a
bot check raises SearchBlockedError, which the profile route answers with the Places API.
Input: The shared BrowserService, a profile to read or a phrase with an optional position.
Output: PlaceSnapshot (overview, newest reviews with owner replies, info tab and, for the
customer's own profile, the capture dates of its photos) or MapListings.
"""

import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, quote_plus

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from radarauditor.collectors.browser import BrowserService, Geolocation, ensure_not_blocked
from radarauditor.collectors.maps_pages import (
    MapListing,
    PhotoDates,
    ViewerPhoto,
    gallery_tab,
    is_place_page,
    newest_photo,
    next_photo_button,
    page_texts,
    parse_listings,
    parse_place,
    photo_button,
    same_business,
    tab_labels,
    viewer_photo,
)
from radarauditor.collectors.snapshot import PlaceSnapshot
from radarauditor.errors import CollectionError, ProfileNotFoundError

logger = logging.getLogger(__name__)

MAPS_URL = "https://www.google.com/maps"
SEARCH_URL = MAPS_URL + "/search/{query}?hl=de&gl=de"
SEARCH_AT_URL = MAPS_URL + "/search/{query}/@{latitude:.7f},{longitude:.7f},{zoom}z?hl=de&gl=de"
PLACE_ID_URL = MAPS_URL + "/place/?q=place_id:{place_id}&hl=de&gl=de"
SEARCH_ZOOM = 14
READY_SELECTOR = "div[role='main'] h1, div[role='feed'], div[role='main']"
READY_TIMEOUT_MS = 15000
TAB_TIMEOUT_MS = 8000
REVIEW_SELECTOR = "div[data-review-id]"
REVIEW_TABS = ("Rezensionen", "Reviews")
ABOUT_TABS = ("Info", "About")
OVERVIEW_TABS = ("Übersicht", "Overview")
SORT_BUTTONS = ("Rezensionen sortieren", "Sort reviews")
# The sort menu has used several roles; any entry labelled "Neueste" is the date order.
NEWEST_SELECTOR = ", ".join(
    f'[role="{role}"]:has-text("{label}")'
    for role in ("menuitemradio", "menuitem", "option")
    for label in ("Neueste", "Newest")
)
CONTROL_TIMEOUT_MS = 5000
REVIEW_SCROLLS = 2
SCROLL_REVIEWS_SCRIPT = (
    "(() => { const review = document.querySelector('div[data-review-id]');"
    " let pane = review && review.parentElement;"
    " while (pane && pane.scrollHeight <= pane.clientHeight) { pane = pane.parentElement; }"
    " if (pane) { pane.scrollBy(0, pane.scrollHeight); } })()"
)
PHOTO_LIMIT = 20
VIEWER_READY_SELECTOR = (
    '[role="tablist"], [aria-label="Nächstes Foto"], [aria-label="Next photo"], '
    '[aria-label="Nächstes Bild"]'
)
EXPAND_REVIEWS_SCRIPT = (
    "document.querySelectorAll('div[data-review-id] button[aria-expanded=\"false\"]')"
    ".forEach((button) => button.click())"
)


@dataclass(frozen=True)
class ProfileTarget:
    """A business to open on Google Maps: by place page address, place id, or name and address.

    with_photos opens the photo viewer to read the capture dates of the photos; it takes up to
    PHOTO_LIMIT clicks, so only the customer's own profile asks for it.
    """

    name: str
    address: str = ""
    place_id: str | None = None
    url: str | None = None
    with_photos: bool = False

    def start_url(self) -> str:
        """Address that opens the profile directly or a search that lists it."""
        if self.url:
            return self.url
        if self.place_id and self.place_id.startswith("ChIJ"):
            return PLACE_ID_URL.format(place_id=quote(self.place_id, safe=""))
        query = f"{self.name} {self.address}".strip()
        return SEARCH_URL.format(query=quote_plus(query))


async def read_profile(browser: BrowserService, target: ProfileTarget) -> PlaceSnapshot:
    """Open a business profile, its newest reviews, its info tab and its photos, and parse them.

    The photos are read only when the target asks for them: the viewer opens on "Vom Inhaber"
    when the owner uploaded photos, else on "Alle", and steps through up to PHOTO_LIMIT photos.
    A viewer that cannot be operated leaves the photo dates unread.

    Args:
        browser: Shared browser of the job.
        target: Business to open.

    Returns:
        The profile as read from Google Maps.

    Raises:
        SearchBlockedError: On a captcha, "unusual traffic" page or HTTP 429.
        ProfileNotFoundError: If Google Maps shows no profile matching the business.
        CollectionError: If the page shows neither a profile nor a result list.
        playwright.async_api.Error: On a navigation failure or timeout.
    """
    async with browser.page() as page:
        html = await _load(browser, page, target.start_url())
        if not is_place_page(html):
            listing = _matching_listing(parse_listings(html), target)
            await browser.linger(page)
            html = await _load(browser, page, listing.url)
            if not is_place_page(html):
                raise CollectionError(f"Google Maps shows no readable profile for {target.name}")
        url = page.url
        reviews_html = await _tab_or_none(_reviews(browser, page, html), "reviews")
        about_html = await _tab_or_none(
            _open_tab(browser, page, html, ABOUT_TABS, "div[role='main'] h2"), "info"
        )
        photos = (
            await _tab_or_none(_photos(browser, page, html), "photos")
            if target.with_photos
            else None
        )
    logger.info("Read the Google Maps profile of %s", target.name)
    return parse_place(
        html,
        url,
        datetime.now(UTC),
        reviews_html=reviews_html,
        about_html=about_html,
        photos=photos,
    )


async def search_listings(
    browser: BrowserService, phrase: str, at: Geolocation | None = None
) -> list[MapListing]:
    """Search Google Maps for a phrase and return the organic result list.

    With a position, the map is centred there and the tab reports it as the device location,
    which is how a person standing at that point would search.

    Args:
        browser: Shared browser of the job.
        phrase: Search phrase, for example "Dachdecker Münster".
        at: Position to search from, or None for no position.

    Returns:
        Listings in Google's order; a search that jumps straight to one profile returns that one.

    Raises:
        SearchBlockedError: On a captcha, "unusual traffic" page or HTTP 429.
        playwright.async_api.Error: On a navigation failure or timeout.
    """
    query = quote(phrase, safe="")
    url = (
        SEARCH_URL.format(query=query)
        if at is None
        else SEARCH_AT_URL.format(
            query=query, latitude=at.latitude, longitude=at.longitude, zoom=SEARCH_ZOOM
        )
    )
    async with browser.page(geolocation=at) as page:
        html = await _load(browser, page, url)
        if is_place_page(html):
            snapshot = parse_place(html, page.url, datetime.now(UTC))
            return [
                MapListing(
                    name=snapshot.name,
                    url=page.url,
                    place_id=snapshot.place_id,
                    rating=snapshot.rating,
                    review_count=snapshot.review_count,
                )
            ]
        await browser.linger(page)
        return parse_listings(await _content(page))


def _matching_listing(listings: list[MapListing], target: ProfileTarget) -> MapListing:
    for listing in listings:
        if (target.place_id and listing.place_id == target.place_id) or same_business(
            listing.name, target.name
        ):
            return listing
    raise ProfileNotFoundError(f"Google Maps lists no profile for {target.name}")


async def _load(browser: BrowserService, page: Page, url: str) -> str:
    await browser.open(page, url)
    await _wait(page, READY_SELECTOR, READY_TIMEOUT_MS)
    return await _content(page)


async def _content(page: Page) -> str:
    html = await page.content()
    ensure_not_blocked(page.url, html)
    return html


async def _wait(page: Page, selector: str, timeout_ms: int) -> None:
    try:
        await page.wait_for_selector(selector, timeout=timeout_ms)
    except PlaywrightError as error:
        # A missing element shows up as missing data; the parser decides what that means.
        logger.debug("Waited in vain for %s: %s", selector, error.message)


async def _tab_or_none[T](reading: Awaitable[T | None], tab: str) -> T | None:
    """A tab that cannot be operated leaves its fields unread instead of losing the profile."""
    try:
        return await reading
    except PlaywrightError as error:
        logger.warning("Google Maps %s tab not read: %s", tab, error.message)
        return None


async def _reviews(browser: BrowserService, page: Page, html: str) -> str | None:
    opened = await _open_tab(browser, page, html, REVIEW_TABS, REVIEW_SELECTOR)
    if opened is None:
        return None
    await _sort_newest(page, opened)
    for _ in range(REVIEW_SCROLLS):
        await page.evaluate(SCROLL_REVIEWS_SCRIPT)
        await browser.linger(page)
    await page.evaluate(EXPAND_REVIEWS_SCRIPT)
    return await _content(page)


async def _open_tab(
    browser: BrowserService, page: Page, html: str, labels: tuple[str, ...], ready: str
) -> str | None:
    offered = tab_labels(html)
    label = next((label for label in labels if any(tab.startswith(label) for tab in offered)), None)
    if label is None:
        return None
    await page.get_by_role("tab", name=label).first.click()
    await _wait(page, ready, TAB_TIMEOUT_MS)
    await browser.linger(page)
    return await _content(page)


async def _photos(browser: BrowserService, page: Page, overview_html: str) -> PhotoDates | None:
    """Open the photo viewer and read the capture date of up to PHOTO_LIMIT photos.

    Returns:
        The newest date and the count of dated photos, Street View and 360° images left out;
        None when the profile shows no photo control or the viewer did not open.
    """
    now = datetime.now(UTC)
    opened = await _open_viewer(browser, page, overview_html)
    if opened is None:
        return None
    viewer, before = opened
    seen = await _read_viewer(browser, page, viewer, before, now)
    photos = newest_photo(seen)
    logger.info(
        "Read %d photos in the Google Maps viewer, %d with a date", len(seen), photos.dated_count
    )
    return photos


async def _open_viewer(
    browser: BrowserService, page: Page, overview_html: str
) -> tuple[str, frozenset[str]] | None:
    """Open the photo viewer on its gallery tab.

    Returns:
        The viewer's page source and the texts the page showed before it opened; None when the
        profile shows no photo control or the viewer did not open.
    """
    current = await _content(page)
    if photo_button(current) is None and photo_button(overview_html) is not None:
        # The review and info tabs may hide the photo control; the overview tab shows it.
        current = await _open_tab(browser, page, current, OVERVIEW_TABS, READY_SELECTOR) or current
    label = photo_button(current)
    if label is None:
        return None
    before = page_texts(current)
    await page.get_by_role("button", name=label).first.click(timeout=CONTROL_TIMEOUT_MS)
    await _wait(page, VIEWER_READY_SELECTOR, TAB_TIMEOUT_MS)
    await browser.rest()
    viewer = await _content(page)
    if viewer == current:
        logger.warning("Google Maps photo viewer did not open")
        return None
    tab = gallery_tab(viewer)
    if tab is not None:
        await page.get_by_role("tab", name=tab).first.click(timeout=CONTROL_TIMEOUT_MS)
        await _wait(page, VIEWER_READY_SELECTOR, TAB_TIMEOUT_MS)
        await browser.rest()
        viewer = await _content(page)
    return viewer, before


async def _read_viewer(
    browser: BrowserService, page: Page, viewer: str, before: frozenset[str], now: datetime
) -> list[ViewerPhoto]:
    """Read the shown photo and step to the next one until PHOTO_LIMIT or the last photo."""
    seen = [viewer_photo(viewer, before, now)]
    while len(seen) < PHOTO_LIMIT:
        next_label = next_photo_button(viewer)
        if next_label is None:
            break
        await page.get_by_role("button", name=next_label).first.click(timeout=CONTROL_TIMEOUT_MS)
        await browser.rest()
        shown = await _content(page)
        if shown == viewer:
            break
        viewer = shown
        seen.append(viewer_photo(viewer, before, now))
    return seen


async def _sort_newest(page: Page, html: str) -> bool:
    """Sort the reviews by date; on failure they keep Google's order of relevance."""
    button = next((label for label in SORT_BUTTONS if label in html), None)
    if button is None:
        return False
    try:
        await page.get_by_role("button", name=button).first.click(timeout=CONTROL_TIMEOUT_MS)
        await page.locator(NEWEST_SELECTOR).first.click(timeout=CONTROL_TIMEOUT_MS)
    except PlaywrightError as error:
        logger.warning("Google Maps reviews not sorted by newest: %s", error.message)
        return False
    await _wait(page, REVIEW_SELECTOR, TAB_TIMEOUT_MS)
    return True
