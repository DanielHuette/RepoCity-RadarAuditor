"""Run real Google and Bing searches in the shared browser and read positions and the local pack.

Context: Free ranking source; runs in the server browser with human pacing. When Google blocks the
browser, the phrases not yet searched are handed back as deferred instead of failing, so a
serp_retry job can run them later. Replaced by the paid provider when enabled.
Input: The shared BrowserService and phrases.
Output: SearchBatch per engine and vertical: parsed SerpResults plus the deferred phrases.
"""

import base64
import binascii
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, quote_plus, urlsplit

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from playwright.async_api import Error as PlaywrightError

from radarauditor.collectors.browser import BrowserService, find_consent_choice, is_blocked_page
from radarauditor.collectors.patterns import collapse_whitespace, site_key
from radarauditor.collectors.snapshot import SerpResult
from radarauditor.errors import SearchBlockedError, TransientCollectionError

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_URL = "https://www.google.de/search?q={query}&hl=de&gl=de&num=20"
GOOGLE_IMAGES_URL = "https://www.google.de/search?q={query}&hl=de&gl=de&tbm=isch"
BING_SEARCH_URL = "https://www.bing.com/search?q={query}&setlang=de&cc=de"
IMAGE_RESULT_LIMIT = 40
DEFERRAL_NOTE = "searches deferred to a retry"

AI_OVERVIEW_LABELS = ("KI-Übersicht", "AI Overview")
PEOPLE_ALSO_ASK_LABELS = ("Nutzer fragen auch", "Ähnliche Fragen", "People also ask")
LOCAL_PACK_LABELS = ("Unternehmen", "Orte", "Places", "Businesses")
AD_LABELS = ("Gesponsert", "Anzeige", "Sponsored")
AD_BLOCK_SELECTOR = "#tads [data-text-ad], #tadsb [data-text-ad], #bottomads [data-text-ad]"
AD_HOST_ATTRIBUTES = ("data-dtld", "data-pcu")
MAX_AI_MENTIONS = 10
MAX_BLOCK_CLIMB = 8
MIN_AI_BLOCK_EXTRA_CHARS = 80
MAX_NAME_WORDS = 6
MAX_NAME_CHARS = 60
MAX_QUESTION_CHARS = 200
NAME_PATTERN = re.compile(
    r"[A-ZÄÖÜ][\wäöüß&'’-]*(?:\s+(?:&\s+)?[A-ZÄÖÜ][\wäöüß&'’-]*)+",
)
LEADING_FILLER_WORDS = frozenset(
    {"Die", "Der", "Das", "Ein", "Eine", "Im", "In", "Für", "Bei", "Mit", "Laut", "Auch", "Zu"}
)
LEGAL_FORM_WORDS = frozenset({"GmbH", "KG", "UG", "e.K.", "OHG", "GbR", "AG"})
GOOGLE_HOST_PATTERN = re.compile(
    r"(^|\.)google\.[a-z.]+$|(^|\.)googleusercontent\.com$|(^|\.)gstatic\.com$|"
    r"(^|\.)googleadservices\.com$"
)

Parser = Callable[[str, str], SerpResult]


@dataclass(frozen=True)
class SearchBatch:
    """Results of the phrases that were searched and the phrases left for a later attempt.

    deferred keeps input order and starts with the phrase whose page failed; reason says why.
    """

    results: list[SerpResult]
    deferred: list[str]
    reason: str | None = None


def deferral_note(count: int, reason: str | None) -> str:
    """Return the warning that marks deferred Google searches; a retry replaces it."""
    return f"{count} {DEFERRAL_NOTE}: {reason or 'unknown reason'}"


async def search_all(browser: BrowserService, phrases: list[str]) -> SearchBatch:
    """Run every phrase on google.de (hl=de, gl=de) with human pauses and parse each result page.

    Args:
        browser: Shared browser of the job.
        phrases: Search phrases including the city, for example "Dachdecker Münster".

    Returns:
        Parsed results in input order; once Google blocks or a page fails, the rest is deferred.
    """
    return await _run_searches(browser, phrases, GOOGLE_SEARCH_URL, parse_result_page)


