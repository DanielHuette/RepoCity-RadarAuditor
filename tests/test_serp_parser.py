"""Tests for the search parsers and the searches on the shared browser.

Context: Unit tests on saved pages and a fake browser, no network. | Input: Fixtures. |
Output: Assertions.
"""

import base64
from pathlib import Path

import pytest
from conftest import BrowserFactory
from playwright.async_api import Error as PlaywrightError

from radarauditor.collectors import serp
from radarauditor.errors import SearchBlockedError, TransientCollectionError

CONSENT_HTML = """
<html><body><div class="consent">
<h1>Bevor Sie zu Google weitergehen</h1>
<form><button>Alle ablehnen</button></form>
<form><button>Alle akzeptieren</button></form>
</div></body></html>
"""
CAPTCHA_HTML = """
<html><body><div>Our systems have detected unusual traffic from your computer network.</div>
<form id="captcha-form"><div class="g-recaptcha"></div></form></body></html>
"""


@pytest.fixture
def google_html(fixtures_dir: Path) -> str:
    """Saved Google result page for the phrase Dachdecker Münster."""
    return (fixtures_dir / "google_dachdecker_muenster.html").read_text(encoding="utf-8")


def _bing_redirect(target: str) -> str:
    encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    return f"https://www.bing.com/ck/a?!&&p=abc&u=a1{encoded}&ntb=1"


def test_parse_result_page_reads_organic_results_by_distinct_domain(google_html: str) -> None:
    """`parse_result_page` reads organic results by distinct domain."""
    result = serp.parse_result_page(google_html, "Dachdecker Münster")

    assert result.phrase == "Dachdecker Münster"
    assert result.engine == "google"
    assert result.organic == [
        (1, "dachdecker-hollenbeck.de"),
        (2, "bedachungen-overmann.de"),
        (3, "gelbeseiten.de"),
        (4, "dach-kemper.de"),
    ]


def test_parse_result_page_reads_local_pack(google_html: str) -> None:
    """`parse_result_page` reads local pack."""
    result = serp.parse_result_page(google_html, "Dachdecker Münster")

    assert result.local_pack == [
        (1, "Dachdeckerei Hollenbeck GmbH"),
        (2, "Bedachungen Overmann"),
        (3, "Dach Service Kemper"),
    ]


def test_parse_result_page_reads_ai_overview(google_html: str) -> None:
    """`parse_result_page` reads ai overview."""
    result = serp.parse_result_page(google_html, "Dachdecker Münster")

    assert result.ai_overview_present is True
    assert result.ai_overview_mentions[:2] == [
        "Dachdeckerei Hollenbeck GmbH",
        "Bedachungen Overmann",
    ]
    assert "Dach Service Kemper" in result.ai_overview_mentions
    assert "Dachdeckerei Lüttke" in result.ai_overview_mentions
    assert not any(mention.endswith("Häufig") for mention in result.ai_overview_mentions)
    assert all("Übersicht" not in mention for mention in result.ai_overview_mentions)
    assert len(result.ai_overview_mentions) <= serp.MAX_AI_MENTIONS


def test_parse_result_page_reads_people_also_ask(google_html: str) -> None:
    """`parse_result_page` reads people also ask."""
    result = serp.parse_result_page(google_html, "Dachdecker Münster")

    assert result.people_also_ask == [
        "Was kostet ein Dachdecker pro Stunde in Münster?",
        "Wie teuer ist eine Dachsanierung pro m²?",
        "Wann ist die beste Zeit für Dacharbeiten?",
    ]


def test_parse_result_page_without_features_returns_empty_lists() -> None:
    """`parse_result_page` without features returns empty lists."""
    html = """<html><body><div id="search"><div id="rso"><div class="g">
    <a href="https://example.de/"><h3>Example</h3></a></div></div></div></body></html>"""

    result = serp.parse_result_page(html, "x")

    assert result.organic == [(1, "example.de")]
    assert result.local_pack == []
    assert result.ai_overview_present is False
    assert result.ai_overview_mentions == []
    assert result.people_also_ask == []
    assert result.ads == []
    assert result.knowledge_panel is False
    assert result.vertical == "web"


BRAND_HTML = """<html><body>
<div id="tads">
  <div data-text-ad="1">
    <a href="https://www.googleadservices.com/pagead/aclk?sa=L&amp;adurl=https://www.dach-schmidt.de/">
      <div role="heading">Dach Schmidt</div></a>
    <span>Gesponsert</span>
  </div>
  <div data-text-ad="1" data-dtld="bedachungen-meier.de">
    <a href="/aclk?sa=L"><div role="heading">Meier</div></a>
  </div>
</div>
<div id="bottomads"><div><span>Anzeige</span>
  <div><cite>www.krause-bau.de › dach</cite><a href="/aclk?x=1">Mehr</a></div>
</div></div>
<div id="search"><div id="rso"><div class="g">
<a href="https://www.dachdeckerei-muster.de/"><h3>Dachdeckerei Muster</h3></a></div></div></div>
<div id="rhs"><h2 data-attrid="title">Dachdeckerei Muster GmbH</h2></div>
</body></html>"""


