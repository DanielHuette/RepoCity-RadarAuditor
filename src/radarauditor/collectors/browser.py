"""One shared, hardened Chromium for every collector that reads a consumer web page.

Context: Google search, Google Maps and the assistant web apps are read in a real browser on the
server, so results match what customers see. All of them share one persistent German profile
(cookies, consent answers, logins) and one tab at a time, which looks like one person at a desk.
Input: The profile directory (setting BROWSER_PROFILE_DIR).
Output: Pages with locale de-DE, time zone Europe/Berlin and a regular Chrome identity, plus
navigation that answers Google's consent dialog and raises SearchBlockedError on bot checks.

API:
    async with BrowserService(settings.browser_profile_dir) as browser:
        async with browser.page(geolocation=Geolocation(51.96, 7.63)) as page:
            html = await browser.open(page, "https://www.google.de/search?q=...")
            await browser.linger(page)
            await browser.type_text(page.get_by_role("textbox"), "Dachdecker Münster")
        await browser.pause()

    - Chromium starts on the first `page()` call, so a job that needs no browser never launches one;
      leaving the `async with` block closes it.
    - `page()` holds the only tab until the block ends; geolocation applies to that tab only.
    - `open()` navigates, answers a Google consent dialog (rejecting where offered) and raises
      SearchBlockedError on a captcha, "unusual traffic" or /sorry/ page or HTTP 429.
    - `pause()`, `linger()` and `type_text()` add human pacing with jitter.
    - `find_consent_choice()` and `is_blocked_page()` work on plain HTML for parsers and tests.
"""

import asyncio
import logging
import random
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Locator, Page, async_playwright

from radarauditor.collectors.patterns import collapse_whitespace
from radarauditor.errors import SearchBlockedError

logger = logging.getLogger(__name__)

LOCALE = "de-DE"
TIMEZONE = "Europe/Berlin"
ACCEPT_LANGUAGE = "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"
VIEWPORT = {"width": 1366, "height": 768}
SCREEN = {"width": 1920, "height": 1080}
NAVIGATION_TIMEOUT_MS = 30000
TOO_MANY_REQUESTS = 429
GEOLOCATION_ACCURACY_METRES = 50.0
LAUNCH_ARGUMENTS = ("--disable-blink-features=AutomationControlled",)
IGNORED_DEFAULT_ARGUMENTS = ("--enable-automation",)
HIDE_WEBDRIVER_SCRIPT = (
    "Object.defineProperty(Navigator.prototype, 'webdriver', "
    "{get: () => false, configurable: true});"
)
HEADLESS_MARKER = "HeadlessChrome/"
CHROME_VERSION = re.compile(r"Chrome/(\d+)\.[\d.]+")
NOT_A_BRAND = {"brand": "Not)A;Brand", "version": "24"}

CONSENT_HOSTS = ("consent.google.com", "consent.youtube.com")
REJECT_LABELS = ("Alle ablehnen", "Reject all")
ACCEPT_LABELS = ("Alle akzeptieren", "Accept all")
BLOCK_PATH_MARKERS = ("/sorry/",)
BLOCK_MARKERS = (
    'id="captcha-form"',
    "g-recaptcha",
    "unusual traffic from your computer network",
    "detected unusual traffic",
    "ungewöhnlichen Datenverkehr",
)

Sleep = Callable[[float], Awaitable[None]]
ContextFactory = Callable[[Path, bool], AbstractAsyncContextManager[BrowserContext]]


@dataclass(frozen=True)
class Geolocation:
    """Position reported to pages that ask for it, e.g. one point of the Maps grid."""

    latitude: float
    longitude: float
    accuracy: float = GEOLOCATION_ACCURACY_METRES


@dataclass(frozen=True)
class Pacing:
    """How long a person would pause; every wait is base plus a random share of the jitter."""

    between_actions: float = 9.0
    between_actions_jitter: float = 6.0
    linger: float = 0.8
    linger_jitter: float = 1.6
    keystroke: float = 0.06
    keystroke_jitter: float = 0.14
    scroll_pixels: tuple[int, int] = (250, 900)
    sleep: Sleep = asyncio.sleep
    rng: random.Random = field(default_factory=random.Random)

    def jittered(self, base: float, jitter: float) -> float:
        """Return base plus a uniformly drawn share of jitter."""
        return base + self.rng.uniform(0, jitter)


