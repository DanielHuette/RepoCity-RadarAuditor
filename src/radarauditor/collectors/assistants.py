"""Ask AI assistants local recommendation questions and record whether the business is named.

Context: ChatGPT, Gemini and Perplexity are asked on their websites in the shared server browser
first. When a website blocks the browser or asks for a login, the rest of that assistant's
questions go to its paid interface (OpenAI, Gemini API, Perplexity API) when its key is set; a
single timeout or browser failure sends only that question there. Claude is always asked through
the Claude Code CLI with web search. Every assistant runs only when its source is switched on in
the app settings, and every request, browser or paid, counts against the source's daily cap.
Every question is repeated to turn single answers into frequencies; each answer records its
route. The German question templates live in config/fragen.yaml.
Input: Question templates, questions, the shared browser.
Output: AssistantAnswer list.
"""

import asyncio
import logging
import string
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic.alias_generators import to_camel

from radarauditor.collectors import assistant_pages
from radarauditor.collectors.browser import BrowserService
from radarauditor.collectors.patterns import find_urls, normalise_name
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import AnswerRoute, AssistantAnswer
from radarauditor.collectors.transport import send
from radarauditor.config import Settings
from radarauditor.db.app_settings import Source
from radarauditor.errors import (
    ClaudeCodeError,
    ClaudeCodeFailure,
    CollectionError,
    ConfigurationError,
    LoginWallError,
    SearchBlockedError,
    TransientCollectionError,
)
from radarauditor.jobs import claude_code

logger = logging.getLogger(__name__)

PROVIDER_SOURCES: dict[str, Source] = {
    "chatgpt": Source.CHATGPT,
    "gemini": Source.GEMINI,
    "perplexity": Source.PERPLEXITY,
    "claude": Source.CLAUDE,
}
# Audits collected before the browser route named ChatGPT's answers after the OpenAI interface.
LEGACY_PROVIDER_SOURCES: dict[str, Source] = {"openai": Source.CHATGPT}
CLAUDE = "claude"
REPEATS_PER_QUESTION = 10
CONCURRENT_REQUESTS = 4
REQUEST_TIMEOUT_SECONDS = 60
ANSWER_EXCERPT_CHARS = 600
BROWSER_FAILURES_BEFORE_FALLBACK = 2
CLAUDE_CODE_UNAVAILABLE = frozenset(
    {ClaudeCodeFailure.NOT_INSTALLED, ClaudeCodeFailure.NOT_LOGGED_IN}
)
TEMPLATE_PLACEHOLDERS = frozenset({"industry", "city", "service"})

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
# Perplexity retired chat completions for Sonar; the Agent API takes a preset instead of a model.
PERPLEXITY_URL = "https://api.perplexity.ai/v1/agent"


