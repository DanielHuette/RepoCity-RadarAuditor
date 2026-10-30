"""Crawl the customer's website: pages, meta data, structure, legal pages, NAP.

Context: First collector; limited to the same host and a page budget.
Input: Start URL.
Output: WebsiteSnapshot.
"""

import asyncio
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TypedDict
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from radarauditor.collectors.patterns import (
    POSTAL_CITY_PATTERN,
    collapse_whitespace,
    find_address,
    find_phone,
    parse_http_url,
    site_key,
)
from radarauditor.collectors.snapshot import PageSnapshot, WebsiteSnapshot
from radarauditor.collectors.transport import USER_AGENT, send
from radarauditor.collectors.web_tools import ads_conversions, google_tag_ids, inline_signatures

logger = logging.getLogger(__name__)

MAX_PAGES = 60
REQUEST_TIMEOUT_SECONDS = 20
CONCURRENT_REQUESTS = 4
POLITE_DELAY_SECONDS = 0.5
TEXT_EXCERPT_CHARS = 600
# The privacy policy names its services far below the first paragraph, so it keeps more text.
PRIVACY_EXCERPT_CHARS = 20000
IMAGE_HEAD_LIMIT = 20
OVERSIZED_IMAGE_BYTES = 300 * 1024
NAP_SOURCE_PAGES = 3
OPENING_HOURS_LINES = 7