class BrowserService:
    """Lazily started persistent Chromium shared by the browser collectors of one job."""

    def __init__(
        self,
        profile_dir: Path,
        *,
        headless: bool = True,
        pacing: Pacing | None = None,
        context_factory: ContextFactory | None = None,
    ) -> None:
        """Remember where the profile lives; nothing starts before the first page.

        Args:
            profile_dir: Persistent Chromium profile (cookies, consent, logins).
            headless: False only for the one-time interactive logins.
            pacing: Human pauses; tests pass zero waits.
            context_factory: Opens the browser context; tests pass a fake.
        """
        self._profile_dir = profile_dir
        self._headless = headless
        self.pacing = pacing if pacing is not None else Pacing()
        self._context_factory = context_factory or persistent_chromium
        self._stack = AsyncExitStack()
        self._context: BrowserContext | None = None
        self._tab = asyncio.Lock()
        self._identity: tuple[str, dict[str, object]] | None = None

    async def __aenter__(self) -> Self:
        """Return the service; Chromium starts with the first page."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close Chromium if it was started."""
        await self.close()

    async def close(self) -> None:
        """Close the browser context and Playwright; safe to call twice."""
        self._context = None
        self._identity = None
        await self._stack.aclose()

    @asynccontextmanager
    async def page(self, geolocation: Geolocation | None = None) -> AsyncIterator[Page]:
        """Open the shared tab; other callers wait until this block ends.

        Args:
            geolocation: Position to report for this tab; None reports none.

        Yields:
            A page with the realistic identity applied.

        Raises:
            playwright.async_api.Error: If Chromium cannot start or open a page.
        """
        async with self._tab:
            context = await self._ensure_context()
            await _set_geolocation(context, geolocation)
            page = await context.new_page()
            try:
                await self._apply_identity(context, page)
                yield page
            finally:
                await page.close()

    async def open(self, page: Page, url: str) -> str:
        """Navigate, answer a consent dialog and return the page source.

        Args:
            page: Tab from `page()`.
            url: Address to open.

        Returns:
            HTML of the page that was asked for.

        Raises:
            SearchBlockedError: On a captcha, "unusual traffic" or /sorry/ page or HTTP 429.
            playwright.async_api.Error: On a navigation failure or timeout.
        """
        response = await page.goto(
            url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
        )
        if response is not None and response.status == TOO_MANY_REQUESTS:
            raise SearchBlockedError(f"{urlsplit(url).hostname} answered HTTP 429", page.url)
        html = await page.content()
        ensure_not_blocked(page.url, html)
        if await self._answer_consent(page, html):
            if urlsplit(page.url).hostname in CONSENT_HOSTS:
                await page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            html = await page.content()
            ensure_not_blocked(page.url, html)
        return html

    async def pause(self) -> None:
        """Wait like a person between two searches or questions."""
        pacing = self.pacing
        await pacing.sleep(pacing.jittered(pacing.between_actions, pacing.between_actions_jitter))

    async def linger(self, page: Page) -> None:
        """Scroll a little and rest on the page, as a reader would."""
        pacing = self.pacing
        await page.mouse.wheel(0, pacing.rng.randint(*pacing.scroll_pixels))
        await pacing.sleep(pacing.jittered(pacing.linger, pacing.linger_jitter))

    async def rest(self) -> None:
        """Rest briefly without scrolling, as a person looking at a picture would."""
        pacing = self.pacing
        await pacing.sleep(pacing.jittered(pacing.linger, pacing.linger_jitter))

    async def type_text(self, target: Locator, text: str) -> None:
        """Click a field and type text key by key with irregular gaps."""
        pacing = self.pacing
        await target.click()
        for character in text:
            await target.press_sequentially(character)
            await pacing.sleep(pacing.jittered(pacing.keystroke, pacing.keystroke_jitter))

    async def _ensure_context(self) -> BrowserContext:
        if self._context is None:
            self._profile_dir.mkdir(parents=True, exist_ok=True)
            context = await self._stack.enter_async_context(
                self._context_factory(self._profile_dir, self._headless)
            )
            await context.add_init_script(HIDE_WEBDRIVER_SCRIPT)
            self._context = context
            logger.info("Browser started with profile %s", self._profile_dir)
        return self._context

    async def _apply_identity(self, context: BrowserContext, page: Page) -> None:
        if self._identity is None:
            reported = str(await page.evaluate("navigator.userAgent"))
            agent = realistic_user_agent(reported)
            self._identity = (agent, user_agent_override(agent)) if agent != reported else None
        if self._identity is None:
            return
        session = await context.new_cdp_session(page)
        await session.send("Emulation.setUserAgentOverride", self._identity[1])

    async def _answer_consent(self, page: Page, html: str) -> bool:
        label = find_consent_choice(html)
        if label is None:
            return False
        logger.info("Answering consent dialog with %r", label)
        button = page.get_by_role("button", name=label, exact=True).first
        await button.click()
        # Waiting for the button to disappear covers both the consent page that redirects back
        # and the overlay that only closes.
        await button.wait_for(state="hidden", timeout=NAVIGATION_TIMEOUT_MS)
        await page.wait_for_load_state("domcontentloaded")
        return True