async def search_bing(browser: BrowserService, phrases: list[str]) -> SearchBatch:
    """Run every phrase on Bing and read the organic results only.

    Args:
        browser: Shared browser of the job.
        phrases: Search phrases including the city.

    Returns:
        Results with engine "bing" in input order; once a page fails, the rest is deferred.
    """
    return await _run_searches(browser, phrases, BING_SEARCH_URL, parse_bing_page)


async def search_images(browser: BrowserService, phrases: list[str]) -> SearchBatch:
    """Run every phrase on Google Images and record the source hosts of the first results.

    Args:
        browser: Shared browser of the job.
        phrases: Search phrases including the city.

    Returns:
        Results with vertical "images" in input order; once Google blocks, the rest is deferred.
    """
    return await _run_searches(browser, phrases, GOOGLE_IMAGES_URL, parse_image_page)


async def _run_searches(
    browser: BrowserService, phrases: list[str], url_template: str, parser: Parser
) -> SearchBatch:
    results: list[SerpResult] = []
    if not phrases:
        return SearchBatch(results, [])
    try:
        async with browser.page() as page:
            for phrase in phrases:
                if results:
                    await browser.pause()
                html = await browser.open(page, url_template.format(query=quote_plus(phrase)))
                results.append(parser(html, phrase))
                await browser.linger(page)
    except TransientCollectionError as error:
        return _deferred(results, phrases, str(error))
    except PlaywrightError as error:
        return _deferred(results, phrases, f"browser failed: {error.message}")
    return SearchBatch(results, [])


def _deferred(results: list[SerpResult], phrases: list[str], reason: str) -> SearchBatch:
    remaining = phrases[len(results) :]
    logger.warning(
        "Search stopped, %d of %d phrases deferred: %s", len(remaining), len(phrases), reason
    )
    return SearchBatch(results, remaining, reason)


def parse_result_page(html: str, phrase: str) -> SerpResult:
    """Extract organic results, local pack, AI overview, questions, ads and knowledge panel.

    Organic positions count distinct domains (without "www.") in page order, so several links of
    one website occupy one position. Ads are the advertiser domains of blocks labelled
    "Gesponsert" or "Anzeige"; the knowledge panel is a right-hand block with a heading or an
    element marked data-attrid="title".

    Args:
        html: Source of a Google result page.
        phrase: The phrase that was searched.

    Returns:
        The parsed result with measured_at set to now (UTC).

    Raises:
        SearchBlockedError: If the page is a captcha.
        TransientCollectionError: If a consent dialog was not dismissed.
    """
    soup = _google_soup(html)
    ai_block = _block_for_heading(soup, AI_OVERVIEW_LABELS, _has_extra_text)
    return SerpResult(
        phrase=phrase,
        organic=_numbered(_google_organic_domains(soup)),
        local_pack=_numbered(_local_pack_names(soup)),
        ai_overview_present=ai_block is not None,
        ai_overview_mentions=_ai_mentions(ai_block) if ai_block is not None else [],
        people_also_ask=_people_also_ask(soup),
        measured_at=datetime.now(UTC),
        ads=_ads(soup),
        knowledge_panel=_has_knowledge_panel(soup),
    )


def parse_image_page(html: str, phrase: str) -> SerpResult:
    """Extract the source domains of the first IMAGE_RESULT_LIMIT results of Google Images.

    A result's source is its data-lpage attribute or, failing that, a link to a page outside
    Google. Domains are listed once, in the order of their first result.

    Args:
        html: Source of a Google Images result page.
        phrase: The phrase that was searched.

    Returns:
        The parsed result with vertical "images" and the source domains as organic entries.

    Raises:
        SearchBlockedError: If the page is a captcha.
        TransientCollectionError: If a consent dialog was not dismissed.
    """
    soup = _google_soup(html)
    return SerpResult(
        phrase=phrase,
        organic=_numbered(_image_source_domains(soup)),
        local_pack=[],
        ai_overview_present=False,
        ai_overview_mentions=[],
        people_also_ask=[],
        measured_at=datetime.now(UTC),
        vertical="images",
    )


