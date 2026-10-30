"""Tests for config.

Context: Unit tests with mocked external calls. | Input: Fixtures. | Output: Assertions.
"""

from pathlib import Path

import pytest

from radarauditor.config import Settings, load_settings
from radarauditor.errors import ConfigurationError


def test_load_settings_reads_environment(settings: Settings, environment: Path) -> None:
    """Values come from the environment."""
    assert str(settings.supabase_url) == "https://example.supabase.co/"
    assert settings.supabase_service_key.get_secret_value() == "service-key-for-tests"
    assert settings.radar_work_dir == environment
    assert settings.dataforseo_eur_per_usd == 1
    assert settings.event_endpoint is None


def test_settings_are_frozen(settings: Settings) -> None:
    """Settings cannot change after start-up."""
    with pytest.raises(ValueError, match="frozen"):
        settings.dataforseo_login = "login"


def test_missing_variables_are_named(environment: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the missing variables are listed."""
    monkeypatch.delenv("SUPABASE_URL")
    monkeypatch.delenv("GOOGLE_API_KEY")

    with pytest.raises(ConfigurationError) as caught:
        load_settings()

    message = str(caught.value)
    assert "missing environment variables" in message
    assert "SUPABASE_URL" in message
    assert "GOOGLE_API_KEY" in message
    assert "SUPABASE_SERVICE_KEY" not in message


def test_malformed_variables_are_named(environment: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed values are listed with their variable."""
    monkeypatch.setenv("SUPABASE_URL", "not a url")
    monkeypatch.setenv("DATAFORSEO_EUR_PER_USD", "maybe")

    with pytest.raises(ConfigurationError) as caught:
        load_settings()

    message = str(caught.value)
    assert message.startswith("malformed environment variables")
    assert "SUPABASE_URL" in message
    assert "DATAFORSEO_EUR_PER_USD" in message


def test_dotenv_file_is_read(environment: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A .env file is read; unknown keys are ignored."""
    monkeypatch.delenv("GOOGLE_API_KEY")
    Path(".env").write_text("GOOGLE_API_KEY=from-dotenv\nUNRELATED=1\n", encoding="utf-8")

    assert load_settings().google_api_key.get_secret_value() == "from-dotenv"


def test_questions_file_defaults_to_catalogue_folder(settings: Settings, environment: Path) -> None:
    """Without QUESTIONS_PATH the questions sit next to the catalogue."""
    assert settings.questions_path == settings.catalogue_path.parent / "fragen.yaml"


def test_packages_file_defaults_to_catalogue_folder(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without PACKAGES_PATH the packages sit next to the catalogue; the variable overrides it."""
    assert settings.packages_path == settings.catalogue_path.parent / "pakete.yaml"
    monkeypatch.setenv("PACKAGES_PATH", "eigene/pakete.yaml")

    assert load_settings().packages_path == Path("eigene/pakete.yaml")


def test_industries_file_defaults_to_catalogue_folder(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without INDUSTRIES_PATH the groups sit next to the catalogue; the variable overrides it."""
    monkeypatch.delenv("INDUSTRIES_PATH")
    assert load_settings().industries_path == settings.catalogue_path.parent / "branchen.yaml"
    monkeypatch.setenv("INDUSTRIES_PATH", "eigene/branchen.yaml")

    assert load_settings().industries_path == Path("eigene/branchen.yaml")


def test_questions_path_and_models_can_be_set(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Questions file and assistant models come from the environment when given."""
    monkeypatch.setenv("QUESTIONS_PATH", "eigene/fragen.yaml")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")

    settings = load_settings()

    assert settings.questions_path == Path("eigene/fragen.yaml")
    assert settings.gemini_model == "gemini-2.5-pro"
    assert settings.openai_model == "gpt-4o-mini"
    assert settings.perplexity_model == "fast"


def test_env_example_documents_every_setting() -> None:
    """Every settings field has a commented line in .env.example."""
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    lines = example.splitlines()
    for name in Settings.model_fields:
        variable = name.upper()
        index = next(i for i, line in enumerate(lines) if line.startswith(f"{variable}="))
        assert lines[index - 1].startswith("#"), variable


def test_empty_variables_count_as_unset(environment: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty lines copied from .env.example fall back to the defaults."""
    monkeypatch.setenv("EVENT_ENDPOINT", "")
    monkeypatch.setenv("QUESTIONS_PATH", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    settings = load_settings()

    assert settings.event_endpoint is None
    assert settings.questions_path == settings.catalogue_path.parent / "fragen.yaml"
    assert settings.openai_api_key is None


def test_analysis_settings_defaults_and_validation(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude Code runs as "claude" with a 900 second timeout; bad values stop the start."""
    monkeypatch.delenv("ANALYSIS_CLAUDE_COMMAND")
    defaults = load_settings()
    assert defaults.analysis_claude_command == "claude"
    assert defaults.analysis_timeout_seconds == 900
    assert defaults.analysis_permission_mode == "dontAsk"
    assert defaults.analysis_model is None

    monkeypatch.setenv("ANALYSIS_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("ANALYSIS_PERMISSION_MODE", "everything")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "ANALYSIS_TIMEOUT_SECONDS" in str(caught.value)
    assert "ANALYSIS_PERMISSION_MODE" in str(caught.value)


def test_assistant_timeout_default_and_validation(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One assistant answer may take 120 seconds by default; zero stops the start."""
    assert load_settings().assistant_timeout_seconds == 120
    monkeypatch.setenv("ASSISTANT_TIMEOUT_SECONDS", "0")

    with pytest.raises(ConfigurationError, match="ASSISTANT_TIMEOUT_SECONDS"):
        load_settings()


def test_mail_triage_defaults_and_validation(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five mails per idle poll, two minutes per answer, claimed for 15 minutes, Sonnet."""
    defaults = load_settings()
    assert defaults.mail_triage_batch == 5
    assert defaults.mail_triage_claim_seconds == 900
    assert defaults.mail_triage_timeout_seconds == 120
    assert defaults.mail_triage_model == "sonnet"

    monkeypatch.setenv("MAIL_TRIAGE_BATCH", "0")
    assert load_settings().mail_triage_batch == 0

    monkeypatch.setenv("MAIL_TRIAGE_BATCH", "-1")
    monkeypatch.setenv("MAIL_TRIAGE_TIMEOUT_SECONDS", "0")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "MAIL_TRIAGE_BATCH" in str(caught.value)
    assert "MAIL_TRIAGE_TIMEOUT_SECONDS" in str(caught.value)


@pytest.mark.parametrize(
    ("values", "variable"),
    [
        ({"MAIL_TRIAGE_BATCH": "51", "MAIL_TRIAGE_TIMEOUT_SECONDS": "60"}, "MAIL_TRIAGE_BATCH"),
        (
            {"MAIL_TRIAGE_BATCH": "0", "MAIL_TRIAGE_CLAIM_SECONDS": "29"},
            "MAIL_TRIAGE_CLAIM_SECONDS",
        ),
        ({"MAIL_TRIAGE_CLAIM_SECONDS": "3601"}, "MAIL_TRIAGE_CLAIM_SECONDS"),
    ],
)
def test_mail_triage_bounds(
    environment: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], variable: str
) -> None:
    """At most 50 mails at a time, a claim between 30 seconds and one hour."""
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError) as caught:
        load_settings()

    assert [part.split(" (")[0] for part in str(caught.value).split(": ", 1)[1].split(", ")] == [
        variable
    ]


def test_a_batch_may_take_at_most_one_hour(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch times answer time above an hour could never be covered by a claim."""
    monkeypatch.setenv("MAIL_TRIAGE_BATCH", "31")
    monkeypatch.setenv("MAIL_TRIAGE_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("MAIL_TRIAGE_CLAIM_SECONDS", "3600")

    with pytest.raises(ConfigurationError, match=r"MAIL_TRIAGE_TIMEOUT_SECONDS \(.*3600"):
        load_settings()


def test_a_claim_ends_after_the_answer_time(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claim shorter than one answer would let a second poll take the same mail."""
    monkeypatch.setenv("MAIL_TRIAGE_CLAIM_SECONDS", "60")

    with pytest.raises(ConfigurationError, match="MAIL_TRIAGE_CLAIM_SECONDS"):
        load_settings()


def test_alive_file_defaults_to_the_work_dir(settings: Settings, environment: Path) -> None:
    """The container health check reads the alive file in the work dir unless set otherwise."""
    assert settings.worker_alive_file == environment / "worker-alive"


def test_cockpit_assistant_defaults(environment: Path) -> None:
    """A request is looked for every 10 seconds, answered by Sonnet within two minutes."""
    defaults = load_settings()

    assert defaults.cockpit_assistant_poll_seconds == 10
    assert defaults.cockpit_assistant_timeout_seconds == 120
    assert defaults.cockpit_assistant_model == "sonnet"
    assert defaults.speech_url is None
    assert defaults.speech_timeout_seconds == 60


def test_cockpit_assistant_can_be_switched_off_and_pointed_at_the_speech_service(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COCKPIT_ASSISTANT_POLL_SECONDS", "0")
    monkeypatch.setenv("SPEECH_URL", "http://host.docker.internal:8090")

    settings = load_settings()

    assert settings.cockpit_assistant_poll_seconds == 0
    assert str(settings.speech_url) == "http://host.docker.internal:8090/"


@pytest.mark.parametrize(
    ("values", "variable"),
    [
        ({"COCKPIT_ASSISTANT_POLL_SECONDS": "-1"}, "COCKPIT_ASSISTANT_POLL_SECONDS"),
        ({"COCKPIT_ASSISTANT_TIMEOUT_SECONDS": "0"}, "COCKPIT_ASSISTANT_TIMEOUT_SECONDS"),
        ({"SPEECH_TIMEOUT_SECONDS": "0"}, "SPEECH_TIMEOUT_SECONDS"),
        ({"SPEECH_URL": "kein-link"}, "SPEECH_URL"),
    ],
)
def test_cockpit_assistant_bounds(
    environment: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], variable: str
) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError, match=variable):
        load_settings()


def test_one_request_is_finished_before_the_crm_hands_it_out_again(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CRM hands a request claimed for more than 10 minutes out again."""
    monkeypatch.setenv("SPEECH_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("COCKPIT_ASSISTANT_TIMEOUT_SECONDS", "300")

    with pytest.raises(ConfigurationError, match=r"COCKPIT_ASSISTANT_TIMEOUT_SECONDS \(.*600"):
        load_settings()
