"""Detect what the worker host can do and write the heartbeat row the app reads.

Context: Called on every poll of the worker loop. Detection starts processes (`claude auth status`
and, when logged in, one short question to Claude Code), so its result is reused for
CAPABILITY_REFRESH; the heartbeat itself is written each poll. analysis_available decides whether
the worker claims new collect jobs; mail_reading_available says whether mails are read and turns
false as soon as a mail run hits a problem. After each stored heartbeat the worker also touches a
local alive file, which the container health check reads without a database round trip.
Input: Settings (browser profile directory, Claude Code command), the host's Playwright browsers.
Output: settings row `worker_heartbeat`, the alive file.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import radarauditor
from radarauditor.config import Settings
from radarauditor.db.app_settings import (
    WORKER_HEARTBEAT_KEY,
    WorkerCapabilities,
    WorkerHeartbeat,
)
from radarauditor.db.repositories import Repositories
from radarauditor.errors import ClaudeCodeError, ClaudeCodeFailure
from radarauditor.jobs.claude_code import failure_reason, ping_claude_code

logger = logging.getLogger(__name__)

MAIL_READING_OFF = "Mails lesen ist ausgeschaltet"
CAPABILITY_REFRESH = timedelta(minutes=10)
ALIVE_MAX_AGE = timedelta(minutes=3)
CLAUDE_STATUS_TIMEOUT_SECONDS = 20
CHROMIUM_DIR_PREFIXES = ("chromium-", "chromium_headless_shell-")
PLAYWRIGHT_BROWSERS_ENV = "PLAYWRIGHT_BROWSERS_PATH"


def playwright_browsers_dir() -> Path:
    """Return where Playwright keeps its browsers on this host.

    Returns:
        PLAYWRIGHT_BROWSERS_PATH when set, else the platform default of Playwright.
    """
    configured = os.environ.get(PLAYWRIGHT_BROWSERS_ENV)
    if configured and configured != "0":
        return Path(configured)
    if configured == "0":
        import playwright

        return Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def chromium_installed(browsers_dir: Path) -> bool:
    """Return whether a Playwright Chromium build lies in the browsers directory."""
    if not browsers_dir.is_dir():
        return False
    return any(
        entry.is_dir() and entry.name.startswith(CHROMIUM_DIR_PREFIXES)
        for entry in browsers_dir.iterdir()
    )


def claude_login_state(command: str = "claude") -> tuple[bool, bool]:
    """Return whether the Claude Code CLI is on PATH and logged in.

    Args:
        command: Claude Code executable, a name on PATH or a full path.

    Returns:
        (CLI found, logged in) from `claude auth status`.
    """
    executable = shutil.which(command)
    if executable is None:
        return False, False
    return True, _reports_logged_in(_run_command([executable, "auth", "status"]))


def _reports_logged_in(output: str | None) -> bool:
    if not output:
        return False
    try:
        status = json.loads(output)
    except json.JSONDecodeError:
        return False
    return isinstance(status, dict) and status.get("loggedIn") is True


def _run_command(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=CLAUDE_STATUS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not run %s: %s", command[0], exc)
        return None
    return completed.stdout


def browser_profile_present(profile_dir: Path) -> bool:
    """Return whether the browser profile directory exists and holds anything."""
    return profile_dir.is_dir() and any(profile_dir.iterdir())


def analysis_state(
    claude_cli: bool, claude_logged_in: bool, ping: Callable[[], None]
) -> tuple[bool, str | None]:
    """Return whether Claude Code can analyse now and, when not, the German reason.

    The answer check runs only when the CLI is there and logged in.

    Args:
        claude_cli: Whether the CLI was found.
        claude_logged_in: Whether `claude auth status` reports a login.
        ping: Asks Claude Code one short question; raises ClaudeCodeError without an answer.

    Returns:
        (available, reason); reason is None when available.
    """
    if not claude_cli:
        return False, failure_reason(ClaudeCodeFailure.NOT_INSTALLED)
    if not claude_logged_in:
        return False, failure_reason(ClaudeCodeFailure.NOT_LOGGED_IN)
    try:
        ping()
    except ClaudeCodeError as exc:
        logger.warning("Claude Code did not answer the check: %s", exc)
        return False, failure_reason(exc.failure)
    return True, None


def mail_reading_state(
    enabled: bool, analysis_available: bool, analysis_reason: str | None
) -> tuple[bool, str | None]:
    """Return whether mails can be read now and, when not, the German reason.

    Args:
        enabled: Whether MAIL_TRIAGE_BATCH is above zero.
        analysis_available: Whether Claude Code answers.
        analysis_reason: Why Claude Code does not answer.

    Returns:
        (available, reason); reason is None when available.
    """
    if not enabled:
        return False, MAIL_READING_OFF
    if not analysis_available:
        return False, analysis_reason
    return True, None


def with_mail_problem(capabilities: WorkerCapabilities, problem: str | None) -> WorkerCapabilities:
    """Mark mail reading unavailable when the last mail run hit a problem.

    Args:
        capabilities: Detected capabilities.
        problem: German reason from the last mail run, or None when it went well.

    Returns:
        The capabilities, with mail reading switched to unavailable when there was a problem.
    """
    if problem is None or not capabilities.mail_reading_available:
        return capabilities
    return capabilities.model_copy(
        update={"mail_reading_available": False, "mail_reading_reason": problem}
    )


def detect_capabilities(
    settings: Settings, ping: Callable[[Settings], None] = ping_claude_code
) -> WorkerCapabilities:
    """Detect Chromium, Claude Code CLI, its login and answer, the browser profile, mail reading.

    Args:
        settings: Browser profile directory, Claude Code command and mail reading switch.
        ping: Answer check of Claude Code; replaced in tests.

    Returns:
        What this host can do right now.
    """
    claude_cli, claude_logged_in = claude_login_state(settings.analysis_claude_command)
    available, reason = analysis_state(claude_cli, claude_logged_in, lambda: ping(settings))
    mail_available, mail_reason = mail_reading_state(
        settings.mail_triage_batch > 0, available, reason
    )
    return WorkerCapabilities(
        chromium=chromium_installed(playwright_browsers_dir()),
        claude_cli=claude_cli,
        claude_logged_in=claude_logged_in,
        browser_profile=browser_profile_present(settings.browser_profile_dir),
        analysis_available=available,
        analysis_reason=reason,
        mail_reading_available=mail_available,
        mail_reading_reason=mail_reason,
    )


class CapabilityProbe:
    """Caches detected capabilities for CAPABILITY_REFRESH."""

    def __init__(
        self,
        detect: Callable[[], WorkerCapabilities],
        refresh: timedelta = CAPABILITY_REFRESH,
    ) -> None:
        """Remember how and how often to detect."""
        self._detect = detect
        self._refresh = refresh
        self._cached: tuple[datetime, WorkerCapabilities] | None = None

    @property
    def detected_at(self) -> datetime | None:
        """When the cached capabilities were detected, None before the first detection."""
        return None if self._cached is None else self._cached[0]

    def current(self, now: datetime) -> WorkerCapabilities:
        """Return the cached capabilities, detecting them again when older than the refresh."""
        if self._cached is None or now - self._cached[0] >= self._refresh:
            self._cached = (now, self._detect())
        return self._cached[1]

    def invalidate(self) -> None:
        """Detect again at the next call, for example after Claude Code lost its login."""
        self._cached = None


def build_heartbeat(now: datetime, capabilities: WorkerCapabilities) -> WorkerHeartbeat:
    """Assemble the heartbeat with the package version."""
    return WorkerHeartbeat(at=now, version=radarauditor.__version__, capabilities=capabilities)


def write_heartbeat(repositories: Repositories, heartbeat: WorkerHeartbeat) -> None:
    """Store the heartbeat under settings key `worker_heartbeat`.

    Raises:
        StorageError: If Supabase rejects the write.
    """
    repositories.set_setting(WORKER_HEARTBEAT_KEY, heartbeat.model_dump(mode="json"))


def touch_alive_file(path: Path) -> None:
    """Set the modification time of the alive file to now, creating it and its folder.

    Raises:
        OSError: If the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def alive_file_fresh(path: Path, now: datetime, max_age: timedelta = ALIVE_MAX_AGE) -> bool:
    """Return whether the alive file was touched within max_age before now.

    Args:
        path: Alive file written after each stored heartbeat.
        now: Time zone aware current time.
        max_age: Oldest modification time that still counts as alive.

    Returns:
        False when the file is missing, unreadable or older than max_age.
    """
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return False
    return now - modified <= max_age
