"""Tests for the AI assistant collector.

Context: Unit tests with fake assistant pages, a fake Claude Code CLI and mocked paid interfaces;
no network. | Input: Fixtures. | Output: Assertions.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx
from conftest import BrowserFactory, FakeBrowser, FakeDocument
from pydantic import SecretStr
from test_assistant_pages import (
    CHATGPT_DONE,
    CHATGPT_START,
    CHATGPT_STREAMING,
    PERPLEXITY_START,
    PERPLEXITY_WALL,
)

from radarauditor.collectors import assistant_pages, assistants
from radarauditor.collectors.quota import SourceGate
from radarauditor.config import Settings
from radarauditor.db.app_settings import Source, SourcesSettings
from radarauditor.errors import CollectionError, ConfigurationError, LoginWallError

pytestmark = pytest.mark.usefixtures("fast_retries")

QUESTIONS = ["Wer ist ein guter Dachdecker in Münster?"]
QUESTIONS_FILE = Path(__file__).resolve().parents[1] / "config" / "fragen.yaml"
GEMINI_URL = assistants.gemini_url("gemini-3.8-flash")
BUSINESS = "Dachdeckerei Hollenbeck GmbH"


def _gate(*sources: Source, cap: int | None = None) -> tuple[SourceGate, list[str]]:
    """Gate with only the given sources on; returns it with the log of counted requests."""
    counted: list[str] = []
    settings = SourcesSettings.model_validate(
        {source.value: {"enabled": source in sources} for source in Source}
    )

    def consume(source: str, daily_cap: int) -> bool:
        counted.append(source)
        return cap is None or counted.count(source) <= cap

    return SourceGate(settings, consume), counted


@pytest.fixture
def templates() -> assistants.QuestionTemplates:
    """The real question templates from config/."""
    return assistants.load_question_templates(QUESTIONS_FILE)


@pytest.fixture
def two_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ask every question twice instead of REPEATS_PER_QUESTION times."""
    monkeypatch.setattr(assistants, "REPEATS_PER_QUESTION", 2)


@pytest.fixture
def paid_keys(collector_settings: Settings, fake_claude: Path) -> Settings:
    """Settings with every paid key, the fake Claude Code and a short assistant timeout."""
    return collector_settings.model_copy(
        update={
            "openai_api_key": SecretStr("openai-key"),
            "gemini_api_key": SecretStr("gemini-key"),
            "perplexity_api_key": SecretStr("perplexity-key"),
            "analysis_claude_command": str(fake_claude),
            "assistant_timeout_seconds": 6,
        }
    )


@pytest.fixture
def walled_browser(fake_browser: BrowserFactory) -> FakeBrowser:
    """A browser on which every assistant website shows a login wall."""
    fake = fake_browser([])
    fake.page.failure = LoginWallError("redirected to the sign-in page")
    return fake


def _chat(content: str, **extra: object) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], **extra})


def _perplexity(text: str, urls: list[str] | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "response",
            "output": [
                {
                    "type": "search_results",
                    "results": [{"id": 1, "url": url} for url in urls or []],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                },
            ],
        },
    )


def _gemini(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {"parts": [{"text": text}]},
                    "groundingMetadata": {
                        "groundingChunks": [{"web": {"uri": "https://grounding.example/x"}}]
                    },
                }
            ]
        },
    )


def _chatgpt_page() -> FakeDocument:
    return FakeDocument(CHATGPT_START, after_submit=[CHATGPT_STREAMING, CHATGPT_DONE])


def test_enabled_providers_follows_the_switches(paid_keys: Settings) -> None:
    """Keys do not matter any more; a switched-off source is left out."""
    gate, _ = _gate(Source.GEMINI, Source.PERPLEXITY, Source.CLAUDE)

    assert assistants.enabled_providers(paid_keys, gate) == ["gemini", "perplexity", "claude"]
    assert assistants.enabled_providers(paid_keys, _gate()[0]) == []


def test_build_questions_creates_three_recommendation_and_three_problem_questions(
    templates: assistants.QuestionTemplates,
) -> None:
    """`build_questions` creates three recommendation and three problem questions."""
    questions = assistants.build_questions(
        "Dachdecker",
        "Münster",
        ["Flachdach", "Dachsanierung", "Dachfenster", "Notdienst"],
        templates,
    )

    assert questions == [
        "Wer ist ein guter Dachdecker in Münster?",
        "Welchen Dachdecker in Münster empfiehlst du?",
        "Top 3 Dachdecker Münster",
        "Flachdach in Münster, wen beauftragen?",
        "Ich brauche Dachsanierung in Münster, wer macht das gut?",
        "Dachfenster Münster Erfahrungen",
    ]


