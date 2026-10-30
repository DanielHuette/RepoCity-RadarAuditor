"""Tests for capability detection and the heartbeat row.

Context: Unit tests; PATH, the Claude Code CLI and the file system are faked. | Input: tmp_path.
| Output: Assertions.
"""

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from radarauditor.config import Settings
from radarauditor.db.app_settings import WorkerCapabilities
from radarauditor.errors import ClaudeCodeError, ClaudeCodeFailure
from radarauditor.jobs import claude_code, heartbeat

NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
CAPABILITIES = WorkerCapabilities(
    chromium=True,
    claude_cli=True,
    claude_logged_in=True,
    browser_profile=True,
    analysis_available=True,
)


def test_chromium_is_found_by_its_build_folder(tmp_path: Path) -> None:
    assert heartbeat.chromium_installed(tmp_path / "missing") is False
    (tmp_path / "ffmpeg-1011").mkdir()
    assert heartbeat.chromium_installed(tmp_path) is False
    (tmp_path / "chromium_headless_shell-1169").mkdir()
    assert heartbeat.chromium_installed(tmp_path) is True


@pytest.mark.parametrize(
    ("configured", "expected_tail"),
    [("/opt/browsers", ("opt", "browsers")), ("0", ("driver", "package", ".local-browsers"))],
)
def test_browsers_dir_follows_the_environment(
    monkeypatch: pytest.MonkeyPatch, configured: str, expected_tail: tuple[str, ...]
) -> None:
    monkeypatch.setenv(heartbeat.PLAYWRIGHT_BROWSERS_ENV, configured)

    assert heartbeat.playwright_browsers_dir().parts[-len(expected_tail) :] == expected_tail


def test_browsers_dir_defaults_to_the_linux_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(heartbeat.PLAYWRIGHT_BROWSERS_ENV, raising=False)
    monkeypatch.setattr(heartbeat.sys, "platform", "linux")

    assert heartbeat.playwright_browsers_dir() == Path.home() / ".cache" / "ms-playwright"


def test_claude_missing_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heartbeat.shutil, "which", lambda name: None)

    assert heartbeat.claude_login_state() == (False, False)


