"""Tests for deferred Google searches in the worker: scheduling, retry runs and re-scoring.

Context: Unit tests with fake repositories; the search run and the scoring are monkeypatched.
| Input: Fixtures. | Output: Assertions.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from conftest import JobFactory

from radarauditor.collectors.browser import BrowserService
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import DeferredSearch, RawAudit, SerpResult
from radarauditor.config import Settings
from radarauditor.db.models import Audit, AuditStatus, CheckResult, JobKind, JobStatus
from radarauditor.errors import StorageError
from radarauditor.jobs import serp_retry, worker

DEFERRED = [DeferredSearch(phrase="Dachdecker Münster"), DeferredSearch(phrase="Flachdach Münster")]


def _serp(phrase: str) -> SerpResult:
    return SerpResult(
        phrase=phrase,
        organic=[(1, "dachdeckerei-muster.de")],
        local_pack=[],
        ai_overview_present=False,
        ai_overview_mentions=[],
        people_also_ask=[],
        measured_at=datetime(2026, 10, 23, tzinfo=UTC),
    )


@pytest.fixture
def deferred_raw(sample_raw_audit: RawAudit) -> RawAudit:
    """Raw data whose Google searches were deferred."""
    return sample_raw_audit.model_copy(update={"serps": [], "deferred_searches": DEFERRED})


@pytest.fixture
def stored_raw(fake_repositories: MagicMock, deferred_raw: RawAudit) -> list[RawAudit]:
    """Serve deferred_raw, or the last upload, from storage and collect every raw upload."""
    uploads: list[RawAudit] = []

    def download(bucket: str, remote_path: str, local_file: Path) -> Path:
        current = uploads[-1] if uploads else deferred_raw
        local_file.write_text(current.model_dump_json(), encoding="utf-8")
        return local_file

    def upload(bucket: str, remote_path: str, local_file: Path) -> str:
        uploads.append(RawAudit.model_validate_json(local_file.read_text(encoding="utf-8")))
        return remote_path

    fake_repositories.download.side_effect = download
    fake_repositories.upload.side_effect = upload
    return uploads


@pytest.fixture
def scoring(monkeypatch: pytest.MonkeyPatch, sample_check_results: list[CheckResult]) -> MagicMock:
    """Replace the catalogue evaluation; returns the evaluate mock."""
    evaluate = MagicMock(return_value=sample_check_results)
    monkeypatch.setattr("radarauditor.scoring.catalog.load_catalogue", MagicMock())
    monkeypatch.setattr("radarauditor.scoring.evaluator.evaluate", evaluate)
    monkeypatch.setattr(
        "radarauditor.scoring.evaluator.total_score", MagicMock(return_value=Decimal("70"))
    )
    monkeypatch.setattr(
        "radarauditor.scoring.potential.with_potential",
        lambda results, catalogue, gain: results,
    )
    return evaluate


def _retry_run(
    monkeypatch: pytest.MonkeyPatch, raw: RawAudit, found: list[SerpResult], left: int
) -> AsyncMock:
    remaining = DEFERRED[len(found) :][:left] if left else []
    merged = serp_retry.merge(raw, found, remaining, "bot check" if remaining else None)
    search = AsyncMock(
        return_value=serp_retry.RetryRun(
            merged, len(found), remaining, "bot check" if remaining else None
        )
    )
    monkeypatch.setattr(serp_retry, "search_deferred", search)
    return search


def _with_status(audit: Audit, status: AuditStatus) -> Audit:
    return audit.model_copy(update={"status": status})


async def test_collect_with_deferred_searches_queues_a_retry(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    deferred_raw: RawAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COLLECT finishes and queues a serp_retry job for the audit 15 minutes later."""
    job = make_job(JobKind.COLLECT)
    monkeypatch.setattr(
        "radarauditor.collectors.run.collect_audit", AsyncMock(return_value=deferred_raw)
    )
    before = datetime.now(UTC)

    await worker.run_job(job, fake_repositories, settings)

    retry, score = fake_repositories.enqueue.call_args_list
    assert retry.args == (JobKind.SERP_RETRY, job.customer_id, job.audit_id)
    run_after = retry.kwargs["run_after"]
    assert before + timedelta(minutes=15) <= run_after <= datetime.now(UTC) + timedelta(minutes=15)
    assert score == call(JobKind.SCORE, job.customer_id, job.audit_id)
    assert fake_repositories.set_audit_status.call_args_list[-1] == call(
        job.audit_id, AuditStatus.COLLECTED
    )


