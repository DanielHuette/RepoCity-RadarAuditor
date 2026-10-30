"""Tests for asking ChatGPT, Gemini and Perplexity on their websites.

Context: Unit tests on saved-shape fake pages and a fake browser; no Chromium, no network. |
Input: Fixtures. | Output: Assertions.
"""

import pytest
from conftest import BrowserFactory, FakeDocument

from radarauditor.collectors import assistant_pages
from radarauditor.collectors.assistant_pages import CHATGPT, GEMINI, PERPLEXITY
from radarauditor.errors import LoginWallError, SearchBlockedError, TransientCollectionError

QUESTION = "Wer ist ein guter Dachdecker in Münster?"
CHATGPT_START = (
    '<html><body><form><textarea name="prompt" placeholder="ChatGPT fragen"></textarea>'
    "</form></body></html>"
)
CHATGPT_ANSWER = """
<html><body><ol>
<li data-message-role="user" id="u1">Wer ist ein guter Dachdecker in Münster?</li>
<li data-message-role="assistant" id="a1" data-assistant-content-started="" {done}>
<p>Empfehlenswert ist die <strong>Dachdeckerei Hollenbeck GmbH</strong>, Tel. 0251 123456.</p>
<button><img src="https://www.google.com/s2/favicons?domain=https://www.gelbeseiten.de&amp;sz=32">
<span>gelbeseiten.de</span></button>
<a href="https://www.mapbox.com/about/maps">Mapbox</a>
<a href="https://hollenbeck.de/">Website</a>
<script>window.x = 1</script>
</li></ol>
<form><textarea name="prompt"></textarea></form></body></html>
"""
CHATGPT_STREAMING = CHATGPT_ANSWER.format(done="").replace(", Tel. 0251 123456", "")
CHATGPT_DONE = CHATGPT_ANSWER.format(done="data-message-complete")
GEMINI_ANSWER = """
<html><body><model-response><message-content>
<p>In Münster arbeitet die Dachdeckerei Hollenbeck.
<button aria-label="Genaue Quellenangaben für Zitate aus WerkenntdenBesten.de und Gelbe Seiten
ansehen. Drücke die Eingabetaste, um das Dialogfeld „Quellen“ zu öffnen.">
<span class="source-title">WerkenntdenBesten.de</span><span>+ 1</span></button>
<button aria-label="Genaue Quellenangaben für Zitat aus www.hollenbeck.de ansehen.">
<span class="source-title">www.hollenbeck.de</span></button>
<button class="other">Trustlocal</button></p>
</message-content><message-actions></message-actions></model-response>
<div class="ql-editor" contenteditable="true" role="textbox"></div></body></html>
"""
PERPLEXITY_START = '<html><body><div id="ask-input" contenteditable="true"></div></body></html>'
PERPLEXITY_ANSWER = """
<html><body><div data-renderer="lm" class="prose"><p>Die Dachdeckerei Hollenbeck
<a href="https://www.gelbeseiten.de/hollenbeck">gelbeseiten</a>
<a href="https://www.perplexity.ai/page/x">intern</a></p></div>
<button aria-label="Anfrage kopieren"></button><button aria-label="Kopieren"></button>
<div id="ask-input" contenteditable="true"></div></body></html>
"""
PERPLEXITY_WALL = """
<html><body><div data-renderer="lm"><p>Melde dich an und wiederhole deine Anfrage.</p></div>
<button aria-label="Kopieren"></button></body></html>
"""


def test_read_answer_of_chatgpt_takes_links_and_favicon_citations() -> None:
    """Favicon pills become sources; map credits and scripts are left out."""
    reading = assistant_pages.read_answer(CHATGPT_DONE, CHATGPT)

    assert reading.complete is True
    assert reading.text.startswith("Empfehlenswert ist die Dachdeckerei Hollenbeck GmbH")
    assert "window.x" not in reading.text
    assert reading.sources == ["https://hollenbeck.de/", "https://www.gelbeseiten.de"]


def test_read_answer_of_chatgpt_is_incomplete_while_streaming() -> None:
    """Without the completion mark the answer is still being written."""
    assert assistant_pages.read_answer(CHATGPT_STREAMING, CHATGPT).complete is False


def test_read_answer_of_gemini_turns_citation_labels_into_sources() -> None:
    """Every source of a citation button counts; domain labels become addresses."""
    reading = assistant_pages.read_answer(GEMINI_ANSWER, GEMINI)

    assert reading.complete is True
    assert reading.sources == [
        "https://werkenntdenbesten.de",
        "Gelbe Seiten",
        "https://www.hollenbeck.de",
    ]


