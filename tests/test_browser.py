"""Tests for the shared hardened browser service.

Context: Unit tests on a fake browser context; no Chromium, no network. | Input: Fixtures. |
Output: Assertions.
"""

import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import CAPTCHA_PAGE, HEADLESS_USER_AGENT, BrowserFactory, FakeDocument

from radarauditor.collectors import browser
from radarauditor.collectors.browser import Geolocation, Pacing
from radarauditor.errors import SearchBlockedError, TransientCollectionError

SEARCH_URL = "https://www.google.de/search?q=Dachdecker+M%C3%BCnster&hl=de&gl=de&num=20"
RESULTS_HTML = '<html><body><div id="search"><a href="https://example.de/"><h3>E</h3></a></div>'
CONSENT_HTML = """
<html><body><h1>Bevor Sie zu Google weitergehen</h1>
<form><button>Alle akzeptieren</button></form>
<form><button>Alle ablehnen</button></form>
</body></html>
"""
ACCEPT_ONLY_HTML = '<html><body><input type="submit" value="Alle akzeptieren"></body></html>'
ENGLISH_CONSENT_HTML = "<html><body><button>Accept all</button><button>Reject all</button></body>"
CONSENT_URL = "https://consent.google.com/ml?continue=https://www.google.de/search"


async def test_browser_starts_only_with_the_first_page(fake_browser: BrowserFactory) -> None:
    """Entering the service opens nothing; the first page starts the context, leaving closes it."""
    fake = fake_browser([RESULTS_HTML])

    async with fake.service as service:
        assert fake.context.opened_with == []
        async with service.page():
            pass

    assert len(fake.context.opened_with) == 1
    profile, headless = fake.context.opened_with[0]
    assert profile.is_dir()
    assert headless is True
    assert fake.context.closed is True
    assert fake.page.closed is True


async def test_page_hides_webdriver_and_sends_a_regular_chrome_identity(
    fake_browser: BrowserFactory,
) -> None:
    """The init script hides automation; the headless marker never reaches a website."""
    fake = fake_browser([])

    async with fake.service as service:
        async with service.page():
            pass
        async with service.page():
            pass

    assert fake.context.init_scripts == [browser.HIDE_WEBDRIVER_SCRIPT]
    assert len(fake.context.cdp.sent) == 2
    method, params = fake.context.cdp.sent[0]
    assert method == "Emulation.setUserAgentOverride"
    assert params["userAgent"] == HEADLESS_USER_AGENT.replace("HeadlessChrome", "Chrome")
    assert params["acceptLanguage"] == browser.ACCEPT_LANGUAGE


async def test_page_of_a_visible_browser_keeps_its_identity(fake_browser: BrowserFactory) -> None:
    """A user agent without the headless marker needs no override."""
    fake = fake_browser([], user_agent=HEADLESS_USER_AGENT.replace("HeadlessChrome", "Chrome"))

    async with fake.service as service, service.page():
        pass

    assert fake.context.cdp.sent == []


async def test_page_reports_the_requested_position_only(fake_browser: BrowserFactory) -> None:
    """A geolocation is granted and set for its page; the next page reports none."""
    fake = fake_browser([])

    async with fake.service as service:
        async with service.page(geolocation=Geolocation(51.96, 7.63)):
            pass
        async with service.page():
            pass

    assert fake.context.permissions == [["geolocation"]]
    assert fake.context.geolocations == [
        {"latitude": 51.96, "longitude": 7.63, "accuracy": browser.GEOLOCATION_ACCURACY_METRES},
        None,
    ]


async def test_open_rejects_consent_on_the_consent_page(fake_browser: BrowserFactory) -> None:
    """The redirect to consent.google.com is answered with "Alle ablehnen"."""
    fake = fake_browser(
        [FakeDocument(CONSENT_HTML, url=CONSENT_URL), FakeDocument(RESULTS_HTML, url=SEARCH_URL)]
    )

    async with fake.service as service, service.page() as page:
        html = await service.open(page, SEARCH_URL)

    assert html == RESULTS_HTML
    assert fake.page.clicked == ["Alle ablehnen"]
    assert fake.page.visited == [SEARCH_URL]


async def test_open_reloads_the_search_when_consent_stays_on_its_host(
    fake_browser: BrowserFactory,
) -> None:
    """When answering consent does not lead back, the search is opened again."""
    fake = fake_browser(
        [
            FakeDocument(CONSENT_HTML, url=CONSENT_URL),
            FakeDocument("<html><body>Danke</body></html>", url=CONSENT_URL),
            FakeDocument(RESULTS_HTML),
        ]
    )

    async with fake.service as service, service.page() as page:
        html = await service.open(page, SEARCH_URL)

    assert html == RESULTS_HTML
    assert fake.page.visited == [SEARCH_URL, SEARCH_URL]


async def test_open_accepts_an_overlay_without_reject_button(fake_browser: BrowserFactory) -> None:
    """An in-page dialog that only offers accepting is accepted."""
    fake = fake_browser([ACCEPT_ONLY_HTML, RESULTS_HTML])

    async with fake.service as service, service.page() as page:
        html = await service.open(page, SEARCH_URL)

    assert html == RESULTS_HTML
    assert fake.page.clicked == ["Alle akzeptieren"]


async def test_open_returns_pages_without_dialog_unchanged(fake_browser: BrowserFactory) -> None:
    """A result page is returned as it is, without clicks."""
    fake = fake_browser([RESULTS_HTML])

    async with fake.service as service, service.page() as page:
        assert await service.open(page, SEARCH_URL) == RESULTS_HTML

    assert fake.page.clicked == []