def test_build_questions_without_services_uses_industry(
    templates: assistants.QuestionTemplates,
) -> None:
    """`build_questions` without services uses industry."""
    questions = assistants.build_questions("Maler", "Münster", [" "], templates)

    assert len(questions) == 6
    assert questions[3:] == [
        "Maler in Münster, wen beauftragen?",
        "Ich brauche Maler in Münster, wer macht das gut?",
        "Maler Münster Erfahrungen",
    ]


def test_build_questions_cycles_services_over_problem_templates() -> None:
    """Fewer services than problem templates are reused in turn; every placeholder works."""
    templates = assistants.QuestionTemplates(
        recommendation=["{industry} {city} {service}?"],
        problem=["{service} A", "{service} B", "{service} C"],
    )

    questions = assistants.build_questions("Maler", "Münster", ["Fassade", "Lack"], templates)

    assert questions == ["Maler Münster Fassade?", "Fassade A", "Lack B", "Fassade C"]


def test_load_question_templates_reads_both_lists(
    templates: assistants.QuestionTemplates,
) -> None:
    """The shipped file has recommendation and problem templates."""
    assert len(templates.recommendation) == 3
    assert len(templates.problem) == 3


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("empfehlung: [a\n", "not valid YAML"),
        ("empfehlung: ['Wer?']\n", "malformed"),
        ("empfehlung: []\nproblem: ['x']\n", "malformed"),
        ("empfehlung: ['{branche} in {city}']\nproblem: ['x']\n", "unknown placeholders"),
        ("empfehlung: ['{city']\nproblem: ['x']\n", "malformed"),
    ],
)
def test_load_question_templates_rejects_bad_files(
    tmp_path: Path, content: str, message: str
) -> None:
    """Broken YAML, missing lists and unknown or broken placeholders stop the run."""
    path = tmp_path / "fragen.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        assistants.load_question_templates(path)


def test_load_question_templates_rejects_missing_file(tmp_path: Path) -> None:
    """A missing file is a configuration error."""
    with pytest.raises(ConfigurationError, match="Cannot read"):
        assistants.load_question_templates(tmp_path / "fehlt.yaml")


def test_gemini_url_names_the_model() -> None:
    """The Gemini address carries the configured model."""
    assert assistants.gemini_url("gemini-x").endswith("/models/gemini-x:generateContent")


async def test_ask_all_without_providers_returns_nothing(
    paid_keys: Settings, fake_browser: BrowserFactory
) -> None:
    """With every assistant switched off nothing is asked."""
    fake = fake_browser([])

    assert await assistants.ask_all(QUESTIONS, BUSINESS, paid_keys, _gate()[0], fake.service) == []
    assert fake.page.visited == []


@pytest.mark.usefixtures("two_repeats")
async def test_ask_all_asks_chatgpt_in_the_browser(
    paid_keys: Settings, fake_browser: BrowserFactory
) -> None:
    """Every repeat opens a fresh chat; the answers carry the browser route and the sources."""
    fake = fake_browser([_chatgpt_page(), _chatgpt_page()])
    gate, counted = _gate(Source.CHATGPT)

    async with fake.service as browser, respx.mock:
        answers = await assistants.ask_all(QUESTIONS, BUSINESS, paid_keys, gate, browser)

    assert [answer.route for answer in answers] == ["browser", "browser"]
    assert all(answer.provider == "chatgpt" and answer.mentioned for answer in answers)
    assert answers[0].cited_sources == ["https://hollenbeck.de/", "https://www.gelbeseiten.de"]
    assert answers[0].question == QUESTIONS[0]
    assert fake.page.visited == [assistant_pages.CHATGPT.url] * 2
    assert counted == ["chatgpt", "chatgpt"]
    pause = fake.service.pacing.between_actions
    assert sum(1 for wait in fake.waits if wait >= pause) == 1