async def test_collect_without_deferred_searches_queues_no_retry(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_raw_audit: RawAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a block only scoring is queued."""
    monkeypatch.setattr(
        "radarauditor.collectors.run.collect_audit", AsyncMock(return_value=sample_raw_audit)
    )

    await worker.run_job(make_job(JobKind.COLLECT), fake_repositories, settings)

    assert [entry.args[0] for entry in fake_repositories.enqueue.call_args_list] == [JobKind.SCORE]


@pytest.mark.usefixtures("stored_raw")
async def test_retry_success_rescores_a_reported_audit(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_audit: Audit,
    deferred_raw: RawAudit,
    stored_raw: list[RawAudit],
    scoring: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found rankings are stored, the audit is re-scored and analysis and report run again."""
    job = make_job(JobKind.SERP_RETRY)
    fake_repositories.audit.return_value = _with_status(sample_audit, AuditStatus.REPORTED)
    search = _retry_run(monkeypatch, deferred_raw, [_serp(item.phrase) for item in DEFERRED], 0)

    await worker.process_job(job, fake_repositories, settings)

    raw, browser, gate = search.await_args.args
    assert raw == deferred_raw
    assert isinstance(browser, BrowserService)
    assert isinstance(gate, SourceGate)
    assert stored_raw[0].deferred_searches == []
    assert [serp.phrase for serp in stored_raw[0].serps] == [item.phrase for item in DEFERRED]
    fake_repositories.save_rankings.assert_called_once()
    assert scoring.call_args.args[1] == stored_raw[0]
    fake_repositories.save_check_results.assert_called_once()
    fake_repositories.enqueue.assert_called_once_with(
        JobKind.ANALYSE, job.customer_id, job.audit_id
    )
    assert fake_repositories.set_audit_status.call_args_list == [
        call(job.audit_id, AuditStatus.SCORED),
        call(job.audit_id, AuditStatus.AWAITING_ANALYSIS),
    ]
    fake_repositories.finish_job.assert_called_once_with(job.id, JobStatus.DONE)


@pytest.mark.usefixtures("stored_raw")
async def test_retry_success_before_scoring_only_stores_the_data(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_audit: Audit,
    deferred_raw: RawAudit,
    scoring: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An audit whose SCORE job is still queued scores the merged data itself."""
    fake_repositories.audit.return_value = _with_status(sample_audit, AuditStatus.COLLECTED)
    _retry_run(monkeypatch, deferred_raw, [_serp(item.phrase) for item in DEFERRED], 0)

    await worker.process_job(make_job(JobKind.SERP_RETRY), fake_repositories, settings)

    fake_repositories.save_rankings.assert_called_once()
    scoring.assert_not_called()
    fake_repositories.enqueue.assert_not_called()


async def test_retry_still_blocked_keeps_partial_results_and_waits(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    deferred_raw: RawAudit,
    stored_raw: list[RawAudit],
    scoring: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial run stores what it found, re-scores and requeues for one hour later."""
    job = make_job(JobKind.SERP_RETRY, attempts=1)
    _retry_run(monkeypatch, deferred_raw, [_serp(DEFERRED[0].phrase)], 1)
    before = datetime.now(UTC)

    await worker.process_job(job, fake_repositories, settings)

    assert stored_raw[0].deferred_searches == DEFERRED[1:]
    scoring.assert_called_once()
    fake_repositories.requeue_job.assert_called_once()
    job_id, message = fake_repositories.requeue_job.call_args.args
    assert job_id == job.id
    assert message == "1 Google searches still deferred: bot check"
    run_after = fake_repositories.requeue_job.call_args.kwargs["run_after"]
    assert before + timedelta(hours=1) <= run_after <= datetime.now(UTC) + timedelta(hours=1)
    fake_repositories.finish_job.assert_not_called()
    assert call(job.audit_id, AuditStatus.FAILED, message) not in (
        fake_repositories.set_audit_status.call_args_list
    )


@pytest.mark.usefixtures("stored_raw")
async def test_retry_gives_up_after_the_fourth_run_without_failing_the_audit(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    deferred_raw: RawAudit,
    scoring: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last unsuccessful run fails only the job, with the reason; nothing is re-scored."""
    job = make_job(JobKind.SERP_RETRY, attempts=4)
    _retry_run(monkeypatch, deferred_raw, [], 2)

    await worker.process_job(job, fake_repositories, settings)

    fake_repositories.requeue_job.assert_not_called()
    fake_repositories.finish_job.assert_called_once_with(
        job.id,
        JobStatus.FAILED,
        "Google search still blocked after 4 attempts, giving up: "
        "2 Google searches still deferred: bot check",
    )
    fake_repositories.set_audit_status.assert_not_called()
    fake_repositories.upload.assert_not_called()
    scoring.assert_not_called()


async def test_retry_follows_the_backoff_on_other_errors(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A storage error in a retry run waits for the next step instead of retrying at once."""
    job = make_job(JobKind.SERP_RETRY, attempts=3)
    fake_repositories.download.side_effect = StorageError("offline")

    await worker.process_job(job, fake_repositories, settings)

    message = fake_repositories.requeue_job.call_args.args[1]
    assert message == "StorageError: offline"
    assert "run_after" in fake_repositories.requeue_job.call_args.kwargs
    fake_repositories.set_audit_status.assert_not_called()


async def test_retry_without_deferred_searches_is_done(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_raw_audit: RawAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When nothing is deferred any more, the job ends without searching."""
    search = AsyncMock()
    monkeypatch.setattr(serp_retry, "search_deferred", search)

    def download(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(sample_raw_audit.model_dump_json(), encoding="utf-8")
        return local_file

    fake_repositories.download.side_effect = download
    job = make_job(JobKind.SERP_RETRY)

    await worker.process_job(job, fake_repositories, settings)

    search.assert_not_awaited()
    fake_repositories.finish_job.assert_called_once_with(job.id, JobStatus.DONE)


def test_retry_jobs_are_claimed_after_the_audit_pipeline() -> None:
    """Due retries run after new audits and before order work."""
    assert worker.PRIORITY.index(JobKind.SERP_RETRY) == worker.PRIORITY.index(JobKind.REPORT) + 1
    assert worker.PRIORITY.index(JobKind.SERP_RETRY) < worker.PRIORITY.index(JobKind.APPLY)