NON_HTML_SUFFIXES = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".zip",
    ".mp4",
    ".mp3",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ics",
    ".vcf",
)
INVISIBLE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe")
LEGAL_NOTICE_PATTERN = re.compile(r"impressum|imprint|legal[-_ ]?notice", re.IGNORECASE)
PRIVACY_PATTERN = re.compile(r"datenschutz|privacy", re.IGNORECASE)
CONTACT_PATTERN = re.compile(r"kontakt|contact|anfahrt", re.IGNORECASE)
FOOTER_HINT_PATTERN = re.compile(r"footer|contact|kontakt|address|adresse|anschrift", re.IGNORECASE)
WEEKDAY = r"(?:Mo|Di|Mi|Do|Fr|Sa|So|Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag)"
OPENING_HOURS_PATTERN = re.compile(rf"\b{WEEKDAY}\b\.?[^\n]{{0,80}}?\d{{1,2}}[:.]\d{{2}}")
VIEWPORT_NAME_PATTERN = re.compile(r"^viewport$", re.IGNORECASE)
STYLE_SOURCE_PATTERN = re.compile(
    r"@import\s+(?:url\(\s*)?['\"]?([^'\")\s;]+)|@font-face\s*\{([^}]*)\}", re.IGNORECASE
)
CSS_URL_PATTERN = re.compile(r"url\(\s*['\"]?([^'\")\s]+)", re.IGNORECASE)
BLOCK_LABEL_PATTERN = re.compile(
    r"^(kontakt|contact|adresse|anschrift|address|impressum|so erreichen sie uns|"
    r"öffnungszeiten)\s*:?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ParsedPage:
    snapshot: PageSnapshot
    image_urls: list[str]
    json_ld_errors: list[str]
    visible_lines: list[str]


@dataclass
class _CrawlState:
    pages: list[PageSnapshot] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    nap_sources: list[str] = field(default_factory=list)
    opening_hours: str | None = None
    image_sizes: dict[str, int | None] = field(default_factory=dict)
    recorded_urls: set[str] = field(default_factory=set)


async def crawl_website(start_url: HttpUrl, max_pages: int = MAX_PAGES) -> WebsiteSnapshot:
    """Fetch the start page and follow internal links breadth-first up to max_pages.

    Args:
        start_url: Public address of the website.
        max_pages: Upper bound of pages to fetch.

    Returns:
        Site-wide facts and one PageSnapshot per fetched page.

    Raises:
        CollectionError: If the start page cannot be fetched.
    """
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        start_response = await send(client, "GET", str(start_url), source="website start page")
        final = urlsplit(str(start_response.url))
        origin = f"{final.scheme}://{final.netloc}"
        robots_txt = await _fetch_text(client, f"{origin}/robots.txt")
        robots = _robot_rules(robots_txt)
        state = await _crawl_pages(client, start_response, robots, max_pages)
        sitemap_urls = await _sitemap_urls(client, origin, robots)
    single_host = await asyncio.to_thread(check_single_host_redirects, start_url)
    legal_notice_url, privacy_url = find_legal_pages(state.pages)
    return WebsiteSnapshot(
        start_url=start_url,
        uses_https=final.scheme == "https",
        redirects_to_single_host=single_host,
        robots_txt=robots_txt,
        sitemap_urls=sitemap_urls,
        legal_notice_url=legal_notice_url,
        privacy_url=privacy_url,
        footer_nap=_merge_nap(state.nap_sources),
        opening_hours_text=state.opening_hours,
        pages=state.pages,
        crawl_errors=state.errors,
    )


async def _crawl_pages(
    client: httpx.AsyncClient,
    start_response: httpx.Response,
    robots: RobotFileParser,
    max_pages: int,
) -> _CrawlState:
    state = _CrawlState()
    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
    start_url = _strip_fragment(str(start_response.url))
    home_site = site_key(urlsplit(start_url).hostname or "")
    visited = {start_url}
    await _record_page(client, semaphore, state, start_response, is_start=True)
    frontier = _new_links(state.pages[-1:], visited, home_site, robots)
    while frontier and len(state.pages) < max_pages:
        batch = frontier[: max_pages - len(state.pages)]
        frontier = frontier[len(batch) :]
        responses = await asyncio.gather(*(_fetch(client, semaphore, url) for url in batch))
        before = len(state.pages)
        for url, outcome in zip(batch, responses, strict=True):
            if isinstance(outcome, str):
                state.errors.append(f"{url}: {outcome}")
                continue
            final_url = _strip_fragment(str(outcome.url))
            if final_url in state.recorded_urls or site_key(outcome.url.host) != home_site:
                continue
            if not _is_html(outcome):
                continue
            await _record_page(client, semaphore, state, outcome, is_start=False)
        frontier.extend(_new_links(state.pages[before:], visited, home_site, robots))
    return state


async def _record_page(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    state: _CrawlState,
    response: httpx.Response,
    *,
    is_start: bool,
) -> None:
    url = _strip_fragment(str(response.url))
    page_url = parse_http_url(url)
    if page_url is None:
        state.errors.append(f"{url}: not a valid http(s) address")
        return
    state.recorded_urls.add(url)
    html = response.text
    parsed = _parse(page_url, response.status_code, html)
    oversized = await _count_oversized(client, semaphore, parsed.image_urls, state.image_sizes)
    state.pages.append(parsed.snapshot.model_copy(update={"oversized_images": oversized}))
    state.errors.extend(f"{url}: {error}" for error in parsed.json_ld_errors)
    is_nap_page = is_start or bool(LEGAL_NOTICE_PATTERN.search(url) or CONTACT_PATTERN.search(url))
    if is_nap_page and len(state.nap_sources) < NAP_SOURCE_PAGES:
        state.nap_sources.append(html)
    if state.opening_hours is None:
        state.opening_hours = extract_opening_hours(parsed.visible_lines)


async def _fetch(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, url: str
) -> httpx.Response | str:
    async with semaphore:
        try:
            return await client.get(url)
        except httpx.HTTPError as error:
            return type(error).__name__
        finally:
            await asyncio.sleep(POLITE_DELAY_SECONDS)


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        response = await client.get(url)
    except httpx.HTTPError as error:
        logger.info("Could not read %s: %s", url, type(error).__name__)
        return None
    if response.status_code != httpx.codes.OK:
        return None
    return response.text


async def _sitemap_urls(
    client: httpx.AsyncClient, origin: str, robots: RobotFileParser
) -> list[HttpUrl]:
    candidates = list(robots.site_maps() or [])
    default_sitemap = f"{origin}/sitemap.xml"
    if default_sitemap not in candidates:
        try:
            response = await client.get(default_sitemap)
        except httpx.HTTPError as error:
            logger.info("Could not read %s: %s", default_sitemap, type(error).__name__)
        else:
            if response.status_code == httpx.codes.OK:
                candidates.append(default_sitemap)
    return [url for url in (parse_http_url(item) for item in candidates) if url is not None]


async def _count_oversized(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    image_urls: list[str],
    sizes: dict[str, int | None],
) -> int:
    pending = [url for url in image_urls[:IMAGE_HEAD_LIMIT] if url not in sizes]
    measured = await asyncio.gather(*(_content_length(client, semaphore, url) for url in pending))
    sizes.update(zip(pending, measured, strict=True))
    return sum(
        1
        for url in image_urls[:IMAGE_HEAD_LIMIT]
        if (size := sizes.get(url)) is not None and size > OVERSIZED_IMAGE_BYTES
    )


async def _content_length(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, url: str
) -> int | None:
    async with semaphore:
        try:
            response = await client.head(url)
        except httpx.HTTPError:
            return None
    header = response.headers.get("content-length")
    if response.status_code != httpx.codes.OK or header is None or not header.isdigit():
        return None
    return int(header)


def _robot_rules(robots_txt: str | None) -> RobotFileParser:
    rules = RobotFileParser()
    rules.parse((robots_txt or "").splitlines())
    return rules


def _new_links(
    pages: Iterable[PageSnapshot],
    visited: set[str],
    home_site: str,
    robots: RobotFileParser,
) -> list[str]:
    queued: list[str] = []
    for page in pages:
        for link in page.internal_links:
            url = str(link)
            if url in visited or site_key(link.host or "") != home_site:
                continue
            visited.add(url)
            if urlsplit(url).path.lower().endswith(NON_HTML_SUFFIXES):
                continue
            if not robots.can_fetch(USER_AGENT, url):
                logger.debug("robots.txt disallows %s", url)
                continue
            queued.append(url)
    return queued


def _is_html(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "text/html")
    return "html" in content_type.lower()


def _strip_fragment(url: str) -> str:
    return urldefrag(url).url


def parse_page(url: HttpUrl, status_code: int, html: str) -> PageSnapshot:
    """Extract title, meta, headings, JSON-LD, links, third-party hosts and counts from a page.

    `oversized_images` is 0 here because it needs network requests; the crawler fills it in.
    An image counts as without alt text when the attribute is missing or empty. Links to another
    site go to `external_links`; hosts outside the site that serve scripts, stylesheets, frames,
    images or inline font faces go to `third_party_hosts`. External script addresses go to
    `script_sources`; the chat, analytics and Google Ads markers of inline scripts, the Google tag
    ids and the Google Ads conversion measurements are recorded for the tool checks. A privacy
    page keeps a longer text excerpt so the services it names can be compared with the hosts in
    use.

    Args:
        url: Address the document was fetched from; relative links resolve against it.
        status_code: HTTP status of the response.
        html: Document source.

    Returns:
        The page snapshot.
    """
    return _parse(url, status_code, html).snapshot


def _parse(url: HttpUrl, status_code: int, html: str) -> _ParsedPage:
    soup = BeautifulSoup(html, "lxml")
    base = str(url)
    json_ld, json_ld_errors = _json_ld(soup)
    image_urls, images_without_alt = _images(soup, base)
    links_and_scripts = _links_and_scripts(soup, base)
    head_values = _head_values(soup, base)
    headings = _headings(soup)
    # Reading the visible text removes scripts and styles from the soup, so it comes last.
    lines = _visible_lines(soup)
    text = " ".join(lines)
    snapshot = PageSnapshot(
        url=url,
        status_code=status_code,
        title=head_values.title,
        meta_description=head_values.meta_description,
        canonical=head_values.canonical,
        lang=head_values.lang,
        headings=headings,
        word_count=len(text.split()),
        json_ld=json_ld,
        open_graph=head_values.open_graph,
        images_without_alt=images_without_alt,
        oversized_images=0,
        text_excerpt=text[: _excerpt_chars(base, head_values.title)],
        has_viewport_meta=head_values.has_viewport_meta,
        **links_and_scripts,
    )
    return _ParsedPage(snapshot, image_urls, json_ld_errors, lines)


class _LinksAndScripts(TypedDict):
    """The link and script fields of a PageSnapshot."""

    tel_links: list[str]
    mailto_links: list[str]
    internal_links: list[HttpUrl]
    external_links: list[HttpUrl]
    third_party_hosts: list[str]
    script_sources: list[str]
    inline_script_signatures: list[str]
    google_tag_ids: list[str]
    ads_conversions: list[str]


def _links_and_scripts(soup: BeautifulSoup, base: str) -> _LinksAndScripts:
    tel_links, mailto_links = _contact_links(soup)
    internal_links, external_links = _links(soup, base)
    third_party_hosts = _third_party_hosts(soup, base)
    script_sources, inline_scripts = _scripts(soup, base)
    script_texts = [*inline_scripts, *script_sources]
    return _LinksAndScripts(
        tel_links=tel_links,
        mailto_links=mailto_links,
        internal_links=internal_links,
        external_links=external_links,
        third_party_hosts=third_party_hosts,
        script_sources=script_sources,
        inline_script_signatures=inline_signatures(inline_scripts),
        google_tag_ids=google_tag_ids(script_texts),
        ads_conversions=ads_conversions(script_texts),
    )


def _excerpt_chars(base: str, title: str | None) -> int:
    """Privacy pages keep a longer excerpt than other pages."""
    is_privacy_page = PRIVACY_PATTERN.search(urlsplit(base).path) or PRIVACY_PATTERN.search(
        title or ""
    )
    return PRIVACY_EXCERPT_CHARS if is_privacy_page else TEXT_EXCERPT_CHARS


@dataclass(frozen=True)
class _HeadValues:
    title: str | None
    meta_description: str | None
    canonical: HttpUrl | None
    lang: str | None
    open_graph: dict[str, str]
    has_viewport_meta: bool


def _head_values(soup: BeautifulSoup, base: str) -> _HeadValues:
    title_tag = soup.find("title")
    title = collapse_whitespace(title_tag.get_text()) if title_tag else ""
    description_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    description = _attribute(description_tag, "content")
    canonical_tag = soup.select_one('link[rel~="canonical"][href]')
    canonical_href = _attribute(canonical_tag, "href")
    html_tag = soup.find("html")
    open_graph: dict[str, str] = {}
    for meta in soup.select('meta[property^="og:"]'):
        key, content = _attribute(meta, "property"), _attribute(meta, "content")
        if key and content is not None:
            open_graph[key] = content
    return _HeadValues(
        title=title or None,
        meta_description=collapse_whitespace(description) if description else None,
        canonical=parse_http_url(urljoin(base, canonical_href)) if canonical_href else None,
        lang=_attribute(html_tag, "lang") or None,
        open_graph=open_graph,
        has_viewport_meta=soup.find("meta", attrs={"name": VIEWPORT_NAME_PATTERN}) is not None,
    )


def _headings(soup: BeautifulSoup) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = collapse_whitespace(tag.get_text(" "))
        if text:
            headings.append((int(tag.name[1]), text))
    return headings


def _json_ld(soup: BeautifulSoup) -> tuple[list[dict[str, object]], list[str]]:
    blocks: list[dict[str, object]] = []
    errors: list[str] = []
    scripts = soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)})
    for index, script in enumerate(scripts, start=1):
        try:
            parsed = json.loads(script.get_text())
        except json.JSONDecodeError as error:
            errors.append(f"invalid JSON-LD block {index}: {error.msg} at line {error.lineno}")
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        blocks.extend(item for item in items if isinstance(item, dict))
    return blocks, errors