@asynccontextmanager
async def persistent_chromium(profile_dir: Path, headless: bool) -> AsyncIterator[BrowserContext]:
    """Start Playwright and open Chromium on the persistent profile.

    The "chromium" channel runs the full browser in the new headless mode, which differs far less
    from a visible Chrome than the separate headless shell.

    Args:
        profile_dir: Persistent profile directory.
        headless: Whether to run without a window.

    Yields:
        The persistent browser context.
    """
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            channel="chromium",
            headless=headless,
            locale=LOCALE,
            timezone_id=TIMEZONE,
            viewport={"width": VIEWPORT["width"], "height": VIEWPORT["height"]},
            screen={"width": SCREEN["width"], "height": SCREEN["height"]},
            extra_http_headers={"Accept-Language": ACCEPT_LANGUAGE},
            args=list(LAUNCH_ARGUMENTS),
            ignore_default_args=list(IGNORED_DEFAULT_ARGUMENTS),
        )
        try:
            yield context
        finally:
            await context.close()


async def _set_geolocation(context: BrowserContext, geolocation: Geolocation | None) -> None:
    if geolocation is None:
        await context.set_geolocation(None)
        return
    await context.grant_permissions(["geolocation"])
    await context.set_geolocation(
        {
            "latitude": geolocation.latitude,
            "longitude": geolocation.longitude,
            "accuracy": geolocation.accuracy,
        }
    )


def realistic_user_agent(reported: str) -> str:
    """Return the user agent a visible Chrome of the same version would send.

    Args:
        reported: navigator.userAgent of the running browser.

    Returns:
        The same string with the headless marker replaced by the regular product name.
    """
    return reported.replace(HEADLESS_MARKER, "Chrome/")


def user_agent_override(agent: str) -> dict[str, object]:
    """Build the DevTools user agent override, client hints included, for a Chrome user agent.

    Args:
        agent: Realistic user agent string.

    Returns:
        Parameters of Emulation.setUserAgentOverride with German languages.
    """
    match = CHROME_VERSION.search(agent)
    major = match.group(1) if match else "0"
    full = match.group(0).removeprefix("Chrome/") if match else "0.0.0.0"
    platform = "Windows" if "Windows" in agent else "macOS" if "Mac OS X" in agent else "Linux"
    return {
        "userAgent": agent,
        "acceptLanguage": ACCEPT_LANGUAGE,
        "platform": {"Windows": "Win32", "macOS": "MacIntel"}.get(platform, "Linux x86_64"),
        "userAgentMetadata": {
            "brands": [
                {"brand": "Chromium", "version": major},
                NOT_A_BRAND,
            ],
            "fullVersionList": [
                {"brand": "Chromium", "version": full},
                {"brand": NOT_A_BRAND["brand"], "version": "24.0.0.0"},
            ],
            "platform": platform,
            "platformVersion": "",
            "architecture": "x86",
            "model": "",
            "mobile": False,
            "bitness": "64",
        },
    }


def find_consent_choice(html: str) -> str | None:
    """Return the label of the consent button to click, or None when no dialog is shown.

    Rejecting is preferred because it keeps the results free of personalisation.

    Args:
        html: Page source.

    Returns:
        A reject label, otherwise an accept label, otherwise None.
    """
    soup = BeautifulSoup(html, "lxml")
    labels = {
        collapse_whitespace(element.get_text(" "))
        for element in soup.select('button, [role="button"]')
    }
    labels.update(
        collapse_whitespace(str(element.get("value", "")))
        for element in soup.select('input[type="submit"]')
    )
    return next((label for label in (*REJECT_LABELS, *ACCEPT_LABELS) if label in labels), None)


def is_blocked_page(url: str, html: str) -> bool:
    """Return True for Google's "sorry" captcha page or a comparable bot check."""
    path = urlsplit(url).path
    return any(marker in path for marker in BLOCK_PATH_MARKERS) or any(
        marker in html for marker in BLOCK_MARKERS
    )


def ensure_not_blocked(url: str, html: str) -> None:
    """Raise SearchBlockedError when the page is a bot check.

    Raises:
        SearchBlockedError: If `is_blocked_page` is true.
    """
    if is_blocked_page(url, html):
        raise SearchBlockedError(f"{urlsplit(url).hostname or 'page'} shows a bot check", url)
