"""Run Claude Code headless: a project command for one hand-off folder, or one question.

Context: Called by the worker's analysis step (/auswerten, writes findings.json and texte.json),
optimisation step (/optimieren, writes aenderungen.json), the assistant collector (one question
with web search, assistant_command) and the mail reading (one question without any tool,
ask_without_tools). For a folder, Claude Code runs in the project directory with only the file
tools; it may read anything there and in the folder, and write only the named output files of that
folder. For a web question, only the web tools are available; a question without tools runs in an
empty temporary directory, so no project instructions are loaded. Every other action is denied
without a prompt (permission mode dontAsk). The heartbeat asks for one short answer without tools
(ping_claude_code) to learn whether Claude Code is answering at all. Everything runs on the Claude
subscription login of the host; the paid API is never used. Failures carry their kind and a German
reason for the app.
Input: Hand-off folder or question, Settings.
Output: The output files written by Claude Code or the answer text, or ClaudeCodeError.
"""

import json
import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from radarauditor.config import Settings
from radarauditor.errors import ClaudeCodeError, ClaudeCodeFailure

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = "/auswerten {folder}"
TOOLS = "Read,Glob,Grep,Write"
READ_TOOLS = ("Read", "Glob", "Grep")
OUTPUT_FILES = ("findings.json", "texte.json")
WEB_TOOLS = ("WebSearch", "WebFetch")
MESSAGE_LIMIT = 300
WINDOWS_DRIVE = re.compile(r"^([A-Za-z]):/(.*)$")
# Claude Code reports a missing or expired subscription login with one of these phrases.
LOGIN_PROBLEM = re.compile(
    r"not logged in|/login|invalid api key|authentication|oauth token", re.IGNORECASE
)
# A used-up subscription, in the wordings Claude Code has printed: "Claude AI usage limit
# reached|<time>", "You've hit your session limit · resets 3:50pm", "Usage credits are required for
# this model" (anthropics/claude-code issues 2087 and 86272).
USAGE_LIMIT = re.compile(
    r"usage limit|hit your (?:\w+ )?limit|usage credits are required", re.IGNORECASE
)
PING_PROMPT = "Antworte nur mit OK."
PING_MODEL = "haiku"
PING_TIMEOUT_SECONDS = 60
FAILURE_REASONS = {
    ClaudeCodeFailure.NOT_INSTALLED: "Claude Code ist nicht installiert",
    ClaudeCodeFailure.NOT_LOGGED_IN: "Claude Code ist nicht angemeldet",
    ClaudeCodeFailure.USAGE_LIMIT: "Das Kontingent des Claude-Abos ist aufgebraucht",
    ClaudeCodeFailure.TIMEOUT: "Claude Code hat nicht rechtzeitig geantwortet",
    ClaudeCodeFailure.FAILED: "Claude Code hat mit einem Fehler geantwortet",
}


def failure_reason(failure: ClaudeCodeFailure) -> str:
    """Return the German sentence the app shows for a kind of failure."""
    return FAILURE_REASONS[failure]


def absolute_rule_path(path: Path) -> str:
    """Write an absolute path the way Claude Code permission rules expect it.

    A rule path starting with a single slash is relative to the settings source; absolute paths
    need two. Windows drives are written as /c/... like Claude Code normalises them.

    Args:
        path: Absolute path.

    Returns:
        Path text starting with "//".
    """
    posix = path.as_posix()
    drive = WINDOWS_DRIVE.match(posix)
    if drive is not None:
        return f"//{drive.group(1).lower()}/{drive.group(2)}"
    return f"/{posix}"


def claude_command(
    executable: str,
    folder: Path,
    settings: Settings,
    *,
    prompt_template: str = PROMPT_TEMPLATE,
    output_files: Sequence[str] = OUTPUT_FILES,
) -> list[str]:
    """Build the argument list of the headless run.

    Args:
        executable: Resolved path of the Claude Code executable.
        folder: Absolute hand-off folder.
        settings: Permission mode and optional model.
        prompt_template: Prompt with a {folder} placeholder, by default /auswerten.
        output_files: Names of the only files Claude Code may write in the folder.

    Returns:
        Arguments for subprocess, starting with the executable.
    """
    writable = [f"Edit({absolute_rule_path(folder / name)})" for name in output_files]
    allowed = [*READ_TOOLS, *writable]
    command = [
        executable,
        "-p",
        prompt_template.format(folder=folder.as_posix()),
        "--output-format",
        "json",
        "--permission-mode",
        settings.analysis_permission_mode,
        "--no-session-persistence",
        "--tools",
        TOOLS,
    ]
    if settings.analysis_model:
        command += ["--model", settings.analysis_model]
    return [*command, "--allowedTools", *allowed, "--add-dir", str(folder)]


def run_claude_code(
    folder: Path,
    settings: Settings,
    *,
    prompt_template: str = PROMPT_TEMPLATE,
    output_files: Sequence[str] = OUTPUT_FILES,
) -> None:
    """Run Claude Code headless on one folder and check that it reports success.

    Args:
        folder: Hand-off folder prepared like `jobs pull` does.
        settings: Command, timeout, project directory, permission mode and model.
        prompt_template: Prompt with a {folder} placeholder, by default /auswerten.
        output_files: Names of the only files Claude Code may write in the folder.

    Raises:
        ClaudeCodeError: If the executable is missing, the run times out, exits with an error,
            or reports an error such as a missing login.
    """
    executable = resolve_executable(settings)
    command = claude_command(
        executable,
        folder.resolve(),
        settings,
        prompt_template=prompt_template,
        output_files=output_files,
    )
    timeout = settings.analysis_timeout_seconds
    logger.info("Running Claude Code on %s (timeout %d s)", folder.name, timeout)
    result = run_headless(command, settings.analysis_project_dir, timeout)
    logger.info("Claude Code finished: %s", _short(result))