def _google_soup(html: str) -> BeautifulSoup:
    if is_blocked_page("", html):
        raise SearchBlockedError("Google answered with a captcha page")
    if find_consent_choice(html) is not None:
        raise TransientCollectionError("Google consent dialog was not dismissed")
    return BeautifulSoup(html, "lxml")


def _image_source_domains(soup: BeautifulSoup) -> list[str]:
    root = soup.select_one("div#search") or soup.select_one("div#islrg") or soup
    domains: list[str] = []
    results = 0
    for element in root.select("[data-lpage], a[href]"):
        if results == IMAGE_RESULT_LIMIT:
            break
        if element.name == "a" and element.find_parent(attrs={"data-lpage": True}) is not None:
            continue
        target = _text_attribute(element, "data-lpage") or _resolve_google_href(
            _text_attribute(element, "href")
        )
        host = urlsplit(target).hostname
        if not host or GOOGLE_HOST_PATTERN.search(host):
            continue
        results += 1
        domain = site_key(host)
        if domain not in domains:
            domains.append(domain)
    return domains


def _ads(soup: BeautifulSoup) -> list[str]:
    blocks: list[Tag] = list(soup.select(AD_BLOCK_SELECTOR))
    for node in soup.find_all(string=True):
        if not isinstance(node, NavigableString) or not isinstance(node.parent, Tag):
            continue
        label = collapse_whitespace(str(node))
        if label in AD_LABELS and node.parent.name not in ("script", "style", "title"):
            block = _climb(node.parent, label, _has_link)
            if block is not None:
                blocks.append(block)
    advertisers: list[str] = []
    for block in blocks:
        advertiser = _advertiser(block)
        if advertiser and advertiser not in advertisers:
            advertisers.append(advertiser)
    return advertisers


def _advertiser(block: Tag) -> str | None:
    for element in [block, *block.find_all(True)]:
        for attribute in AD_HOST_ATTRIBUTES:
            host = _domain_of(_text_attribute(element, attribute))
            if host:
                return host
    for anchor in block.select("a[href]"):
        host = urlsplit(_resolve_ad_href(_text_attribute(anchor, "href"))).hostname
        if host and not GOOGLE_HOST_PATTERN.search(host):
            return site_key(host)
    for element in block.select("cite, [role='text']"):
        words = collapse_whitespace(element.get_text(" ")).split(" ")
        host = _domain_of(words[0])
        if host:
            return host
    heading = block.select_one("[role='heading']")
    if heading is None:
        return None
    return collapse_whitespace(heading.get_text(" ")) or None


def _resolve_ad_href(href: str) -> str:
    target = parse_qs(urlsplit(href).query).get("adurl")
    return target[0] if target else _resolve_google_href(href)


def _domain_of(value: str) -> str | None:
    if not value:
        return None
    host = urlsplit(value if "://" in value else f"https://{value}").hostname
    return site_key(host) if host and "." in host else None


def _has_knowledge_panel(soup: BeautifulSoup) -> bool:
    if soup.select_one("[data-attrid='title']") is not None:
        return True
    panel = soup.select_one("div#rhs")
    return panel is not None and panel.select_one("h1, h2, h3, [role='heading']") is not None


