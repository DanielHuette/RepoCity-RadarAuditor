"""Load and validate runtime settings from the environment.

Context: Read once at start-up by the CLI and the worker; invalid settings stop the process.
Input: Environment variables or a .env file.
Output: A frozen Settings object.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, HttpUrl, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from radarauditor.errors import ConfigurationError
from radarauditor.industries import INDUSTRIES_FILE

DEFAULT_CATALOGUE_PATH = Path("config/pruefkatalog.yaml")
QUESTIONS_FILE = "fragen.yaml"
PACKAGES_FILE = "pakete.yaml"
ALIVE_FILE = "worker-alive"
DEFAULT_ANALYSIS_TIMEOUT_SECONDS = 900
DEFAULT_ASSISTANT_TIMEOUT_SECONDS = 120
DEFAULT_MAIL_TRIAGE_BATCH = 5
DEFAULT_MAIL_TRIAGE_TIMEOUT_SECONDS = 120
DEFAULT_MAIL_TRIAGE_CLAIM_SECONDS = 900
MAX_MAIL_TRIAGE_BATCH = 50
MIN_MAIL_TRIAGE_CLAIM_SECONDS = 30
MAX_MAIL_TRIAGE_CLAIM_SECONDS = 3600
DEFAULT_COCKPIT_ASSISTANT_POLL_SECONDS = 10
DEFAULT_COCKPIT_ASSISTANT_TIMEOUT_SECONDS = 120
DEFAULT_SPEECH_TIMEOUT_SECONDS = 60
SPEECH_ATTEMPTS = 3
# crm.claim_assistant_request hands a request claimed for longer than this out again.
ASSISTANT_CLAIM_SECONDS = 600

PermissionMode = Literal["dontAsk", "acceptEdits", "auto", "bypassPermissions", "manual", "plan"]


def _questions_next_to_catalogue(values: dict[str, Any]) -> Path:
    """Default questions file: fragen.yaml in the folder of the check catalogue."""
    catalogue = values.get("catalogue_path", DEFAULT_CATALOGUE_PATH)
    return Path(catalogue).parent / QUESTIONS_FILE


def _packages_next_to_catalogue(values: dict[str, Any]) -> Path:
    """Default package catalogue: pakete.yaml in the folder of the check catalogue."""
    catalogue = values.get("catalogue_path", DEFAULT_CATALOGUE_PATH)
    return Path(catalogue).parent / PACKAGES_FILE


def _industries_next_to_catalogue(values: dict[str, Any]) -> Path:
    """Default industry groups: branchen.yaml in the folder of the check catalogue."""
    catalogue = values.get("catalogue_path", DEFAULT_CATALOGUE_PATH)
    return Path(catalogue).parent / INDUSTRIES_FILE


def _alive_file_in_work_dir(values: dict[str, Any]) -> Path:
    """Default alive file: worker-alive in the work directory."""
    return Path(values.get("radar_work_dir", Path("work"))) / ALIVE_FILE


class Settings(BaseSettings):
    """Runtime settings; every field is documented in .env.example."""

    model_config = SettingsConfigDict(
        env_file=".env", frozen=True, extra="ignore", env_ignore_empty=True
    )

    supabase_url: HttpUrl
    supabase_service_key: SecretStr
    google_api_key: SecretStr
    radar_work_dir: Path = Path("work")
    worker_alive_file: Path = Field(default_factory=_alive_file_in_work_dir)
    browser_profile_dir: Path = Path("browser-profile")
    forecast_path: Path = Path("config/prognose.yaml")
    templates_dir: Path = Path("templates")
    catalogue_path: Path = DEFAULT_CATALOGUE_PATH
    questions_path: Path = Field(default_factory=_questions_next_to_catalogue)
    packages_path: Path = Field(default_factory=_packages_next_to_catalogue)
    industries_path: Path = Field(default_factory=_industries_next_to_catalogue)
    event_endpoint: HttpUrl | None = None
    dataforseo_login: str | None = None
    dataforseo_password: SecretStr | None = None
    # DataForSEO bills in US dollars; 1 counts every dollar as a euro, which errs on the safe side.
    dataforseo_eur_per_usd: Decimal = Field(default=Decimal(1), gt=0)
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    perplexity_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    gemini_model: str = "gemini-3.8-flash"
    # Preset of the Perplexity Agent API (fast, low, high), named model for compatibility.
    perplexity_model: str = "fast"
    claude_model: str = "claude-haiku-4-5"
    assistant_timeout_seconds: int = Field(default=DEFAULT_ASSISTANT_TIMEOUT_SECONDS, gt=0)
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    analysis_claude_command: str = "claude"
    analysis_timeout_seconds: int = Field(default=DEFAULT_ANALYSIS_TIMEOUT_SECONDS, gt=0)
    analysis_project_dir: Path = Path(".")
    analysis_permission_mode: PermissionMode = "dontAsk"
    analysis_model: str | None = None
    mail_triage_batch: int = Field(
        default=DEFAULT_MAIL_TRIAGE_BATCH, ge=0, le=MAX_MAIL_TRIAGE_BATCH
    )
    mail_triage_timeout_seconds: int = Field(default=DEFAULT_MAIL_TRIAGE_TIMEOUT_SECONDS, gt=0)
    mail_triage_claim_seconds: int = Field(
        default=DEFAULT_MAIL_TRIAGE_CLAIM_SECONDS,
        ge=MIN_MAIL_TRIAGE_CLAIM_SECONDS,
        le=MAX_MAIL_TRIAGE_CLAIM_SECONDS,
    )
    mail_triage_model: str = "sonnet"
    speech_url: HttpUrl | None = None
    speech_timeout_seconds: int = Field(default=DEFAULT_SPEECH_TIMEOUT_SECONDS, gt=0)
    cockpit_assistant_poll_seconds: int = Field(
        default=DEFAULT_COCKPIT_ASSISTANT_POLL_SECONDS, ge=0, le=3600
    )
    cockpit_assistant_timeout_seconds: int = Field(
        default=DEFAULT_COCKPIT_ASSISTANT_TIMEOUT_SECONDS, gt=0
    )
    cockpit_assistant_model: str = "sonnet"

    @field_validator("mail_triage_timeout_seconds")
    @classmethod
    def _batch_fits_the_longest_claim(cls, value: int, info: ValidationInfo) -> int:
        batch = info.data.get("mail_triage_batch", 0)
        if batch * value > MAX_MAIL_TRIAGE_CLAIM_SECONDS:
            raise ValueError(
                f"MAIL_TRIAGE_BATCH times MAIL_TRIAGE_TIMEOUT_SECONDS ({batch * value}) is above "
                f"the longest claim of {MAX_MAIL_TRIAGE_CLAIM_SECONDS} seconds"
            )
        return value

    @field_validator("mail_triage_claim_seconds")
    @classmethod
    def _claim_outlasts_the_batch(cls, value: int, info: ValidationInfo) -> int:
        # A claim that ends while the batch is still running lets the next poll take the same mail.
        batch = info.data.get("mail_triage_batch", 0)
        timeout = info.data.get("mail_triage_timeout_seconds", 0)
        if value < batch * timeout:
            raise ValueError(
                f"must be at least MAIL_TRIAGE_BATCH times MAIL_TRIAGE_TIMEOUT_SECONDS "
                f"({batch * timeout})"
            )
        return value

    @field_validator("cockpit_assistant_timeout_seconds")
    @classmethod
    def _request_ends_before_it_is_claimed_again(cls, value: int, info: ValidationInfo) -> int:
        # A request still running when the CRM hands it out again would be answered twice.
        speech = info.data.get("speech_timeout_seconds", 0) * SPEECH_ATTEMPTS
        if speech + value >= ASSISTANT_CLAIM_SECONDS:
            raise ValueError(
                f"{SPEECH_ATTEMPTS} times SPEECH_TIMEOUT_SECONDS plus "
                f"COCKPIT_ASSISTANT_TIMEOUT_SECONDS ({speech + value}) must stay below the "
                f"{ASSISTANT_CLAIM_SECONDS} seconds after which the CRM hands a request out again"
            )
        return value


def load_settings() -> Settings:
    """Build Settings and fail fast on missing or malformed values.

    Returns:
        Validated settings.

    Raises:
        ConfigurationError: If a required value is missing or malformed.
    """
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigurationError(describe_validation_error(exc)) from exc


def describe_validation_error(exc: ValidationError) -> str:
    """Turn a pydantic validation error into one readable line per environment variable.

    Args:
        exc: Error raised while building Settings.

    Returns:
        Message naming missing and malformed variables in upper case.
    """
    missing: list[str] = []
    malformed: list[str] = []
    for error in exc.errors():
        variable = str(error["loc"][0]).upper() if error["loc"] else "<settings>"
        if error["type"] == "missing":
            missing.append(variable)
        else:
            malformed.append(f"{variable} ({error['msg']})")
    parts: list[str] = []
    if missing:
        parts.append("missing environment variables: " + ", ".join(missing))
    if malformed:
        parts.append("malformed environment variables: " + ", ".join(malformed))
    return "; ".join(parts)