def toolless_command(executable: str, prompt: str, model: str) -> list[str]:
    """Build the argument list of a question answered from its text alone.

    No tool is offered and every action is denied without a prompt, so the answer can only be
    text, whatever the prompt contains. No settings file of the host is read (no user settings,
    plugins, hooks or allow rules), no MCP server is started and no skill or command is offered;
    flags as listed by `claude --help` of Claude Code 2.1.310. --bare is not used, because it
    ignores the subscription login.

    Args:
        executable: Resolved path of the Claude Code executable.
        prompt: The whole question.
        model: Model name or alias.

    Returns:
        Arguments for subprocess, starting with the executable.
    """
    return [
        executable,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--tools",
        "",
        "--model",
        model,
    ]


def ask_without_tools(prompt: str, settings: Settings, model: str, timeout: int) -> str:
    """Ask Claude Code one question without any tool, outside the project directory.

    Args:
        prompt: The whole question.
        settings: The Claude Code command.
        model: Model name or alias.
        timeout: Seconds before the run is stopped.

    Returns:
        The answer text.

    Raises:
        ClaudeCodeError: If Claude Code is missing, not logged in, too slow or answers an error.
    """
    command = toolless_command(resolve_executable(settings), prompt, model)
    with tempfile.TemporaryDirectory() as scratch:
        return run_headless(command, Path(scratch), timeout)


def ping_claude_code(settings: Settings) -> None:
    """Ask Claude Code for one short answer to learn whether it is logged in and answering.

    Args:
        settings: The Claude Code command.

    Raises:
        ClaudeCodeError: If Claude Code is missing, not logged in, too slow or answers an error.
    """
    ask_without_tools(PING_PROMPT, settings, PING_MODEL, PING_TIMEOUT_SECONDS)


def resolve_executable(settings: Settings) -> str:
    """Find the Claude Code executable named by ANALYSIS_CLAUDE_COMMAND.

    Raises:
        ClaudeCodeError: NOT_INSTALLED if it is neither on PATH nor an executable path.
    """
    executable = shutil.which(settings.analysis_claude_command)
    if executable is None:
        raise ClaudeCodeError(
            f"executable {settings.analysis_claude_command!r} not found",
            ClaudeCodeFailure.NOT_INSTALLED,
        )
    return executable


def assistant_command(executable: str, question: str, model: str | None) -> list[str]:
    """Build the argument list that asks Claude one question with web search, like claude.ai.

    Only the web tools are available and allowed; every other action is denied without a prompt.

    Args:
        executable: Resolved path of the Claude Code executable.
        question: Question in German.
        model: Model name; None or empty uses the default model of the login.

    Returns:
        Arguments for subprocess, starting with the executable.
    """
    command = [
        executable,
        "-p",
        question,
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--tools",
        ",".join(WEB_TOOLS),
    ]
    if model:
        command += ["--model", model]
    return [*command, "--allowedTools", *WEB_TOOLS]


def run_headless(command: Sequence[str], cwd: Path, timeout: int) -> str:
    """Run a prepared headless Claude Code command and return its result text.

    Args:
        command: Arguments from claude_command, toolless_command or assistant_command.
        cwd: Directory Claude Code runs in.
        timeout: Seconds before the run is stopped.

    Returns:
        The "result" text of the JSON output.

    Raises:
        ClaudeCodeError: If the run cannot start, times out (TIMEOUT), exits with an error or
            reports one.
    """
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCodeError(
            f"no result within {timeout} seconds", ClaudeCodeFailure.TIMEOUT
        ) from exc
    except OSError as exc:
        raise ClaudeCodeError(f"cannot start {command[0]}: {exc}") from exc
    return check_result(completed.returncode, completed.stdout, completed.stderr)


def check_result(returncode: int, stdout: str, stderr: str) -> str:
    """Interpret the single JSON result Claude Code prints with --output-format json.

    Args:
        returncode: Exit code of the process.
        stdout: Standard output, expected to be one JSON object.
        stderr: Standard error, used in the message when stdout has no result.

    Returns:
        The "result" text, empty when the run reported none.

    Raises:
        ClaudeCodeError: If the exit code is not zero or the result is missing or an error;
            NOT_LOGGED_IN when the message names a missing login.
    """
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        result = None
    if not isinstance(result, dict):
        detail = _short(stderr or stdout) or "no output"
        raise ClaudeCodeError(
            f"exit code {returncode}, no JSON result: {detail}", _failure_of(detail)
        )
    if returncode != 0 or result.get("is_error") is True or result.get("subtype") != "success":
        detail = _short(str(result.get("result") or result.get("subtype") or stderr))
        raise ClaudeCodeError(f"exit code {returncode}: {detail}", _failure_of(detail))
    return str(result.get("result") or "")


def _failure_of(detail: str) -> ClaudeCodeFailure:
    if USAGE_LIMIT.search(detail):
        return ClaudeCodeFailure.USAGE_LIMIT
    if LOGIN_PROBLEM.search(detail):
        return ClaudeCodeFailure.NOT_LOGGED_IN
    return ClaudeCodeFailure.FAILED


def _short(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= MESSAGE_LIMIT else f"{flat[:MESSAGE_LIMIT]}..."