def _images(soup: BeautifulSoup, base: str) -> tuple[list[str], int]:
    urls: list[str] = []
    without_alt = 0
    for image in soup.find_all("img"):
        alt = _attribute(image, "alt")
        if alt is None or not alt.strip():
            without_alt += 1
        source = _attribute(image, "src") or _attribute(image, "data-src")
        if source and not source.startswith("data:"):
            absolute = urljoin(base, source)
            if absolute.startswith(("http://", "https://")) and absolute not in urls:
                urls.append(absolute)
    return urls, without_alt


def _contact_links(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    tel_links: list[str] = []
    mailto_links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = (_attribute(anchor, "href") or "").strip()
        lowered = href.lower()
        if lowered.startswith("tel:"):
            value = href[4:].strip()
            target = tel_links
        elif lowered.startswith("mailto:"):
            value = href[7:].split("?", 1)[0].strip()
            target = mailto_links
        else:
            continue
        if value and value not in target:
            target.append(value)
    return tel_links, mailto_links


def _links(soup: BeautifulSoup, base: str) -> tuple[list[HttpUrl], list[HttpUrl]]:
    home_site = site_key(urlsplit(base).hostname or "")
    seen: set[str] = set()
    internal: list[HttpUrl] = []
    external: list[HttpUrl] = []
    for anchor in soup.find_all("a", href=True):
        href = (_attribute(anchor, "href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "tel:", "mailto:")):
            continue
        absolute = _strip_fragment(urljoin(base, href))
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        link = parse_http_url(absolute)
        if link is None or str(link) in seen:
            continue
        seen.add(str(link))
        (internal if site_key(parts.hostname) == home_site else external).append(link)
    return internal, external


def _third_party_hosts(soup: BeautifulSoup, base: str) -> list[str]:
    home_site = site_key(urlsplit(base).hostname or "")
    sources = [
        _attribute(tag, "src") or (_attribute(tag, "data-src") if tag.name == "img" else None)
        for tag in soup.find_all(["script", "iframe", "img"])
    ]
    sources.extend(_attribute(tag, "href") for tag in soup.select('link[rel~="stylesheet"][href]'))
    sources.extend(_inline_style_sources(soup))
    hosts: list[str] = []
    for source in sources:
        if not source:
            continue
        host = (urlsplit(urljoin(base, source.strip())).hostname or "").lower()
        own = site_key(host) == home_site or host.endswith(f".{home_site}")
        if host and not own and host not in hosts:
            hosts.append(host)
    return hosts


def _scripts(soup: BeautifulSoup, base: str) -> tuple[list[str], list[str]]:
    """Absolute addresses of external scripts and the text of inline scripts, JSON-LD left out."""
    sources: list[str] = []
    inline: list[str] = []
    for tag in soup.find_all("script"):
        source = (_attribute(tag, "src") or _attribute(tag, "data-src") or "").strip()
        if source:
            absolute = urljoin(base, source)
            if absolute not in sources:
                sources.append(absolute)
            continue
        kind = (_attribute(tag, "type") or "").casefold()
        text = tag.get_text()
        if "json" not in kind and text.strip():
            inline.append(text)
    return sources, inline


def _inline_style_sources(soup: BeautifulSoup) -> list[str]:
    blocks = [tag.get_text() for tag in soup.find_all("style")]
    blocks.extend(style for tag in soup.find_all(style=True) if (style := _attribute(tag, "style")))
    sources: list[str] = []
    for block in blocks:
        for match in STYLE_SOURCE_PATTERN.finditer(block):
            imported, font_face = match.groups()
            if imported:
                sources.append(imported)
            if font_face:
                sources.extend(CSS_URL_PATTERN.findall(font_face))
    return sources


def _visible_lines(soup: BeautifulSoup) -> list[str]:
    root = soup.body or soup
    for tag in root.find_all(INVISIBLE_TAGS):
        tag.decompose()
    lines = (collapse_whitespace(line) for line in root.get_text("\n").splitlines())
    return [line for line in lines if line]


def _attribute(tag: object, name: str) -> str | None:
    if not isinstance(tag, Tag):
        return None
    value = tag.get(name)
    if value is None:
        return None
    return value if isinstance(value, str) else " ".join(value)


def find_legal_pages(pages: list[PageSnapshot]) -> tuple[HttpUrl | None, HttpUrl | None]:
    """Locate the legal notice (Impressum) and privacy policy by link text and URL patterns.

    Crawled page addresses, titles and first headings are checked first, then the internal links
    of every page, so a legal page beyond the crawl budget is still found.

    Args:
        pages: Crawled pages in crawl order.

    Returns:
        Legal notice URL and privacy policy URL, each None if not found.
    """
    return _first_match(pages, LEGAL_NOTICE_PATTERN), _first_match(pages, PRIVACY_PATTERN)


def _first_match(pages: list[PageSnapshot], pattern: re.Pattern[str]) -> HttpUrl | None:
    for page in pages:
        first_heading = page.headings[0][1] if page.headings else ""
        if pattern.search(urlsplit(str(page.url)).path) or pattern.search(
            f"{page.title or ''} {first_heading}"
        ):
            return page.url
    for page in pages:
        for link in page.internal_links:
            if pattern.search(urlsplit(str(link)).path):
                return link
    return None


def extract_footer_nap(html: str) -> dict[str, str]:
    """Read name, street, postal code, city and phone from the footer or a contact block.

    Candidate blocks are tried in this order: `<footer>`, elements whose id or class names a
    footer or contact block, `<address>`, then the whole body. The first block with a postal code
    and city wins; the phone may come from any block.

    Args:
        html: Document source.

    Returns:
        Keys name, street, postal_code, city and phone, each only when found.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(INVISIBLE_TAGS):
        tag.decompose()
    blocks = [_block_lines(block) for block in _nap_blocks(soup)]
    nap: dict[str, str] = {}
    for lines in blocks:
        address = find_address(lines)
        if address:
            nap.update(address)
            name = _name_from_lines(lines)
            if name:
                nap["name"] = name
            phone = find_phone("\n".join(lines))
            if phone:
                nap["phone"] = phone
            break
    if "phone" not in nap:
        for lines in blocks:
            phone = find_phone("\n".join(lines))
            if phone:
                nap["phone"] = phone
                break
    return nap


def _nap_blocks(soup: BeautifulSoup) -> list[Tag]:
    blocks: list[Tag] = list(soup.find_all("footer"))
    for tag in soup.find_all(True):
        hint = f"{_attribute(tag, 'id') or ''} {_attribute(tag, 'class') or ''}"
        if tag.name not in ("footer", "html", "body") and FOOTER_HINT_PATTERN.search(hint):
            blocks.append(tag)
    blocks.extend(soup.find_all("address"))
    if soup.body is not None:
        blocks.append(soup.body)
    return blocks


def _block_lines(block: Tag) -> list[str]:
    lines = (collapse_whitespace(line) for line in block.get_text("\n").splitlines())
    return [line for line in lines if line]


def _name_from_lines(lines: list[str]) -> str | None:
    for line in lines:
        if BLOCK_LABEL_PATTERN.match(line):
            continue
        if POSTAL_CITY_PATTERN.search(line) or find_phone(line):
            return None
        return line
    return None


def _merge_nap(html_sources: list[str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for html in html_sources:
        for key, value in extract_footer_nap(html).items():
            merged.setdefault(key, value)
    return merged


def extract_opening_hours(lines: list[str]) -> str | None:
    """Return the lines that pair a German weekday with a time, joined by newlines.

    Args:
        lines: Visible text lines of a page.

    Returns:
        Up to seven matching lines, or None when the page shows no opening hours.
    """
    matches = [line for line in lines if OPENING_HOURS_PATTERN.search(line)]
    return "\n".join(matches[:OPENING_HOURS_LINES]) if matches else None


def check_single_host_redirects(start_url: HttpUrl) -> bool:
    """Return True if http, https, www and non-www variants all end on one canonical host.

    Variants that cannot be reached at all (for example a www name without a DNS entry) do not
    create duplicate content and are skipped.

    Args:
        start_url: Public address of the website.

    Returns:
        True when every reachable variant ends on the same scheme and host.
    """
    bare_host = site_key(start_url.host or "")
    variants = [
        f"{scheme}://{prefix}{bare_host}/"
        for scheme in ("http", "https")
        for prefix in ("", "www.")
    ]
    endpoints: set[tuple[str, str]] = set()
    with httpx.Client(
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for variant in variants:
            try:
                response = client.get(variant)
            except httpx.HTTPError as error:
                logger.info("Variant %s unreachable: %s", variant, type(error).__name__)
                continue
            endpoints.add((response.url.scheme, response.url.host.lower()))
    return len(endpoints) == 1