def test_read_answer_of_perplexity_ignores_its_own_links() -> None:
    """Links to perplexity.ai are not sources; the copy button marks the end."""
    reading = assistant_pages.read_answer(PERPLEXITY_ANSWER, PERPLEXITY)

    assert reading.complete is True
    assert reading.sources == ["https://www.gelbeseiten.de/hollenbeck"]


def test_read_answer_without_an_answer_is_empty() -> None:
    """A page without an answer container reads as empty and incomplete."""
    reading = assistant_pages.read_answer(CHATGPT_START, CHATGPT)

    assert (reading.text, reading.sources, reading.complete) == ("", [], False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Melde dich an und wiederhole deine Anfrage.", True),
        ("Please sign in to continue.", True),
        ("Die Dachdeckerei Hollenbeck ist gut.", False),
        ("Melde dich an " + "x" * 300, False),
    ],
)
def test_is_login_request_only_matches_short_login_answers(text: str, expected: bool) -> None:
    """A long answer that happens to contain a phrase is a real answer."""
    assert assistant_pages.is_login_request(text) is expected


def test_ensure_answerable_detects_login_pages_and_challenges() -> None:
    """A sign-in address is a login wall; a Cloudflare challenge is a bot check."""
    with pytest.raises(LoginWallError, match="auth.openai.com"):
        assistant_pages.ensure_answerable("https://auth.openai.com/log-in", "<html></html>")
    with pytest.raises(SearchBlockedError, match="bot check"):
        assistant_pages.ensure_answerable(
            "https://chatgpt.com/", "<html><title>Just a moment...</title></html>"
        )
    assistant_pages.ensure_answerable("https://chatgpt.com/", CHATGPT_START)


async def test_ask_on_site_types_the_question_and_waits_for_the_complete_answer(
    fake_browser: BrowserFactory,
) -> None:
    """The question is typed key by key, sent with Enter and read once ChatGPT marks it done."""
    fake = fake_browser(
        [FakeDocument(CHATGPT_START, after_submit=["", CHATGPT_STREAMING, CHATGPT_DONE])]
    )

    async with fake.service as service:
        answer = await assistant_pages.ask_on_site(service, CHATGPT, QUESTION, 60)

    assert "0251 123456" in answer.text
    assert answer.sources == ["https://hollenbeck.de/", "https://www.gelbeseiten.de"]
    assert fake.page.visited == [CHATGPT.url]
    assert "".join(fake.page.typed) == QUESTION
    assert fake.page.clicked == [CHATGPT.composer]
    assert fake.page.pressed == ["Enter"]
    assert fake.waits.count(assistant_pages.POLL_SECONDS) == 3
    assert fake.page.closed is True


async def test_ask_on_site_raises_on_a_login_answer(fake_browser: BrowserFactory) -> None:
    """Perplexity logged out answers only with a login request."""
    fake = fake_browser([FakeDocument(PERPLEXITY_START, after_submit=[PERPLEXITY_WALL])])

    async with fake.service as service:
        with pytest.raises(LoginWallError, match="perplexity asks to sign in"):
            await assistant_pages.ask_on_site(service, PERPLEXITY, QUESTION, 60)


async def test_ask_on_site_gives_up_after_the_timeout(fake_browser: BrowserFactory) -> None:
    """An answer that never completes is a temporary failure after timeout / poll interval."""
    fake = fake_browser([FakeDocument(CHATGPT_START, after_submit=[CHATGPT_STREAMING])])

    async with fake.service as service:
        with pytest.raises(TransientCollectionError, match="did not finish within 9 s"):
            await assistant_pages.ask_on_site(service, CHATGPT, QUESTION, 9)

    assert fake.waits.count(assistant_pages.POLL_SECONDS) == 3


async def test_ask_on_site_without_question_field_is_a_login_wall(
    fake_browser: BrowserFactory,
) -> None:
    """A page that never shows the question field is treated as a login page."""
    fake = fake_browser(["<html><body>Bitte anmelden</body></html>"])

    async with fake.service as service:
        with pytest.raises(LoginWallError, match="no question field"):
            await assistant_pages.ask_on_site(service, GEMINI, QUESTION, 60)

    assert fake.page.typed == []
    assert fake.waits.count(assistant_pages.POLL_SECONDS) == assistant_pages.COMPOSER_POLLS - 1


async def test_ask_on_site_detects_a_redirect_to_the_sign_in_page(
    fake_browser: BrowserFactory,
) -> None:
    """Gemini sending the browser to accounts.google.com is a login wall."""
    fake = fake_browser(
        [FakeDocument("<html><body>Anmelden</body></html>", url="https://accounts.google.com/x")]
    )

    async with fake.service as service:
        with pytest.raises(LoginWallError, match="accounts.google.com"):
            await assistant_pages.ask_on_site(service, GEMINI, QUESTION, 60)
