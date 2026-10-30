"""Ask ChatGPT, Gemini and Perplexity on their websites in the shared browser and read the answer.

Context: First route of the assistant collector. Every question opens a fresh chat in the shared
tab, types the question like a person, waits until the site marks the answer complete and reads
answer text and cited sources from the page. Logins come from the persistent browser profile:
ChatGPT and Gemini answer logged out, a logged-in profile is used when present; Perplexity asks
for a login from server addresses. Web search is the default of all three sites.
Input: The shared BrowserService, an AssistantSite and a question.
Output: PageAnswer, or LoginWallError, SearchBlockedError or TransientCollectionError.
"""

import logging
import math
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup, Tag
from playwright.async_api import Page

from radarauditor.collectors.browser import BrowserService
from radarauditor.collectors.patterns import collapse_whitespace
from radarauditor.errors import LoginWallError, SearchBlockedError, TransientCollectionError

logger = logging.getLogger(__name__)

POLL_SECONDS = 3.0
COMPOSER_POLLS = 5
LOGIN_WALL_MAX_CHARS = 200
LOGIN_HOSTS = ("auth.openai.com", "accounts.google.com", "auth.perplexity.ai")
LOGIN_PHRASES = (
    "melde dich an",
    "melden sie sich an",
    "anmelden, um",
    "zum fortfahren anmelden",
    "log in to",
    "sign in to",
    "sign up to continue",
)
CHALLENGE_MARKERS = (
    "cf-chl",
    "challenge-form",
    "cf-turnstile",
    "<title>Just a moment",
    "<title>Einen Moment",
)
FAVICON_PATH = "/s2/favicons"
DOMAIN_LABEL = re.compile(r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}$", re.IGNORECASE)
# Gemini labels each citation button "Genaue Quellenangaben für Zitate aus A und B ansehen. ...".
CITATION_LABEL = re.compile(
    r"(?:Zitaten? aus|citations? from) (.+?)(?: ansehen|\.\s|\.?$)", re.IGNORECASE
)
NAME_SEPARATOR = re.compile(r",\s*|\s+(?:und|and)\s+")
SHARED_IGNORED_HOSTS = ("mapbox.com", "openstreetmap.org", "google.com", "gstatic.com")


@dataclass(frozen=True)
class AssistantSite:
    """How one assistant website is asked and read.

    All selectors are CSS. composer is the question field; answer matches the answer container
    (the last one is read); done matches anywhere on the page once the answer is complete;
    source_labels matches citation labels that carry a source name instead of a link;
    ignored_hosts are the site's own and map hosts, never counted as sources.
    """

    provider: str
    url: str
    composer: str
    answer: str
    done: str
    source_labels: str | None
    ignored_hosts: tuple[str, ...]


@dataclass(frozen=True)
class PageAnswer:
    """Answer text and cited sources of one question."""

    text: str
    sources: list[str]


@dataclass(frozen=True)
class AnswerReading:
    """What the page shows right now: answer so far and whether the site marked it complete."""

    text: str
    sources: list[str]
    complete: bool


CHATGPT = AssistantSite(
    provider="chatgpt",
    url="https://chatgpt.com/?hints=search",
    composer='textarea[name="prompt"], #prompt-textarea',
    answer='[data-message-role="assistant"][id], [data-message-author-role="assistant"]',
    done='[data-message-role="assistant"][data-message-complete]',
    source_labels=None,
    ignored_hosts=("chatgpt.com", "openai.com", "oaistatic.com", "oaiusercontent.com"),
)
GEMINI = AssistantSite(
    provider="gemini",
    url="https://gemini.google.com/app",
    composer='div.ql-editor[contenteditable="true"], rich-textarea [role="textbox"]',
    answer="model-response message-content",
    done="model-response message-actions",
    source_labels='button[aria-label*="Quellenangaben"], button[aria-label*="citation"]',
    ignored_hosts=("gemini.google.com", "googleusercontent.com"),
)
PERPLEXITY = AssistantSite(
    provider="perplexity",
    url="https://www.perplexity.ai/",
    composer="#ask-input, textarea[placeholder]",
    answer='[data-renderer="lm"]',
    done='button[aria-label="Kopieren"], button[aria-label="Copy"]',
    source_labels=None,
    ignored_hosts=("perplexity.ai", "pplx.ai"),
)
SITES: dict[str, AssistantSite] = {site.provider: site for site in (CHATGPT, GEMINI, PERPLEXITY)}


async def ask_on_site(
    browser: BrowserService, site: AssistantSite, question: str, timeout_seconds: int
) -> PageAnswer:
    """Open a fresh chat on the site, ask one question and wait for the complete answer.

    Args:
        browser: Shared browser of the job; the tab is held for this one question only.
        site: The assistant website.
        question: Question in German.
        timeout_seconds: Longest wait for the complete answer.

    Returns:
        Answer text and cited sources.

    Raises:
        LoginWallError: If the site shows a login page or answers only with a login request.
        SearchBlockedError: On a bot check such as a Cloudflare challenge or a captcha.
        TransientCollectionError: If no input field appears or the answer stays incomplete.
        playwright.async_api.Error: On a navigation or browser failure.
    """
    async with browser.page() as page:
        html = await browser.open(page, site.url)
        await _wait_for_composer(browser, page, site, html)
        composer = page.locator(site.composer).first
        await browser.type_text(composer, question)
        await composer.press("Enter")
        answer = await _wait_for_answer(browser, page, site, timeout_seconds)
        await browser.linger(page)
    return answer


