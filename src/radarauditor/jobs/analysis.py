"""Run the analysis step of an ANALYSE job inside the worker.

Context: Same steps as the manual hand-off: prepare the folder like `jobs pull`, let Claude Code run
/auswerten headless on it, validate and upload like `jobs push`. Claude Code is the only author of
the analysis: when it is missing, not logged in, too slow or writes invalid files, the step raises
AnalysisUnavailableError with the German reason, and the worker re-queues the job after the
backoff of retry_at while the audit stays awaiting_analysis.
Input: Claimed ANALYSE job, Settings.
Output: Findings, texts and analysis record in Supabase, report job queued.
"""

import logging
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from radarauditor.config import Settings
from radarauditor.db.models import Job
from radarauditor.db.repositories import Repositories
from radarauditor.errors import AnalysisUnavailableError, ClaudeCodeError, HandoffError
from radarauditor.jobs import handoff
from radarauditor.jobs.claude_code import failure_reason, run_claude_code

logger = logging.getLogger(__name__)

INVALID_FILES_REASON = "Claude Code hat ungültige Dateien geschrieben"
BACKOFF = (timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=2))
STEADY_INTERVAL = timedelta(hours=6)

ClaudeRunner = Callable[[Path, Settings], None]


def retry_at(attempts: int, now: datetime) -> datetime:
    """Return when an analyse job may run again after its attempts-th run without an answer.

    Args:
        attempts: Runs so far, counted by the claim.
        now: Current time.

    Returns:
        now plus 10 min, 30 min, 2 h, and 6 h for every later run.
    """
    step = attempts - 1
    if 0 <= step < len(BACKOFF):
        return now + BACKOFF[step]
    return now + STEADY_INTERVAL


def analyse_job(
    job: Job,
    repositories: Repositories,
    settings: Settings,
    runner: ClaudeRunner = run_claude_code,
) -> handoff.AnalysisRecord:
    """Prepare, analyse and deliver one ANALYSE job; the caller marks the job done.

    Args:
        job: Claimed ANALYSE job.
        repositories: Database and storage access.
        settings: Work directory, catalogue and Claude Code settings.
        runner: Runs Claude Code on a folder; replaced in tests.

    Returns:
        The record uploaded as auswertung.json.

    Raises:
        AnalysisUnavailableError: If Claude Code gave no valid files; the message is the reason.
        HandoffError: If the folder cannot be prepared.
        StorageError: If a download, upload or database write fails.
    """
    folder = settings.radar_work_dir / handoff.ANALYSE_DIR / str(job.id)
    catalogue_json = handoff.load_catalogue_json(settings.catalogue_path)
    record = handoff.AnalysisRecord(path=handoff.AnalysisPath.CLAUDE_CODE)
    try:
        handoff.materialise(job, folder, catalogue_json, repositories)
        write_analysis(folder, settings, runner)
        done_root = settings.radar_work_dir / handoff.DONE_DIR
        handoff.deliver(folder, repositories, done_root, record)
    except Exception:
        # A failed attempt is retried from scratch; a leftover folder would be pushed by hand.
        shutil.rmtree(folder, ignore_errors=True)
        raise
    logger.info("Analyse job %s delivered by Claude Code", job.id)
    return record


def write_analysis(folder: Path, settings: Settings, runner: ClaudeRunner) -> None:
    """Let Claude Code write findings.json and texte.json and check them like `jobs push`.

    Args:
        folder: Prepared hand-off folder.
        settings: Claude Code settings.
        runner: Runs Claude Code on a folder.

    Raises:
        AnalysisUnavailableError: If Claude Code failed or wrote invalid files.
    """
    for name in (handoff.FINDINGS_FILE, handoff.TEXTS_FILE):
        (folder / name).unlink(missing_ok=True)
    try:
        runner(folder, settings)
    except ClaudeCodeError as exc:
        logger.warning("Claude Code gave no analysis for %s: %s", folder.name, exc)
        raise AnalysisUnavailableError(failure_reason(exc.failure)) from exc
    try:
        handoff.validate_outputs(folder)
    except HandoffError as exc:
        logger.warning("Claude Code wrote invalid files for %s: %s", folder.name, exc)
        raise AnalysisUnavailableError(INVALID_FILES_REASON) from exc