def test_parse_result_page_reads_ads_and_knowledge_panel() -> None:
    """Sponsored blocks give advertiser domains; the right-hand panel is detected."""
    result = serp.parse_result_page(BRAND_HTML, "Dachdeckerei Muster")

    assert result.ads == ["dach-schmidt.de", "bedachungen-meier.de", "krause-bau.de"]
    assert result.knowledge_panel is True
    assert result.organic == [(1, "dachdeckerei-muster.de")]


def test_parse_result_page_detects_panel_by_heading_in_right_column() -> None:
    """A right-hand column with a heading counts as panel; an empty one does not."""
    with_heading = '<html><body><div id="rhs"><div role="heading">Muster</div></div></body></html>'
    empty = '<html><body><div id="rhs"><p>Weitere Suchanfragen</p></div></body></html>'

    assert serp.parse_result_page(with_heading, "x").knowledge_panel is True
    assert serp.parse_result_page(empty, "x").knowledge_panel is False


def test_parse_result_page_names_advertiser_without_link_target() -> None:
    """A sponsored block without any domain falls back to its heading."""
    html = """<html><body><div><span>Gesponsert</span>
    <a href="/aclk?sa=L"><div role="heading">Dachprofi Nord</div></a></div></body></html>"""

    assert serp.parse_result_page(html, "x").ads == ["Dachprofi Nord"]


IMAGES_HTML = """<html><body><div id="search">
<div data-lpage="https://www.dachdeckerei-muster.de/referenzen/"><a href="https://www.dachdeckerei-muster.de/referenzen/">Bild</a></div>
<div data-lpage="https://www.pinterest.de/pin/1"></div>
<a href="/url?q=https://www.dachdeckerei-muster.de/galerie/">Bild</a>
<a href="https://www.google.de/imgres?x=1">Google</a>
<div data-lpage="https://www.houzz.de/foto/2"></div>
</div></body></html>"""


def test_parse_image_page_lists_source_domains_once() -> None:
    """Image results give their source domains in order, without Google's own hosts."""
    result = serp.parse_image_page(IMAGES_HTML, "Dachdecker Münster")

    assert result.vertical == "images"
    assert result.organic == [(1, "dachdeckerei-muster.de"), (2, "pinterest.de"), (3, "houzz.de")]
    assert result.local_pack == []


def test_parse_image_page_reads_only_the_first_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """Results beyond the limit are ignored."""
    monkeypatch.setattr(serp, "IMAGE_RESULT_LIMIT", 2)

    result = serp.parse_image_page(IMAGES_HTML, "Dachdecker Münster")

    assert result.organic == [(1, "dachdeckerei-muster.de"), (2, "pinterest.de")]


def test_parse_image_page_on_captcha_raises() -> None:
    """A captcha instead of image results is a temporary failure."""
    with pytest.raises(SearchBlockedError):
        serp.parse_image_page(CAPTCHA_HTML, "Dachdecker Münster")


def test_parse_result_page_falls_back_to_local_block_heading() -> None:
    """`parse_result_page` falls back to local block heading."""
    html = """<html><body><div id="search"><div id="rso"><div class="MjjYud">
    <div><div role="heading" aria-level="2">Orte</div>
    <div class="entry"><span role="heading" aria-level="3">Malerbetrieb Sander</span></div>
    <div class="entry"><span role="heading" aria-level="3">Farbwerk Münster</span></div>
    </div></div></div></div></body></html>"""

    result = serp.parse_result_page(html, "Maler Münster")

    assert result.local_pack == [(1, "Malerbetrieb Sander"), (2, "Farbwerk Münster")]


def test_parse_result_page_on_consent_page_raises() -> None:
    """`parse_result_page` on consent page raises."""
    with pytest.raises(TransientCollectionError, match="consent"):
        serp.parse_result_page(CONSENT_HTML, "x")


def test_parse_result_page_on_captcha_raises() -> None:
    """`parse_result_page` on captcha raises."""
    with pytest.raises(SearchBlockedError, match="captcha"):
        serp.parse_result_page(CAPTCHA_HTML, "x")


def test_parse_bing_page_resolves_redirects_and_cites() -> None:
    """`parse_bing_page` resolves redirects and cites."""
    html = f"""<html><body><ol id="b_results">
    <li class="b_algo"><h2><a href="{_bing_redirect("https://www.dachdecker-hollenbeck.de/")}">
    Hollenbeck</a></h2><cite>https://www.dachdecker-hollenbeck.de</cite></li>
    <li class="b_algo"><h2><a href="https://bedachungen-overmann.de/">Overmann</a></h2></li>
    <li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=zz">Kemper</a></h2>
    <cite>dach-kemper.de › notdienst</cite></li>
    <li class="b_algo"><h2><a href="https://dachdecker-hollenbeck.de/kontakt">dup</a></h2></li>
    </ol></body></html>"""

    result = serp.parse_bing_page(html, "Dachdecker Münster")

    assert result.engine == "bing"
    assert result.organic == [
        (1, "dachdecker-hollenbeck.de"),
        (2, "bedachungen-overmann.de"),
        (3, "dach-kemper.de"),
    ]
    assert result.local_pack == []