async def _wait_for_composer(
    browser: BrowserService, page: Page, site: AssistantSite, html: str
) -> None:
    for attempt in range(COMPOSER_POLLS):
        ensure_answerable(page.url, html)
        if BeautifulSoup(html, "lxml").select_one(site.composer) is not None:
            return
        if attempt < COMPOSER_POLLS - 1:
            await browser.pacing.sleep(POLL_SECONDS)
            html = await page.content()
    raise LoginWallError(f"{site.provider} shows no question field at {_host(page.url)}")


async def _wait_for_answer(
    browser: BrowserService, page: Page, site: AssistantSite, timeout_seconds: int
) -> PageAnswer:
    reading = AnswerReading("", [], complete=False)
    for _ in range(max(1, math.ceil(timeout_seconds / POLL_SECONDS))):
        await browser.pacing.sleep(POLL_SECONDS)
        html = await page.content()
        ensure_answerable(page.url, html)
        reading = read_answer(html, site)
        if is_login_request(reading.text):
            raise LoginWallError(f"{site.provider} asks to sign in: {reading.text[:80]}")
        if reading.complete and reading.text:
            return PageAnswer(reading.text, reading.sources)
    detail = "no answer" if not reading.text else "the answer did not finish"
    raise TransientCollectionError(f"{site.provider}: {detail} within {timeout_seconds} s")


def ensure_answerable(url: str, html: str) -> None:
    """Raise when the page is a login page or a bot check instead of the assistant.

    Raises:
        LoginWallError: If the address is a sign-in page.
        SearchBlockedError: If the page is a Cloudflare challenge.
    """
    host = _host(url)
    if host in LOGIN_HOSTS:
        raise LoginWallError(f"redirected to the sign-in page {host}")
    if any(marker in html for marker in CHALLENGE_MARKERS):
        raise SearchBlockedError(f"{host or 'page'} shows a bot check", url)


def is_login_request(text: str) -> bool:
    """True for a short answer that only asks to sign in, as Perplexity gives logged out."""
    folded = text.casefold()
    return len(folded) <= LOGIN_WALL_MAX_CHARS and any(phrase in folded for phrase in LOGIN_PHRASES)


def read_answer(html: str, site: AssistantSite) -> AnswerReading:
    """Read the last answer on an assistant page.

    Sources are, in page order: links in the answer, the domains of favicon images (ChatGPT shows
    citations as favicon pills) and citation labels (Gemini names sources without links; labels
    that are domains become https addresses, other names are kept as written).

    Args:
        html: Page source.
        site: The assistant website.

    Returns:
        Answer text with collapsed whitespace, sources, and whether the site marked it complete.
    """
    soup = BeautifulSoup(html, "lxml")
    answers = soup.select(site.answer)
    if not answers:
        return AnswerReading("", [], complete=False)
    answer = answers[-1]
    for hidden in answer.select("script, style, svg"):
        hidden.decompose()
    ignored = (*site.ignored_hosts, *SHARED_IGNORED_HOSTS)
    sources = _unique(
        [
            *_link_sources(answer, ignored),
            *_favicon_sources(answer, ignored),
            *_label_sources(answer, site.source_labels),
        ]
    )
    return AnswerReading(
        text=collapse_whitespace(answer.get_text(" ")),
        sources=sources,
        complete=soup.select_one(site.done) is not None,
    )


def _link_sources(answer: Tag, ignored: tuple[str, ...]) -> list[str]:
    links = [str(link.get("href", "")) for link in answer.select("a[href]")]
    return [link for link in links if link.startswith("http") and not _is_ignored(link, ignored)]


def _favicon_sources(answer: Tag, ignored: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for image in answer.select("img[src]"):
        address = urlsplit(str(image.get("src", "")))
        if address.path != FAVICON_PATH:
            continue
        domain = parse_qs(address.query).get("domain", [""])[0]
        site = domain if "://" in domain else f"https://{domain}"
        if domain and not _is_ignored(site, ignored):
            found.append(site)
    return found


def _label_sources(answer: Tag, selector: str | None) -> list[str]:
    if selector is None:
        return []
    names = [name for element in answer.select(selector) for name in _citation_names(element)]
    return [f"https://{name.lower()}" if DOMAIN_LABEL.match(name) else name for name in names]


def _citation_names(element: Tag) -> list[str]:
    """Source names of one citation button; its label names every source, the text only one."""
    label = collapse_whitespace(str(element.get("aria-label", "")))
    match = CITATION_LABEL.search(label)
    if match is None:
        text = collapse_whitespace(element.get_text(" "))
        return [text] if text else []
    return [name.strip() for name in NAME_SEPARATOR.split(match.group(1)) if name.strip()]


def _is_ignored(url: str, ignored: tuple[str, ...]) -> bool:
    host = _host(url)
    return any(host == item or host.endswith(f".{item}") for item in ignored)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