def parse_bing_page(html: str, phrase: str) -> SerpResult:
    """Extract the organic results of a Bing result page.

    Args:
        html: Source of a Bing result page.
        phrase: The phrase that was searched.

    Returns:
        The parsed result with engine "bing" and empty Google-only features.

    Raises:
        SearchBlockedError: If the page is a captcha.
    """
    if is_blocked_page("", html):
        raise SearchBlockedError("Bing answered with a captcha page")
    soup = BeautifulSoup(html, "lxml")
    domains: list[str] = []
    for result in soup.select("li.b_algo"):
        anchor = result.select_one("h2 a[href]")
        host = _bing_target_host(anchor, result.select_one("cite"))
        if host and host not in domains:
            domains.append(host)
    return SerpResult(
        phrase=phrase,
        organic=_numbered(domains),
        local_pack=[],
        ai_overview_present=False,
        ai_overview_mentions=[],
        people_also_ask=[],
        measured_at=datetime.now(UTC),
        engine="bing",
    )


def _numbered(values: Iterable[str]) -> list[tuple[int, str]]:
    return list(enumerate(values, start=1))


def _google_organic_domains(soup: BeautifulSoup) -> list[str]:
    root = soup.select_one("div#search") or soup.select_one("div#rso") or soup
    domains: list[str] = []
    for anchor in root.select("a[href]"):
        if anchor.find("h3") is None:
            continue
        host = urlsplit(_resolve_google_href(_text_attribute(anchor, "href"))).hostname
        if not host or GOOGLE_HOST_PATTERN.search(host):
            continue
        domain = site_key(host)
        if domain not in domains:
            domains.append(domain)
    return domains


def _resolve_google_href(href: str) -> str:
    if href.startswith("/url?"):
        target = parse_qs(urlsplit(href).query).get("q") or parse_qs(urlsplit(href).query).get(
            "url"
        )
        return target[0] if target else ""
    return href if href.startswith(("http://", "https://")) else ""


def _bing_target_host(anchor: Tag | None, cite: Tag | None) -> str | None:
    href = _text_attribute(anchor, "href") if anchor is not None else ""
    parts = urlsplit(href)
    if parts.hostname and parts.hostname.endswith("bing.com") and parts.path.startswith("/ck/"):
        href = _decode_bing_redirect(parts.query)
        parts = urlsplit(href)
    if parts.hostname and not parts.hostname.endswith("bing.com"):
        return site_key(parts.hostname)
    if cite is not None:
        cited = collapse_whitespace(cite.get_text(" ")).split(" ")[0]
        cited_host = urlsplit(cited if "://" in cited else f"https://{cited}").hostname
        return site_key(cited_host) if cited_host else None
    return None


def _decode_bing_redirect(query: str) -> str:
    encoded = (parse_qs(query).get("u") or [""])[0]
    if not encoded.startswith("a1"):
        return ""
    payload = encoded[2:]
    try:
        return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return ""


def _local_pack_names(soup: BeautifulSoup) -> list[str]:
    elements: list[Tag] = list(soup.select("div.VkpGBb .dbg0pd, div.VkpGBb [role='heading']"))
    if not elements:
        elements = list(soup.select("div[data-attrid*='local'] [role='heading']"))
    if not elements:
        block = _block_for_heading(soup, LOCAL_PACK_LABELS, _has_entry_headings, exact=True)
        if block is not None:
            elements = [
                element
                for element in block.select("[role='heading']")
                if collapse_whitespace(element.get_text(" ")) not in LOCAL_PACK_LABELS
            ]
    names: list[str] = []
    for element in elements:
        name = collapse_whitespace(element.get_text(" "))
        if name and name not in names:
            names.append(name)
    return names


def _people_also_ask(soup: BeautifulSoup) -> list[str]:
    block = _block_for_heading(soup, PEOPLE_ALSO_ASK_LABELS, _has_question)
    scope: Tag = block if block is not None else soup
    questions: list[str] = []
    for element in scope.select("[data-q]"):
        _add_question(questions, _text_attribute(element, "data-q"))
    if block is not None:
        for element in block.select("div[jsname], [role='button']"):
            _add_question(questions, element.get_text(" "))
    return questions


