"""Keys and JSON shapes of the settings rows shared by the app and the worker.

Context: The app writes `sources` into table `settings`; the worker reads it at the start of every
job and writes `worker_heartbeat` on every poll. The app mirrors this module.
Input: JSON values of table `settings`.
Output: Validated WorkerSettings and WorkerHeartbeat models.

Shapes (missing rows, sources and fields fall back to the defaults below):

    sources: {"<source>": {"enabled": true, "daily_cap": 200, "monthly_cost_cap_eur": 50}}
    worker_heartbeat: {"at": "2026-10-23T08:00:00+00:00", "version": "0.1.0",
                       "capabilities": {"chromium": true, "claude_cli": true,
                                        "claude_logged_in": true, "browser_profile": true,
                                        "analysis_available": false,
                                        "analysis_reason": "Claude Code ist nicht angemeldet",
                                        "mail_reading_available": false,
                                        "mail_reading_reason": "Claude Code ist nicht angemeldet"}}

daily_cap counts external requests per calendar day in Europe/Berlin (one search, one question to
an assistant, one API call). monthly_cost_cap_eur is optional and null means no cost cap; the paid
sources (places, dataforseo, prospect_search) book the price of every request against it and always
keep one: a missing or null cap there falls back to the default.
prospect_search is the customer search, which reads the Places API under its own caps.
analysis_available is true when Claude Code is logged in and answered a short question;
analysis_reason is the German reason when it is false, else null. While it is false the worker
claims no new collect jobs. mail_reading_available is true when mail reading is switched on, Claude
Code answers and the last run could take mails from the CRM; mail_reading_reason says why not.
"""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from radarauditor.errors import ConfigurationError

SOURCES_KEY = "sources"
WORKER_HEARTBEAT_KEY = "worker_heartbeat"
WORKER_SETTINGS_KEYS = (SOURCES_KEY,)


class Source(StrEnum):
    """External sources with a switch and a daily cap in the app settings."""

    PLACES = "places"
    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    GEMINI = "gemini"
    PERPLEXITY = "perplexity"
    GOOGLE_SEARCH = "google_search"
    GOOGLE_MAPS = "google_maps"
    DATAFORSEO = "dataforseo"
    PROSPECT_SEARCH = "prospect_search"


class SourceSetting(BaseModel):
    """Switch and limits of one source."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool
    daily_cap: int = Field(ge=0)
    monthly_cost_cap_eur: Decimal | None = Field(default=None, ge=0)


DEFAULT_SOURCES: dict[Source, SourceSetting] = {
    Source.PLACES: SourceSetting(enabled=True, daily_cap=500, monthly_cost_cap_eur=Decimal(25)),
    Source.CHATGPT: SourceSetting(enabled=True, daily_cap=200),
    Source.CLAUDE: SourceSetting(enabled=True, daily_cap=200),
    Source.GEMINI: SourceSetting(enabled=True, daily_cap=200),
    Source.PERPLEXITY: SourceSetting(enabled=True, daily_cap=200),
    Source.GOOGLE_SEARCH: SourceSetting(enabled=True, daily_cap=300),
    Source.GOOGLE_MAPS: SourceSetting(enabled=True, daily_cap=500),
    Source.DATAFORSEO: SourceSetting(
        enabled=True, daily_cap=1000, monthly_cost_cap_eur=Decimal(50)
    ),
    # The customer search has its own caps, so it never uses up what the audits need.
    Source.PROSPECT_SEARCH: SourceSetting(
        enabled=True, daily_cap=15, monthly_cost_cap_eur=Decimal(10)
    ),
}


def _without_lifted_cap(given: Mapping[str, object], default: SourceSetting) -> dict[str, object]:
    """The stored entry; a null euro cap is dropped where the default has one, so it stays."""
    if default.monthly_cost_cap_eur is not None and given.get("monthly_cost_cap_eur") is None:
        return {key: item for key, item in given.items() if key != "monthly_cost_cap_eur"}
    return dict(given)


class SourcesSettings(BaseModel):
    """Settings of every source; a partial entry keeps the defaults of the fields it leaves out."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    places: SourceSetting = DEFAULT_SOURCES[Source.PLACES]
    chatgpt: SourceSetting = DEFAULT_SOURCES[Source.CHATGPT]
    claude: SourceSetting = DEFAULT_SOURCES[Source.CLAUDE]
    gemini: SourceSetting = DEFAULT_SOURCES[Source.GEMINI]
    perplexity: SourceSetting = DEFAULT_SOURCES[Source.PERPLEXITY]
    google_search: SourceSetting = DEFAULT_SOURCES[Source.GOOGLE_SEARCH]
    google_maps: SourceSetting = DEFAULT_SOURCES[Source.GOOGLE_MAPS]
    dataforseo: SourceSetting = DEFAULT_SOURCES[Source.DATAFORSEO]
    prospect_search: SourceSetting = DEFAULT_SOURCES[Source.PROSPECT_SEARCH]

    @model_validator(mode="before")
    @classmethod
    def _merge_onto_defaults(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        merged: dict[str, object] = {}
        for source, default in DEFAULT_SOURCES.items():
            given = value.get(source.value)
            if isinstance(given, Mapping):
                stored = _without_lifted_cap(given, default)
                merged[source.value] = {**default.model_dump(), **stored}
            elif given is not None:
                merged[source.value] = given
        return merged

    def of(self, source: Source) -> SourceSetting:
        """Return the setting of one source."""
        setting: SourceSetting = getattr(self, source.value)
        return setting


class WorkerSettings(BaseModel):
    """What the app switched: source switches with caps."""

    model_config = ConfigDict(frozen=True)

    sources: SourcesSettings = SourcesSettings()


class WorkerCapabilities(BaseModel):
    """What the worker host can do right now."""

    chromium: bool
    claude_cli: bool
    claude_logged_in: bool
    browser_profile: bool
    analysis_available: bool
    analysis_reason: str | None = None
    mail_reading_available: bool = False
    mail_reading_reason: str | None = None


class WorkerHeartbeat(BaseModel):
    """Sign of life the worker writes on every poll."""

    at: datetime
    version: str
    capabilities: WorkerCapabilities


def parse_worker_settings(values: Mapping[str, object]) -> WorkerSettings:
    """Build WorkerSettings from settings rows by key; missing keys mean the defaults.

    Args:
        values: JSON values of table `settings` by key.

    Returns:
        Validated settings.

    Raises:
        ConfigurationError: If a row has the wrong shape.
    """
    present = {key: values[key] for key in WORKER_SETTINGS_KEYS if values.get(key) is not None}
    try:
        return WorkerSettings.model_validate(present)
    except ValidationError as exc:
        keys = ", ".join(present)
        raise ConfigurationError(f"settings rows {keys} are malformed: {exc}") from exc