@pytest.mark.usefixtures("two_repeats")
@respx.mock
async def test_ask_all_falls_back_to_the_paid_interface_after_a_login_wall(
    paid_keys: Settings, fake_browser: BrowserFactory
) -> None:
    """Perplexity asking for a login sends this and every later question to its API."""
    fake = fake_browser([FakeDocument(PERPLEXITY_START, after_submit=[PERPLEXITY_WALL])])
    perplexity = respx.post(assistants.PERPLEXITY_URL).mock(
        return_value=_perplexity(f"{BUSINESS} [1]", ["https://www.gelbeseiten.de/x"])
    )
    gate, counted = _gate(Source.PERPLEXITY)

    async with fake.service as browser:
        answers = await assistants.ask_all(QUESTIONS, BUSINESS, paid_keys, gate, browser)

    assert [answer.route for answer in answers] == ["api", "api"]
    assert answers[0].cited_sources == ["https://www.gelbeseiten.de/x"]
    assert perplexity.call_count == 2
    assert fake.page.visited == [assistant_pages.PERPLEXITY.url]
    assert counted == ["perplexity"] * 3


async def test_ask_all_without_key_reports_the_blocked_browser(
    collector_settings: Settings, walled_browser: FakeBrowser
) -> None:
    """A login wall without a paid key leaves the assistant silent."""
    gate, counted = _gate(Source.GEMINI)

    with pytest.raises(CollectionError, match="no assistant answered: gemini: redirected"):
        await assistants.ask_all(
            QUESTIONS, BUSINESS, collector_settings, gate, walled_browser.service
        )

    assert counted == ["gemini"]


@respx.mock
async def test_ask_all_sends_a_single_timeout_to_the_paid_interface(
    paid_keys: Settings, fake_browser: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unfinished answer goes to the API; the next question tries the browser again."""
    monkeypatch.setattr(assistants, "REPEATS_PER_QUESTION", 3)
    stuck = FakeDocument(CHATGPT_START, after_submit=[CHATGPT_STREAMING])
    fake = fake_browser([stuck, _chatgpt_page(), _chatgpt_page()])
    openai = respx.post(assistants.OPENAI_URL).mock(return_value=_chat("Bedachungen Overmann"))
    gate, _ = _gate(Source.CHATGPT)

    async with fake.service as browser:
        answers = await assistants.ask_all(QUESTIONS, BUSINESS, paid_keys, gate, browser)

    assert [answer.route for answer in answers] == ["api", "browser", "browser"]
    assert answers[0].mentioned is False
    assert openai.call_count == 1


@respx.mock
async def test_ask_all_leaves_the_browser_after_repeated_failures(
    paid_keys: Settings, fake_browser: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After BROWSER_FAILURES_BEFORE_FALLBACK failures in a row the rest goes to the API."""
    monkeypatch.setattr(assistants, "REPEATS_PER_QUESTION", 4)
    stuck = FakeDocument(CHATGPT_START, after_submit=[CHATGPT_STREAMING])
    fake = fake_browser([stuck, stuck])
    openai = respx.post(assistants.OPENAI_URL).mock(return_value=_chat(BUSINESS))
    gate, counted = _gate(Source.CHATGPT)

    async with fake.service as browser:
        answers = await assistants.ask_all(QUESTIONS, BUSINESS, paid_keys, gate, browser)

    assert [answer.route for answer in answers] == ["api"] * 4
    assert len(fake.page.visited) == assistants.BROWSER_FAILURES_BEFORE_FALLBACK
    assert openai.call_count == 4
    assert counted.count("chatgpt") == 2 + 4


@pytest.mark.usefixtures("two_repeats")
async def test_ask_all_stops_a_provider_at_its_daily_cap(
    paid_keys: Settings, fake_browser: BrowserFactory
) -> None:
    """Questions beyond the cap are not asked; the capped source is reported by the gate."""
    fake = fake_browser([_chatgpt_page(), _chatgpt_page()])
    gate, _ = _gate(Source.CHATGPT, cap=1)

    async with fake.service as browser:
        answers = await assistants.ask_all(QUESTIONS, BUSINESS, paid_keys, gate, browser)

    assert len(answers) == 1
    assert len(fake.page.visited) == 1
    assert gate.exhausted == {Source.CHATGPT}


@pytest.mark.usefixtures("two_repeats")
async def test_ask_all_asks_claude_through_claude_code(
    paid_keys: Settings, fake_browser: BrowserFactory, fake_claude: Path
) -> None:
    """Claude answers through the CLI with web search, in an empty folder, with the model."""
    gate, counted = _gate(Source.CLAUDE)

    answers = await assistants.ask_all(
        QUESTIONS, BUSINESS, paid_keys, gate, fake_browser([]).service
    )

    assert [answer.route for answer in answers] == ["claude_code", "claude_code"]
    assert answers[0].mentioned is True
    assert answers[0].cited_sources == [
        "https://hollenbeck.de",
        "https://www.gelbeseiten.de/dachdecker/muenster",
    ]
    argv = json.loads(fake_claude.with_name("argv.json").read_text(encoding="utf-8"))
    assert argv[:2] == ["-p", QUESTIONS[0]]
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    assert argv[argv.index("--allowedTools") + 1 :] == ["WebSearch", "WebFetch"]
    working_dir = Path(fake_claude.with_name("cwd.txt").read_text(encoding="utf-8"))
    assert working_dir.name.startswith("radar-claude-")
    assert not working_dir.exists()
    assert counted == ["claude", "claude"]


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("not_logged_in", "Not logged in"),
        ("error", "529 Overloaded"),
        ("empty", "empty result"),
    ],
)
async def test_ask_all_reports_claude_code_failures(
    paid_keys: Settings,
    fake_browser: BrowserFactory,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    message: str,
) -> None:
    """A Claude Code that is not logged in or answers nothing gives no answers."""
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    monkeypatch.setattr(assistants, "REPEATS_PER_QUESTION", 1)

    with pytest.raises(CollectionError, match=message):
        await assistants.ask_all(
            QUESTIONS, BUSINESS, paid_keys, _gate(Source.CLAUDE)[0], fake_browser([]).service
        )