class QuestionTemplates(BaseModel):
    """Question templates from fragen.yaml: recommendation questions and problem questions."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    recommendation: list[str] = Field(alias="empfehlung", min_length=1)
    problem: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class _Reply:
    text: str
    citations: list[str]


class _ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class _UrlCitation(_ApiModel):
    url: str


class _Annotation(_ApiModel):
    url_citation: _UrlCitation | None = None


class _ChatMessage(_ApiModel):
    content: str | None = None
    annotations: list[_Annotation] = []


class _ChatChoice(_ApiModel):
    message: _ChatMessage


class _SearchResult(_ApiModel):
    url: str


class _ChatResponse(_ApiModel):
    choices: list[_ChatChoice]
    citations: list[str] = []
    search_results: list[_SearchResult] = []


class _PerplexityText(_ApiModel):
    type: str
    text: str = ""


class _PerplexityItem(_ApiModel):
    type: str
    content: list[_PerplexityText] = []
    results: list[_SearchResult] = []


class _PerplexityResponse(_ApiModel):
    output: list[_PerplexityItem] = []


class _GeminiPart(_ApiModel):
    text: str = ""


class _GeminiContent(_ApiModel):
    parts: list[_GeminiPart] = []


class _GeminiWeb(_ApiModel):
    uri: str


class _GeminiChunk(_ApiModel):
    web: _GeminiWeb | None = None


class _GeminiGrounding(_ApiModel):
    grounding_chunks: list[_GeminiChunk] = []


class _GeminiCandidate(_ApiModel):
    content: _GeminiContent | None = None
    grounding_metadata: _GeminiGrounding | None = None


class _GeminiResponse(_ApiModel):
    candidates: list[_GeminiCandidate] = []


Asker = Callable[[httpx.AsyncClient, str, SecretStr, str], Awaitable[_Reply]]


def provider_source(provider: str) -> Source | None:
    """Return the app-settings source of a provider name found in an answer, old names included."""
    return PROVIDER_SOURCES.get(provider) or LEGACY_PROVIDER_SOURCES.get(provider)


def enabled_providers(settings: Settings, gate: SourceGate) -> list[str]:
    """Return the assistants whose source is switched on.

    The browser route and Claude Code need no key, so the switch alone decides; a missing paid
    key only removes the fallback.

    Args:
        settings: Runtime settings.
        gate: Source switches and caps of the job.

    Returns:
        A subset of "chatgpt", "gemini", "perplexity", "claude" in this order.
    """
    return [name for name, source in PROVIDER_SOURCES.items() if gate.is_enabled(source)]


async def ask_all(
    questions: list[str],
    business_name: str,
    settings: Settings,
    gate: SourceGate,
    browser: BrowserService,
) -> list[AssistantAnswer]:
    """Send every question REPEATS_PER_QUESTION times to every enabled assistant.

    Browser questions of all assistants share the one tab and take turns; paid and Claude Code
    calls run up to CONCURRENT_REQUESTS at a time. An assistant whose paid key is rejected, whose
    Claude Code is missing or not logged in, or whose browser route failed without a fallback is
    skipped for the rest of the run; a single answer that fails temporarily is dropped, and so is
    every call after the source's daily cap ran out. Callers detect a silent assistant by
    comparing `enabled_providers` with the providers present in the result.

    Args:
        questions: Questions in German.
        business_name: Name of the business looked for in the answers.
        settings: Runtime settings with paid keys, models, timeout and the Claude Code command.
        gate: Source switches and caps of the job; every request counts against its cap.
        browser: Shared browser of the job.

    Returns:
        One answer per successful call, grouped by provider and question.

    Raises:
        CollectionError: If assistants are enabled but not a single call succeeded.
        StorageError: If the cap counter cannot be updated.
    """
    providers = enabled_providers(settings, gate)
    if not providers or not questions:
        return []
    calls = [question for question in questions for _ in range(REPEATS_PER_QUESTION)]
    with tempfile.TemporaryDirectory(prefix="radar-claude-") as empty_dir:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            run = _Run(business_name, settings, gate, _Tools(browser, client, Path(empty_dir)))
            grouped = await asyncio.gather(
                *(run.provider_answers(provider, calls) for provider in providers)
            )
    answers = [answer for group in grouped for answer in group]
    if not answers:
        raise CollectionError(f"no assistant answered: {run.last_error}")
    return answers


@dataclass(frozen=True)
class _Tools:
    browser: BrowserService
    client: httpx.AsyncClient
    claude_dir: Path


class _Run:
    def __init__(
        self, business_name: str, settings: Settings, gate: SourceGate, tools: _Tools
    ) -> None:
        self.business_key = normalise_name(business_name)
        self.settings = settings
        self.gate = gate
        self.tools = tools
        self.paid_slots = asyncio.Semaphore(CONCURRENT_REQUESTS)
        self.claude_slots = asyncio.Semaphore(CONCURRENT_REQUESTS)
        self.rejected: set[str] = set()
        self.browser_used = False
        self.claude_executable: str | None = None
        self.last_error = "no call was made"

    async def provider_answers(self, provider: str, calls: list[str]) -> list[AssistantAnswer]:
        if provider == CLAUDE:
            return _present(await asyncio.gather(*(self._claude(question) for question in calls)))
        answers: list[AssistantAnswer] = []
        remaining = await self._browser_answers(provider, calls, answers)
        fallback = await asyncio.gather(*(self._paid(provider, question) for question in remaining))
        return answers + _present(fallback)

    async def _browser_answers(
        self, provider: str, calls: list[str], answers: list[AssistantAnswer]
    ) -> list[str]:
        """Ask in the browser until done or blocked; return the questions left for the API."""
        site = assistant_pages.SITES[provider]
        failures = 0
        for index, question in enumerate(calls):
            if not self._acquire(provider):
                return []
            try:
                answers.append(await self._ask_page(site, question))
                failures = 0
            except (LoginWallError, SearchBlockedError) as error:
                self._note(provider, "browser blocked for the rest of the run", error)
                return calls[index:]
            except (TransientCollectionError, PlaywrightError) as error:
                failures += 1
                self._note(provider, "browser gave no answer", error)
                if failures >= BROWSER_FAILURES_BEFORE_FALLBACK:
                    return calls[index:]
                fallback = await self._paid(provider, question)
                if fallback is not None:
                    answers.append(fallback)
        return []

    async def _ask_page(
        self, site: assistant_pages.AssistantSite, question: str
    ) -> AssistantAnswer:
        browser = self.tools.browser
        if self.browser_used:
            await browser.pause()
        self.browser_used = True
        page = await assistant_pages.ask_on_site(
            browser, site, question, self.settings.assistant_timeout_seconds
        )
        return self._answer(site.provider, question, _Reply(page.text, page.sources), "browser")

    async def _paid(self, provider: str, question: str) -> AssistantAnswer | None:
        key = _provider_key(self.settings, provider)
        async with self.paid_slots:
            if key is None or provider in self.rejected:
                return None
            if not self._acquire(provider):
                return None
            try:
                reply = await _ASKERS[provider](
                    self.tools.client, question, key, _provider_model(self.settings, provider)
                )
            except TransientCollectionError as error:
                self._note(provider, "paid interface gave no answer", error)
                return None
            except CollectionError as error:
                self._reject(provider, error)
                return None
        return self._answer(provider, question, reply, "api")

    async def _claude(self, question: str) -> AssistantAnswer | None:
        async with self.claude_slots:
            if CLAUDE in self.rejected or not self._acquire(CLAUDE):
                return None
            try:
                text = await asyncio.to_thread(self._run_claude_code, question)
            except ClaudeCodeError as error:
                if error.failure in CLAUDE_CODE_UNAVAILABLE:
                    self._reject(CLAUDE, error)
                else:
                    self._note(CLAUDE, "Claude Code gave no answer", error)
                return None
        if not text.strip():
            self._note(CLAUDE, "Claude Code gave no answer", "empty result")
            return None
        return self._answer(CLAUDE, question, _Reply(text, []), "claude_code")

    def _run_claude_code(self, question: str) -> str:
        if self.claude_executable is None:
            self.claude_executable = claude_code.resolve_executable(self.settings)
        command = claude_code.assistant_command(
            self.claude_executable, question, self.settings.claude_model
        )
        return claude_code.run_headless(
            command, self.tools.claude_dir, self.settings.assistant_timeout_seconds
        )

    def _acquire(self, provider: str) -> bool:
        if self.gate.try_acquire(PROVIDER_SOURCES[provider]):
            return True
        self.last_error = f"{provider}: daily cap reached"
        return False

    def _note(self, provider: str, what: str, error: object) -> None:
        logger.warning("%s: %s: %s", provider, what, error)
        self.last_error = f"{provider}: {error}"

    def _reject(self, provider: str, error: Exception) -> None:
        logger.warning("%s disabled for this run: %s", provider, error)
        self.rejected.add(provider)
        self.last_error = f"{provider}: {error}"

    def _answer(
        self, provider: str, question: str, reply: _Reply, route: AnswerRoute
    ) -> AssistantAnswer:
        return AssistantAnswer(
            provider=provider,
            question=question,
            mentioned=bool(self.business_key) and self.business_key in normalise_name(reply.text),
            cited_sources=_merge_sources(find_urls(reply.text), reply.citations),
            answer_excerpt=reply.text[:ANSWER_EXCERPT_CHARS],
            route=route,
        )


def _present(answers: list[AssistantAnswer | None]) -> list[AssistantAnswer]:
    return [answer for answer in answers if answer is not None]


def load_question_templates(path: Path) -> QuestionTemplates:
    """Read and validate the German question templates.

    Args:
        path: Location of fragen.yaml with the lists "empfehlung" and "problem".

    Returns:
        The validated templates.

    Raises:
        ConfigurationError: If the file is unreadable, malformed or uses an unknown placeholder.
    """
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        templates = QuestionTemplates.model_validate(document)
    except OSError as error:
        raise ConfigurationError(f"Cannot read question templates {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigurationError(
            f"Question templates {path} are not valid YAML: {error}"
        ) from error
    except ValidationError as error:
        raise ConfigurationError(f"Question templates {path} are malformed: {error}") from error
    for template in [*templates.recommendation, *templates.problem]:
        _check_placeholders(template, path)
    return templates


def _check_placeholders(template: str, path: Path) -> None:
    try:
        names = {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
    except ValueError as error:
        raise ConfigurationError(f"{path}: template {template!r} is malformed") from error
    unknown = names - TEMPLATE_PLACEHOLDERS
    if unknown:
        raise ConfigurationError(
            f"{path}: template {template!r} uses unknown placeholders {sorted(unknown)}"
        )


def trade_from_keyword(phrase: str, city: str) -> str | None:
    """Trade as customers ask for it, taken from the primary keyword without the city.

    "Elektriker Münster" becomes "Elektriker", so the assistants are asked "Wer ist ein guter
    Elektriker in Münster?" instead of a group term no customer would type.

    Args:
        phrase: Primary keyword of the customer.
        city: City of the location.

    Returns:
        The keyword without the city words, or None when nothing is left.
    """
    city_words = {word.casefold() for word in city.split()}
    words = [word for word in phrase.split() if word.casefold() not in city_words]
    trade = " ".join(words).strip(" ,")
    return trade or None


def build_questions(
    industry: str, city: str, services: list[str], templates: QuestionTemplates
) -> list[str]:
    """Fill the German question templates with industry, city and services.

    Args:
        industry: Trade noun in German, for example "Haustechnikbetrieb" (question_term of the
            customer's industry group).
        city: City of the location.
        services: Services offered; problem questions take them in turn, one per template.
            Without services the industry is used instead.
        templates: Templates from fragen.yaml.

    Returns:
        One recommendation question per template followed by one problem question per template.
    """
    topics = [service.strip() for service in services if service.strip()]
    topics = topics[: len(templates.problem)] or [industry]
    recommendations = [
        template.format(industry=industry, city=city, service=topics[0])
        for template in templates.recommendation
    ]
    problems = [
        template.format(industry=industry, city=city, service=topics[index % len(topics)])
        for index, template in enumerate(templates.problem)
    ]
    return recommendations + problems


def gemini_url(model: str) -> str:
    """Address of the generateContent endpoint for a Gemini model."""
    return GEMINI_URL_TEMPLATE.format(model=model)


async def _ask_openai(
    client: httpx.AsyncClient, question: str, key: SecretStr, model: str
) -> _Reply:
    payload = await _post_json(
        client,
        OPENAI_URL,
        "OpenAI",
        headers={"Authorization": f"Bearer {key.get_secret_value()}"},
        json={"model": model, "messages": [{"role": "user", "content": question}]},
    )
    response = _validate(_ChatResponse, payload, "OpenAI")
    message = _first_message(response, "OpenAI")
    citations = [item.url_citation.url for item in message.annotations if item.url_citation]
    return _Reply(message.content or "", citations)


async def _ask_perplexity(
    client: httpx.AsyncClient, question: str, key: SecretStr, model: str
) -> _Reply:
    payload = await _post_json(
        client,
        PERPLEXITY_URL,
        "Perplexity",
        headers={"Authorization": f"Bearer {key.get_secret_value()}"},
        json={"preset": model, "input": question},
    )
    response = _validate(_PerplexityResponse, payload, "Perplexity")
    text = "".join(
        part.text
        for item in response.output
        if item.type == "message"
        for part in item.content
        if part.type == "output_text"
    )
    if not text:
        raise TransientCollectionError("Perplexity returned no text")
    sources = [
        result.url
        for item in response.output
        if item.type == "search_results"
        for result in item.results
    ]
    return _Reply(text, sources)


async def _ask_gemini(
    client: httpx.AsyncClient, question: str, key: SecretStr, model: str
) -> _Reply:
    # The key travels in a header instead of the "key" query parameter so it never appears in
    # logged request URLs.
    payload = await _post_json(
        client,
        gemini_url(model),
        "Gemini",
        headers={"x-goog-api-key": key.get_secret_value()},
        json={
            "contents": [{"role": "user", "parts": [{"text": question}]}],
            # Grounding in Google search, as the Gemini app answers; it also returns the sources.
            "tools": [{"google_search": {}}],
        },
    )
    response = _validate(_GeminiResponse, payload, "Gemini")
    if not response.candidates:
        raise TransientCollectionError("Gemini returned no candidate")
    candidate = response.candidates[0]
    text = "".join(part.text for part in candidate.content.parts) if candidate.content else ""
    chunks = candidate.grounding_metadata.grounding_chunks if candidate.grounding_metadata else []
    return _Reply(text, [chunk.web.uri for chunk in chunks if chunk.web])


_ASKERS: dict[str, Asker] = {
    "chatgpt": _ask_openai,
    "gemini": _ask_gemini,
    "perplexity": _ask_perplexity,
}


def _provider_key(settings: Settings, provider: str) -> SecretStr | None:
    keys = {
        "chatgpt": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
        "perplexity": settings.perplexity_api_key,
    }
    key = keys.get(provider)
    return key if key is not None and key.get_secret_value().strip() else None


def _provider_model(settings: Settings, provider: str) -> str:
    models = {
        "chatgpt": settings.openai_model,
        "gemini": settings.gemini_model,
        "perplexity": settings.perplexity_model,
    }
    return models[provider]


async def _post_json(
    client: httpx.AsyncClient, url: str, source: str, **request_arguments: Any
) -> object:
    response = await send(client, "POST", url, source=source, **request_arguments)
    try:
        payload: object = response.json()
    except ValueError as error:
        raise CollectionError(f"{source} answer is not JSON") from error
    return payload


def _validate[ModelT: BaseModel](model: type[ModelT], payload: object, source: str) -> ModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise CollectionError(f"{source} answer is malformed") from error


def _first_message(response: _ChatResponse, source: str) -> _ChatMessage:
    if not response.choices:
        raise TransientCollectionError(f"{source} returned no choice")
    return response.choices[0].message


def _merge_sources(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for url in group:
            if url not in merged:
                merged.append(url)
    return merged