def _add_question(questions: list[str], raw: str) -> None:
    text = collapse_whitespace(raw)
    if (
        text.endswith("?")
        and text.count("?") == 1
        and len(text) <= MAX_QUESTION_CHARS
        and text not in questions
    ):
        questions.append(text)


def _ai_mentions(block: Tag) -> list[str]:
    candidates: list[str] = []
    for element in block.select("strong, b, a, [role='heading']"):
        text = collapse_whitespace(element.get_text(" "))
        words = text.split()
        if (
            text
            and text[0].isupper()
            and len(text) <= MAX_NAME_CHARS
            and (len(words) >= 2 or LEGAL_FORM_WORDS.intersection(words))
            and len(words) <= MAX_NAME_WORDS
        ):
            candidates.append(text.rstrip(":.,"))
    candidates.extend(_capitalised_phrases(collapse_whitespace(block.get_text(" "))))
    return _unique_names(candidates)


def ai_overview_names(text: str) -> list[str]:
    """Return business-like names (capitalised multi-word phrases) found in a text.

    Args:
        text: Text of an AI overview.

    Returns:
        Up to MAX_AI_MENTIONS distinct names in order of appearance.
    """
    return _unique_names(_capitalised_phrases(collapse_whitespace(text)))


def _unique_names(candidates: list[str]) -> list[str]:
    mentions: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        name = _strip_filler(candidate)
        key = name.lower()
        if not name or any(label.lower() in key for label in AI_OVERVIEW_LABELS) or key in seen:
            continue
        seen.add(key)
        mentions.append(name)
        if len(mentions) == MAX_AI_MENTIONS:
            break
    return mentions


def _capitalised_phrases(text: str) -> list[str]:
    return [match.group(0) for match in NAME_PATTERN.finditer(text)]


def _strip_filler(candidate: str) -> str:
    words = candidate.split()
    while words and words[0] in LEADING_FILLER_WORDS:
        words = words[1:]
    if len(words) < 2 and not LEGAL_FORM_WORDS.intersection(words):
        return ""
    return " ".join(words[:MAX_NAME_WORDS])


def _block_for_heading(
    soup: BeautifulSoup,
    labels: tuple[str, ...],
    is_block: Callable[[Tag, str], bool],
    *,
    exact: bool = False,
) -> Tag | None:
    for node in soup.find_all(string=True):
        if not isinstance(node, NavigableString) or not isinstance(node.parent, Tag):
            continue
        if node.parent.name in ("script", "style", "title"):
            continue
        text = collapse_whitespace(str(node))
        matched = text in labels if exact else any(label in text for label in labels)
        if not text or not matched:
            continue
        block = _climb(node.parent, text, is_block)
        if block is not None:
            return block
    return None


def _climb(start: Tag, heading: str, is_block: Callable[[Tag, str], bool]) -> Tag | None:
    current: Tag | None = start
    for _ in range(MAX_BLOCK_CLIMB):
        if current is None or current.name in ("body", "html") or current.get("id") == "rso":
            return None
        if is_block(current, heading):
            return current
        current = current.parent
    return None


def _has_extra_text(element: Tag, heading: str) -> bool:
    return len(collapse_whitespace(element.get_text(" "))) >= len(heading) + (
        MIN_AI_BLOCK_EXTRA_CHARS
    )


def _has_question(element: Tag, heading: str) -> bool:
    return any(
        collapse_whitespace(item.get_text(" ")).endswith("?")
        for item in element.select("[data-q], div[jsname], [role='button']")
    )


def _has_link(element: Tag, heading: str) -> bool:
    return element.select_one("a[href]") is not None


def _has_entry_headings(element: Tag, heading: str) -> bool:
    return any(
        collapse_whitespace(item.get_text(" ")) != heading
        for item in element.select("[role='heading']")
    )


def _text_attribute(tag: Tag | None, name: str) -> str:
    if tag is None:
        return ""
    value = tag.get(name)
    if value is None:
        return ""
    return value if isinstance(value, str) else " ".join(value)