def test_parse_bing_page_on_captcha_raises() -> None:
    """`parse_bing_page` on captcha raises."""
    with pytest.raises(SearchBlockedError):
        serp.parse_bing_page(CAPTCHA_HTML, "x")


def test_ai_overview_names_strips_articles_and_single_words() -> None:
    """`ai_overview_names` strips articles and single words."""
    names = serp.ai_overview_names(
        "Die Dachdeckerei Hollenbeck GmbH ist bekannt. Laut Kunden ist Overmann gut."
    )

    assert names == ["Dachdeckerei Hollenbeck GmbH"]


SEARCH_PHRASES = ["Dachdecker Münster", "Dachsanierung Münster", "Flachdach Münster"]


async def test_search_all_handles_consent_and_parses_every_phrase(
    fake_browser: BrowserFactory, google_html: str
) -> None:
    """`search_all` answers consent once, pauses between phrases and parses every page."""
    fake = fake_browser([CONSENT_HTML, google_html, google_html])

    async with fake.service as service:
        batch = await serp.search_all(service, SEARCH_PHRASES[:2])

    assert [result.phrase for result in batch.results] == SEARCH_PHRASES[:2]
    assert batch.deferred == []
    assert batch.reason is None
    assert fake.page.clicked == ["Alle ablehnen"]
    assert fake.page.visited[0].startswith("https://www.google.de/search?q=Dachdecker+M%C3%BCnster")
    assert "hl=de&gl=de&num=20" in fake.page.visited[0]
    assert batch.results[0].organic[0] == (1, "dachdecker-hollenbeck.de")
    assert len(fake.page.mouse.scrolled) == 2
    pacing = fake.service.pacing
    pauses = [wait for wait in fake.waits if wait >= pacing.between_actions]
    assert len(pauses) == 1


async def test_search_all_defers_the_rest_when_google_blocks(
    fake_browser: BrowserFactory, google_html: str
) -> None:
    """A captcha keeps the results so far and defers the blocked phrase and all after it."""
    fake = fake_browser([google_html, CAPTCHA_HTML])

    async with fake.service as service:
        batch = await serp.search_all(service, SEARCH_PHRASES)

    assert [result.phrase for result in batch.results] == ["Dachdecker Münster"]
    assert batch.deferred == ["Dachsanierung Münster", "Flachdach Münster"]
    assert batch.reason == "www.google.de shows a bot check"
    assert len(fake.page.visited) == 2


async def test_search_all_defers_everything_when_the_browser_fails(
    fake_browser: BrowserFactory,
) -> None:
    """A browser error defers the phrases instead of dropping them."""
    fake = fake_browser([])
    fake.page.failure = PlaywrightError("net::ERR_TIMED_OUT")

    async with fake.service as service:
        batch = await serp.search_all(service, SEARCH_PHRASES[:1])

    assert batch.results == []
    assert batch.deferred == SEARCH_PHRASES[:1]
    assert batch.reason == "browser failed: net::ERR_TIMED_OUT"


async def test_search_all_without_phrases_skips_the_browser(fake_browser: BrowserFactory) -> None:
    """`search_all` without phrases never starts the browser."""
    fake = fake_browser([])

    async with fake.service as service:
        batch = await serp.search_all(service, [])

    assert batch == serp.SearchBatch([], [])
    assert fake.context.opened_with == []


async def test_search_bing_uses_bing_url(fake_browser: BrowserFactory) -> None:
    """`search_bing` opens Bing in German and reads the organic results."""
    fake = fake_browser(['<li class="b_algo"><h2><a href="https://example.de/">E</a></h2></li>'])

    async with fake.service as service:
        batch = await serp.search_bing(service, SEARCH_PHRASES[:1])

    assert fake.page.visited[0].startswith("https://www.bing.com/search?q=Dachdecker+M%C3%BCnster")
    assert "setlang=de&cc=de" in fake.page.visited[0]
    assert batch.results[0].organic == [(1, "example.de")]
    assert batch.results[0].engine == "bing"


async def test_search_images_uses_image_vertical(fake_browser: BrowserFactory) -> None:
    """`search_images` opens Google Images and returns image results."""
    fake = fake_browser([IMAGES_HTML])

    async with fake.service as service:
        batch = await serp.search_images(service, SEARCH_PHRASES[:1])

    assert fake.page.visited[0].startswith("https://www.google.de/search?q=Dachdecker+M%C3%BCnster")
    assert "tbm=isch" in fake.page.visited[0]
    assert batch.results[0].vertical == "images"
    assert batch.results[0].organic[0] == (1, "dachdeckerei-muster.de")


def test_deferral_note_names_count_and_reason() -> None:
    """The note marks deferred searches so a retry can replace it."""
    note = serp.deferral_note(2, "www.google.de shows a bot check")

    assert note == "2 searches deferred to a retry: www.google.de shows a bot check"
    assert serp.DEFERRAL_NOTE in note