async def test_ask_all_skips_claude_without_the_cli(
    paid_keys: Settings, fake_browser: BrowserFactory
) -> None:
    """A missing Claude Code disables Claude for the run after the first question."""
    settings = paid_keys.model_copy(update={"analysis_claude_command": "claude-not-installed"})
    gate, counted = _gate(Source.CLAUDE)

    with pytest.raises(CollectionError, match="claude-not-installed"):
        await assistants.ask_all(QUESTIONS, BUSINESS, settings, gate, fake_browser([]).service)

    assert counted.count("claude") <= assistants.CONCURRENT_REQUESTS


async def test_ask_all_drops_a_claude_answer_after_the_timeout(
    paid_keys: Settings, fake_browser: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A question Claude Code does not answer in time is dropped."""
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "sleep")
    monkeypatch.setattr(assistants, "REPEATS_PER_QUESTION", 1)
    settings = paid_keys.model_copy(update={"assistant_timeout_seconds": 1})

    with pytest.raises(CollectionError, match="no result within 1 seconds"):
        await assistants.ask_all(
            QUESTIONS, BUSINESS, settings, _gate(Source.CLAUDE)[0], fake_browser([]).service
        )


@pytest.mark.usefixtures("two_repeats")
@respx.mock
async def test_ask_all_uses_configured_models(
    paid_keys: Settings, walled_browser: FakeBrowser
) -> None:
    """Model names of the paid fallback come from the settings."""
    settings = paid_keys.model_copy(
        update={"openai_model": "gpt-x", "gemini_model": "gemini-x", "perplexity_model": "high"}
    )
    openai = respx.post(assistants.OPENAI_URL).mock(return_value=_chat("a"))
    gemini = respx.post(assistants.gemini_url("gemini-x")).mock(return_value=_gemini("b"))
    perplexity = respx.post(assistants.PERPLEXITY_URL).mock(return_value=_perplexity("c"))
    gate, _ = _gate(Source.CHATGPT, Source.GEMINI, Source.PERPLEXITY)

    await assistants.ask_all(QUESTIONS, BUSINESS, settings, gate, walled_browser.service)

    assert json.loads(openai.calls[0].request.content)["model"] == "gpt-x"
    assert gemini.call_count == 2
    assert json.loads(perplexity.calls[0].request.content)["preset"] == "high"


@pytest.mark.usefixtures("two_repeats")
@respx.mock
async def test_ask_all_reads_every_paid_interface(
    paid_keys: Settings, walled_browser: FakeBrowser
) -> None:
    """Answers, citations and request shapes of the three paid interfaces."""
    openai = respx.post(assistants.OPENAI_URL).mock(
        return_value=_chat("Die **Dachdeckerei Hollenbeck** ist beliebt (https://hollenbeck.de).")
    )
    gemini = respx.post(GEMINI_URL).mock(return_value=_gemini("Bedachungen Overmann."))
    perplexity = respx.post(assistants.PERPLEXITY_URL).mock(
        return_value=_perplexity(
            f"{BUSINESS} [1]", ["https://www.gelbeseiten.de/x", "https://hollenbeck.de"]
        )
    )
    gate, _ = _gate(Source.CHATGPT, Source.GEMINI, Source.PERPLEXITY)

    answers = await assistants.ask_all(QUESTIONS, BUSINESS, paid_keys, gate, walled_browser.service)

    assert [answer.provider for answer in answers] == (
        ["chatgpt"] * 2 + ["gemini"] * 2 + ["perplexity"] * 2
    )
    assert {answer.route for answer in answers} == {"api"}
    by_provider = {answer.provider: answer for answer in answers}
    assert by_provider["chatgpt"].mentioned is True
    assert by_provider["chatgpt"].cited_sources == ["https://hollenbeck.de"]
    assert by_provider["gemini"].mentioned is False
    assert by_provider["gemini"].cited_sources == ["https://grounding.example/x"]
    assert by_provider["perplexity"].cited_sources == [
        "https://www.gelbeseiten.de/x",
        "https://hollenbeck.de",
    ]
    assert openai.call_count == gemini.call_count == perplexity.call_count == 2
    assert json.loads(gemini.calls[0].request.content)["tools"] == [{"google_search": {}}]
    openai_body = json.loads(openai.calls[0].request.content)
    assert openai_body["model"] == "gpt-4o-mini"
    assert openai_body["messages"] == [{"role": "user", "content": QUESTIONS[0]}]
    assert openai.calls[0].request.headers["Authorization"] == "Bearer openai-key"
    assert gemini.calls[0].request.headers["x-goog-api-key"] == "gemini-key"
    assert "key=" not in str(gemini.calls[0].request.url)
    assert json.loads(perplexity.calls[0].request.content) == {
        "preset": "fast",
        "input": QUESTIONS[0],
    }


@pytest.mark.usefixtures("two_repeats")
@respx.mock
async def test_ask_all_skips_a_provider_that_rejects_its_key(
    paid_keys: Settings, walled_browser: FakeBrowser
) -> None:
    """A rejected key ends that fallback; a temporary failure drops single answers."""
    openai = respx.post(assistants.OPENAI_URL).mock(return_value=httpx.Response(401))
    respx.post(GEMINI_URL).mock(return_value=_gemini("Hollenbeck"))
    respx.post(assistants.PERPLEXITY_URL).mock(return_value=httpx.Response(503))
    gate, _ = _gate(Source.CHATGPT, Source.GEMINI, Source.PERPLEXITY)

    answers = await assistants.ask_all(
        QUESTIONS, "Hollenbeck", paid_keys, gate, walled_browser.service
    )

    assert {answer.provider for answer in answers} == {"gemini"}
    assert openai.call_count == 1


@pytest.mark.usefixtures("two_repeats")
@respx.mock
async def test_ask_all_truncates_excerpt(paid_keys: Settings, walled_browser: FakeBrowser) -> None:
    """Long answers are cut to ANSWER_EXCERPT_CHARS; empty paid answers are dropped."""
    respx.post(assistants.OPENAI_URL).mock(return_value=_chat("x" * 2000))
    respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json={}))
    respx.post(assistants.PERPLEXITY_URL).mock(
        return_value=httpx.Response(200, json={"output": []})
    )
    gate, _ = _gate(Source.CHATGPT, Source.GEMINI, Source.PERPLEXITY)

    answers = await assistants.ask_all(
        QUESTIONS, "Hollenbeck", paid_keys, gate, walled_browser.service
    )

    assert {answer.provider for answer in answers} == {"chatgpt"}
    assert len(answers[0].answer_excerpt) == assistants.ANSWER_EXCERPT_CHARS


def test_provider_source_knows_current_and_legacy_names() -> None:
    """Answers of older audits named ChatGPT "openai"; unknown names have no source."""
    assert assistants.provider_source("chatgpt") is Source.CHATGPT
    assert assistants.provider_source("openai") is Source.CHATGPT
    assert assistants.provider_source("claude") is Source.CLAUDE
    assert assistants.provider_source("copilot") is None


@pytest.mark.parametrize(
    ("phrase", "city", "expected"),
    [
        ("Elektriker Münster", "Münster", "Elektriker"),
        ("münster elektriker", "Münster", "elektriker"),
        ("Heizung Notdienst Münster", "Münster", "Heizung Notdienst"),
        ("Friseur Bad Oeynhausen", "Bad Oeynhausen", "Friseur"),
        ("Münster", "Münster", None),
        ("", "Münster", None),
    ],
)
def test_trade_from_keyword_drops_the_city(phrase: str, city: str, expected: str | None) -> None:
    """The primary keyword without the city is the trade the assistants are asked about."""
    assert assistants.trade_from_keyword(phrase, city) == expected