@pytest.mark.parametrize(
    ("output", "logged_in"),
    [
        ('{"loggedIn": true, "authMethod": "oauth_token"}', True),
        ('{"loggedIn": false}', False),
        ("not json", False),
        ("[]", False),
        (None, False),
    ],
)
def test_claude_login_is_read_from_auth_status(
    monkeypatch: pytest.MonkeyPatch, output: str | None, logged_in: bool
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(heartbeat.shutil, "which", lambda name: "/usr/bin/claude")

    def run(command: list[str]) -> str | None:
        commands.append(command)
        return output

    monkeypatch.setattr(heartbeat, "_run_command", run)

    assert heartbeat.claude_login_state("claude") == (True, logged_in)
    assert commands == [["/usr/bin/claude", "auth", "status"]]


def test_run_command_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(heartbeat.subprocess, "run", timeout)

    assert heartbeat._run_command(["claude", "auth", "status"]) is None


def test_run_command_returns_standard_output(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout='{"loggedIn":false}')
    monkeypatch.setattr(heartbeat.subprocess, "run", MagicMock(return_value=completed))

    assert heartbeat._run_command(["claude", "auth", "status"]) == '{"loggedIn":false}'


def test_browser_profile_must_hold_something(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    assert heartbeat.browser_profile_present(profile) is False
    profile.mkdir()
    assert heartbeat.browser_profile_present(profile) is False
    (profile / "Default").mkdir()
    assert heartbeat.browser_profile_present(profile) is True


def test_detect_capabilities_combines_every_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings: Settings
) -> None:
    commands: list[str] = []

    def login_state(command: str) -> tuple[bool, bool]:
        commands.append(command)
        return True, False

    monkeypatch.setattr(heartbeat, "claude_login_state", login_state)
    monkeypatch.setattr(heartbeat, "playwright_browsers_dir", lambda: tmp_path)
    (tmp_path / "chromium-1169").mkdir()
    ping = MagicMock()

    assert heartbeat.detect_capabilities(settings, ping) == WorkerCapabilities(
        chromium=True,
        claude_cli=True,
        claude_logged_in=False,
        browser_profile=False,
        analysis_available=False,
        analysis_reason="Claude Code ist nicht angemeldet",
        mail_reading_available=False,
        mail_reading_reason="Claude Code ist nicht angemeldet",
    )
    assert commands == [settings.analysis_claude_command]
    ping.assert_not_called()


def failing_ping(failure: ClaudeCodeFailure) -> MagicMock:
    """An answer check that fails with the given kind."""
    return MagicMock(side_effect=ClaudeCodeError("no answer", failure))


@pytest.mark.parametrize(
    ("claude_cli", "logged_in", "ping", "expected"),
    [
        (False, False, MagicMock(), (False, "Claude Code ist nicht installiert")),
        (True, False, MagicMock(), (False, "Claude Code ist nicht angemeldet")),
        (True, True, MagicMock(), (True, None)),
        (
            True,
            True,
            failing_ping(ClaudeCodeFailure.TIMEOUT),
            (False, "Claude Code hat nicht rechtzeitig geantwortet"),
        ),
        (
            True,
            True,
            failing_ping(ClaudeCodeFailure.NOT_LOGGED_IN),
            (False, "Claude Code ist nicht angemeldet"),
        ),
    ],
)
def test_analysis_state_needs_login_and_an_answer(
    claude_cli: bool, logged_in: bool, ping: MagicMock, expected: tuple[bool, str | None]
) -> None:
    assert heartbeat.analysis_state(claude_cli, logged_in, ping) == expected
    assert ping.called is (claude_cli and logged_in)


@pytest.mark.parametrize(
    ("mode", "logged_in", "available", "reason"),
    [
        ("ok", True, True, None),
        ("not_logged_in", False, False, "Claude Code ist nicht angemeldet"),
        ("error", True, False, "Claude Code hat mit einem Fehler geantwortet"),
        ("garbage", True, False, "Claude Code hat mit einem Fehler geantwortet"),
        ("sleep", True, False, "Claude Code hat nicht rechtzeitig geantwortet"),
    ],
)
def test_detect_capabilities_with_the_fake_cli(
    claude_settings: Settings,
    fake_claude: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    logged_in: bool,
    available: bool,
    reason: str | None,
) -> None:
    """`claude auth status` and the short answer check through the real subprocess calls."""
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    monkeypatch.setattr(claude_code, "PING_TIMEOUT_SECONDS", 1)

    capabilities = heartbeat.detect_capabilities(claude_settings)

    assert capabilities.claude_cli is True
    assert capabilities.claude_logged_in is logged_in
    assert capabilities.analysis_available is available
    assert capabilities.analysis_reason == reason
    if logged_in:
        argv = json.loads(fake_claude.with_name("argv.json").read_text(encoding="utf-8"))
        expected = claude_code.toolless_command(
            "x", claude_code.PING_PROMPT, claude_code.PING_MODEL
        )
        assert argv == expected[1:]


@pytest.mark.parametrize(
    ("enabled", "available", "reason", "expected"),
    [
        (True, True, None, (True, None)),
        (
            True,
            False,
            "Claude Code ist nicht angemeldet",
            (False, "Claude Code ist nicht angemeldet"),
        ),
        (False, True, None, (False, heartbeat.MAIL_READING_OFF)),
        (False, False, "Claude Code ist nicht installiert", (False, heartbeat.MAIL_READING_OFF)),
    ],
)
def test_mail_reading_needs_the_switch_and_claude_code(
    enabled: bool, available: bool, reason: str | None, expected: tuple[bool, str | None]
) -> None:
    assert heartbeat.mail_reading_state(enabled, available, reason) == expected


def test_mail_reading_follows_the_batch_setting(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(heartbeat, "claude_login_state", lambda command: (True, True))
    switched_off = settings.model_copy(update={"mail_triage_batch": 0})

    on = heartbeat.detect_capabilities(settings, MagicMock())
    off = heartbeat.detect_capabilities(switched_off, MagicMock())

    assert (on.mail_reading_available, on.mail_reading_reason) == (True, None)
    assert (off.mail_reading_available, off.mail_reading_reason) == (
        False,
        heartbeat.MAIL_READING_OFF,
    )


def test_a_problem_of_the_last_mail_run_marks_mail_reading_unavailable() -> None:
    working = WorkerCapabilities(
        chromium=True,
        claude_cli=True,
        claude_logged_in=True,
        browser_profile=True,
        analysis_available=True,
        mail_reading_available=True,
    )

    assert heartbeat.with_mail_problem(working, None) == working
    marked = heartbeat.with_mail_problem(working, "Die Mails im CRM lassen sich nicht abholen")
    assert (marked.mail_reading_available, marked.mail_reading_reason) == (
        False,
        "Die Mails im CRM lassen sich nicht abholen",
    )
    assert marked.analysis_available is True
    off = working.model_copy(
        update={"mail_reading_available": False, "mail_reading_reason": heartbeat.MAIL_READING_OFF}
    )
    assert heartbeat.with_mail_problem(off, "egal") == off


def test_probe_detects_again_only_after_the_refresh() -> None:
    detections: list[datetime] = []

    def detect() -> WorkerCapabilities:
        detections.append(NOW)
        return CAPABILITIES

    probe = heartbeat.CapabilityProbe(detect, refresh=timedelta(minutes=10))

    assert probe.current(NOW) == CAPABILITIES
    probe.current(NOW + timedelta(minutes=9))
    probe.current(NOW + timedelta(minutes=10))

    assert len(detections) == 2


def test_an_invalidated_probe_detects_at_the_next_beat() -> None:
    detections: list[int] = []

    def detect() -> WorkerCapabilities:
        detections.append(1)
        return CAPABILITIES

    probe = heartbeat.CapabilityProbe(detect, refresh=timedelta(minutes=10))
    assert probe.detected_at is None
    probe.current(NOW)
    assert probe.detected_at == NOW

    probe.invalidate()
    probe.current(NOW + timedelta(minutes=1))

    assert len(detections) == 2
    assert probe.detected_at == NOW + timedelta(minutes=1)


def test_the_switched_off_mail_reading_names_no_setting() -> None:
    assert heartbeat.MAIL_READING_OFF == "Mails lesen ist ausgeschaltet"


def test_write_heartbeat_stores_json_under_its_key() -> None:
    repositories = MagicMock()

    heartbeat.write_heartbeat(repositories, heartbeat.build_heartbeat(NOW, CAPABILITIES))

    repositories.set_setting.assert_called_once_with(
        "worker_heartbeat",
        {
            "at": "2026-10-23T08:00:00Z",
            "version": "0.1.0",
            "capabilities": CAPABILITIES.model_dump(),
        },
    )


def test_alive_file_is_created_with_its_folder(tmp_path: Path) -> None:
    alive_file = tmp_path / "work" / "worker-alive"

    heartbeat.touch_alive_file(alive_file)

    assert alive_file.is_file()


def test_alive_file_freshness_follows_its_modification_time(tmp_path: Path) -> None:
    alive_file = tmp_path / "worker-alive"
    assert heartbeat.alive_file_fresh(alive_file, NOW) is False

    heartbeat.touch_alive_file(alive_file)
    written = (NOW - timedelta(minutes=2)).timestamp()
    os.utime(alive_file, (written, written))
    assert heartbeat.alive_file_fresh(alive_file, NOW) is True

    stale = (NOW - heartbeat.ALIVE_MAX_AGE - timedelta(seconds=1)).timestamp()
    os.utime(alive_file, (stale, stale))
    assert heartbeat.alive_file_fresh(alive_file, NOW) is False
