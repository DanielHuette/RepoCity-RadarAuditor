"""Tests for the worker's analysis step.

Context: Fake repositories and a fake Claude Code; the real CLI never runs. | Input: Fixtures. |
Output: Assertions.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.config import Settings
from radarauditor.db.models import Audit, Job, JobKind
from radarauditor.errors import (
    AnalysisUnavailableError,
    ClaudeCodeError,
    ClaudeCodeFailure,
    StorageError,
)
from radarauditor.jobs.analysis import analyse_job, retry_at
from radarauditor.jobs.handoff import AnalysisPath

JobFactory = Callable[..., Job]
VALID_TEXTS = {
    "introduction": "Sie werden gefunden.",
    "summary": "Gut. Lückenhaft. Teuer.",
    "forecast_note": "Eine Schätzung.",
    "subscription_reason": "Ihre Wettbewerber holen auf.",
    "offer_text": "Erstoptimierung.",
    "talk_text": "Sie stehen auf der Karte hinter drei Nachbarn.",
}


@pytest.fixture
def repositories(fake_repositories: MagicMock, sample_raw_audit: RawAudit) -> MagicMock:
    """Fake repositories whose download writes the sample RawAudit."""

    def write(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(sample_raw_audit.model_dump_json(), encoding="utf-8")
        return local_file

    fake_repositories.download.side_effect = write
    return fake_repositories


def writing_runner(texts: object) -> Callable[[Path, Settings], None]:
    """A runner that writes one finding for a1 and the given texts, like Claude Code."""

    def run(folder: Path, settings: Settings) -> None:
        audit_id = json.loads((folder / "audit.json").read_text(encoding="utf-8"))["audit"]["id"]
        finding = {
            "id": "00000000-0000-4000-8000-0000000000f1",
            "audit_id": audit_id,
            "check_id": "a1",
            "severity": 3,
            "title": "Ohne HTTPS.",
            "explanation": "Kein Zertifikat.",
            "customer_impact": "Warnung.",
            "fix": "Zertifikat.",
            "fix_payload": None,
        }
        (folder / "findings.json").write_text(json.dumps([finding]), encoding="utf-8")
        (folder / "texte.json").write_text(json.dumps(texts), encoding="utf-8")

    return run


def failing_runner(failure: ClaudeCodeFailure) -> Callable[[Path, Settings], None]:
    """A runner that fails like Claude Code does with the given kind of failure."""

    def run(folder: Path, settings: Settings) -> None:
        raise ClaudeCodeError("exit code 1: something", failure)

    return run


def uploaded_record(repositories: MagicMock, settings: Settings, job: Job) -> dict[str, object]:
    """The auswertung.json that was uploaded, read from the done folder."""
    remote_paths = [c.args[1] for c in repositories.upload.call_args_list]
    assert remote_paths == [f"{job.audit_id}/texte.json", f"{job.audit_id}/auswertung.json"]
    done = settings.radar_work_dir / "erledigt" / str(job.id) / "auswertung.json"
    content: dict[str, object] = json.loads(done.read_text(encoding="utf-8"))
    return content


def test_claude_code_path_delivers_its_files(
    repositories: MagicMock, settings: Settings, make_job: JobFactory
) -> None:
    """Valid output from Claude Code is pushed unchanged and recorded as claude_code."""
    job = make_job(JobKind.ANALYSE)

    record = analyse_job(job, repositories, settings, writing_runner(VALID_TEXTS))

    assert record.path is AnalysisPath.CLAUDE_CODE
    saved = repositories.save_findings.call_args.args[0]
    assert [finding.title for finding in saved] == ["Ohne HTTPS."]
    assert uploaded_record(repositories, settings, job) == {"path": "claude_code"}
    repositories.enqueue.assert_called_once_with(JobKind.REPORT, job.customer_id, job.audit_id)
    repositories.finish_job.assert_not_called()


@pytest.mark.parametrize(
    ("runner", "reason"),
    [
        (failing_runner(ClaudeCodeFailure.NOT_INSTALLED), "Claude Code ist nicht installiert"),
        (failing_runner(ClaudeCodeFailure.NOT_LOGGED_IN), "Claude Code ist nicht angemeldet"),
        (
            failing_runner(ClaudeCodeFailure.TIMEOUT),
            "Claude Code hat nicht rechtzeitig geantwortet",
        ),
        (failing_runner(ClaudeCodeFailure.FAILED), "Claude Code hat mit einem Fehler geantwortet"),
        (writing_runner({"introduction": ""}), "Claude Code hat ungültige Dateien geschrieben"),
    ],
)
def test_no_substitute_when_claude_code_fails_or_writes_invalid_files(
    repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    runner: Callable[[Path, Settings], None],
    reason: str,
) -> None:
    """Nothing is delivered; the German reason is raised and the folder removed."""
    job = make_job(JobKind.ANALYSE)

    with pytest.raises(AnalysisUnavailableError) as caught:
        analyse_job(job, repositories, settings, runner)

    assert str(caught.value) == reason
    repositories.save_findings.assert_not_called()
    repositories.upload.assert_not_called()
    repositories.set_audit_status.assert_not_called()
    repositories.enqueue.assert_not_called()
    assert not (settings.radar_work_dir / "analyse" / str(job.id)).exists()


def test_missing_cli_is_reported_as_not_installed(
    repositories: MagicMock, settings: Settings, make_job: JobFactory
) -> None:
    """The test settings name a command that does not exist."""
    with pytest.raises(AnalysisUnavailableError, match="nicht installiert"):
        analyse_job(make_job(JobKind.ANALYSE), repositories, settings)


def test_failed_delivery_removes_the_folder(
    repositories: MagicMock, settings: Settings, make_job: JobFactory
) -> None:
    """A storage error propagates for a retry and leaves no folder behind for `jobs push`."""
    repositories.save_findings.side_effect = StorageError("database down")
    job = make_job(JobKind.ANALYSE)

    with pytest.raises(StorageError, match="database down"):
        analyse_job(job, repositories, settings, writing_runner(VALID_TEXTS))

    assert not (settings.radar_work_dir / "analyse" / str(job.id)).exists()


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("ok", None),
        ("not_logged_in", "Claude Code ist nicht angemeldet"),
        ("invalid", "Claude Code hat ungültige Dateien geschrieben"),
        ("sleep", "Claude Code hat nicht rechtzeitig geantwortet"),
        ("error", "Claude Code hat mit einem Fehler geantwortet"),
    ],
)
def test_with_the_fake_cli(
    repositories: MagicMock,
    claude_settings: Settings,
    make_job: JobFactory,
    sample_audit: Audit,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    reason: str | None,
) -> None:
    """The real subprocess call: success, missing login, invalid files, timeout and an error."""
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    quick = claude_settings.model_copy(update={"analysis_timeout_seconds": 1})

    if reason is not None:
        with pytest.raises(AnalysisUnavailableError) as caught:
            analyse_job(make_job(JobKind.ANALYSE), repositories, quick)
        assert str(caught.value) == reason
        repositories.set_audit_status.assert_not_called()
        return
    record = analyse_job(make_job(JobKind.ANALYSE), repositories, quick)
    assert record.path is AnalysisPath.CLAUDE_CODE
    repositories.set_audit_status.assert_called_once()
    assert repositories.set_audit_status.call_args.args[0] == sample_audit.id


@pytest.mark.parametrize(
    ("attempts", "wait"),
    [
        (1, timedelta(minutes=10)),
        (2, timedelta(minutes=30)),
        (3, timedelta(hours=2)),
        (4, timedelta(hours=6)),
        (20, timedelta(hours=6)),
    ],
)
def test_backoff_steps_then_every_six_hours(attempts: int, wait: timedelta) -> None:
    """10 min, 30 min, 2 h, then 6 h for good; the job never gives up."""
    now = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)

    assert retry_at(attempts, now) == now + wait