@pytest.mark.parametrize(
    "document",
    [
        FakeDocument("<html></html>", url="https://www.google.de/sorry/index?continue=x"),
        FakeDocument(CAPTCHA_PAGE),
        FakeDocument("<html>Too many requests</html>", status=429),
    ],
    ids=["sorry-page", "captcha", "http-429"],
)
async def test_open_raises_search_blocked_on_bot_checks(
    fake_browser: BrowserFactory, document: FakeDocument
) -> None:
    """Sorry page, captcha markup and HTTP 429 raise SearchBlockedError."""
    fake = fake_browser([document])

    async with fake.service as service, service.page() as page:
        with pytest.raises(SearchBlockedError) as raised:
            await service.open(page, SEARCH_URL)

    assert isinstance(raised.value, TransientCollectionError)
    assert raised.value.url


async def test_open_detects_a_block_behind_the_consent_dialog(fake_browser: BrowserFactory) -> None:
    """A captcha shown after the consent answer is still detected."""
    fake = fake_browser([FakeDocument(CONSENT_HTML), FakeDocument(CAPTCHA_PAGE, url=SEARCH_URL)])

    async with fake.service as service, service.page() as page:
        with pytest.raises(SearchBlockedError):
            await service.open(page, SEARCH_URL)


async def test_pacing_waits_with_jitter(fake_browser: BrowserFactory) -> None:
    """pause, linger and typing wait base plus a share of the jitter; linger scrolls."""
    fake = fake_browser([])
    pacing = fake.service.pacing

    async with fake.service as service, service.page() as page:
        await service.pause()
        await service.linger(page)
        await service.type_text(page.get_by_role("textbox", name="Suche"), "Dach")

    pause, linger, *keystrokes = fake.waits
    assert pacing.between_actions <= pause <= pacing.between_actions + pacing.between_actions_jitter
    assert pacing.linger <= linger <= pacing.linger + pacing.linger_jitter
    assert len(keystrokes) == 4
    assert all(
        pacing.keystroke <= wait <= pacing.keystroke + pacing.keystroke_jitter
        for wait in keystrokes
    )
    assert len(set(keystrokes)) > 1
    assert fake.page.typed == ["D", "a", "c", "h"]
    low, high = pacing.scroll_pixels
    assert low <= fake.page.mouse.scrolled[0] <= high


def test_pacing_jitter_stays_in_range() -> None:
    """Jittered draws from [base, base + jitter]."""
    pacing = Pacing(rng=random.Random(1))

    draws = [pacing.jittered(2.0, 3.0) for _ in range(200)]

    assert min(draws) >= 2.0
    assert max(draws) <= 5.0


async def test_persistent_chromium_launches_a_german_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Chromium opens the profile with German locale, Berlin time and no automation flag."""
    launched: dict[str, Any] = {}

    class Context:
        closed = False

        async def close(self) -> None:
            self.closed = True

    context = Context()

    class Chromium:
        async def launch_persistent_context(self, user_data_dir: str, **options: Any) -> Context:
            launched.update(options, user_data_dir=user_data_dir)
            return context

    class Playwright:
        chromium = Chromium()

    @asynccontextmanager
    async def fake_playwright() -> AsyncIterator[Playwright]:
        yield Playwright()

    monkeypatch.setattr(browser, "async_playwright", fake_playwright)

    async with browser.persistent_chromium(tmp_path, True) as opened:
        assert opened is context

    assert context.closed is True
    assert launched["user_data_dir"] == str(tmp_path)
    assert launched["channel"] == "chromium"
    assert launched["locale"] == "de-DE"
    assert launched["timezone_id"] == "Europe/Berlin"
    assert launched["ignore_default_args"] == ["--enable-automation"]
    assert "--disable-blink-features=AutomationControlled" in launched["args"]
    assert launched["extra_http_headers"]["Accept-Language"].startswith("de-DE")


def test_user_agent_override_carries_client_hints() -> None:
    """The override names the same Chromium version in the string and in the client hints."""
    agent = browser.realistic_user_agent(HEADLESS_USER_AGENT)

    override = browser.user_agent_override(agent)

    assert "HeadlessChrome" not in agent
    assert override["platform"] == "Linux x86_64"
    metadata = override["userAgentMetadata"]
    assert isinstance(metadata, dict)
    assert {"brand": "Chromium", "version": "156"} in metadata["brands"]
    assert metadata["platform"] == "Linux"
    assert metadata["mobile"] is False


def test_find_consent_choice_prefers_reject() -> None:
    """Rejecting wins over accepting, in German and in English."""
    assert browser.find_consent_choice(CONSENT_HTML) == "Alle ablehnen"
    assert browser.find_consent_choice(ENGLISH_CONSENT_HTML) == "Reject all"


def test_find_consent_choice_reads_submit_inputs() -> None:
    """Submit inputs count as buttons."""
    assert browser.find_consent_choice(ACCEPT_ONLY_HTML) == "Alle akzeptieren"


def test_find_consent_choice_on_result_page_is_none() -> None:
    """A page without dialog needs no answer."""
    assert browser.find_consent_choice(RESULTS_HTML) is None


def test_is_blocked_page_detects_sorry_url_and_captcha_markup() -> None:
    """The sorry path and captcha markup are bot checks; a result page is not."""
    assert browser.is_blocked_page("https://www.google.de/sorry/index?continue=x", "<html></html>")
    assert browser.is_blocked_page(SEARCH_URL, CAPTCHA_PAGE)
    assert browser.is_blocked_page(SEARCH_URL, "<p>ungewöhnlichen Datenverkehr</p>")
    assert not browser.is_blocked_page(SEARCH_URL, RESULTS_HTML)
