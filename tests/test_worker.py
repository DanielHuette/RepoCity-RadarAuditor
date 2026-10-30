"""Tests for the worker loop and job dispatch.

Context: Unit tests with fake repositories; collectors, scoring and reporting are monkeypatched.
| Input: Fixtures. | Output: Assertions.
"""

import asyncio
import os
import signal
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call
from uuid import uuid4

import pytest
from pydantic import HttpUrl

import radarauditor
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import RawAudit
from radarauditor.config import Settings, load_settings
from radarauditor.db.app_settings import Source, WorkerCapabilities, WorkerHeartbeat
from radarauditor.db.models import (
    Audit,
    AuditKind,
    AuditPurpose,
    AuditStatus,
    CheckResult,
    Customer,
    Job,
    JobKind,
    JobStatus,
    Keyword,
    Ranking,
)
from radarauditor.errors import (
    AnalysisUnavailableError,
    ConfigurationError,
    RadarError,
    StorageError,
)
from radarauditor.jobs import worker
from radarauditor.jobs.heartbeat import CapabilityProbe
from radarauditor.jobs.mail_triage import ReadOutcome

JobFactory = Callable[..., Job]
AVAILABLE = WorkerCapabilities(
    chromium=False,
    claude_cli=True,
    claude_logged_in=True,
    browser_profile=False,
    analysis_available=True,
    mail_reading_available=True,
)
NOT_LOGGED_IN = AVAILABLE.model_copy(
    update={
        "claude_logged_in": False,
        "analysis_available": False,
        "analysis_reason": "Claude Code ist nicht angemeldet",
        "mail_reading_available": False,
        "mail_reading_reason": "Claude Code ist nicht angemeldet",
    }
)
CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "config" / "pruefkatalog.yaml"


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll without real waiting."""
    monkeypatch.setattr(worker, "POLL_SECONDS", 0.01)


@pytest.fixture(autouse=True)
def no_host_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the serve loop from probing the test machine for Chromium and Claude Code."""
    monkeypatch.setattr(worker, "detect_capabilities", lambda settings: AVAILABLE)


def test_claim_next_follows_priority(fake_repositories: MagicMock, make_job: JobFactory) -> None:
    """Score is taken only when no collect job is queued."""
    score_job = make_job(JobKind.SCORE)
    fake_repositories.claim_jobs.side_effect = lambda kind, limit: (
        [score_job] if kind is JobKind.SCORE else []
    )

    assert worker.claim_next(fake_repositories) is score_job
    assert [c.args for c in fake_repositories.claim_jobs.call_args_list] == [
        (JobKind.COLLECT, 1),
        (JobKind.SCORE, 1),
    ]


def test_claim_next_skips_paused_kinds(fake_repositories: MagicMock) -> None:
    """A paused kind is not even asked for."""
    assert worker.claim_next(fake_repositories, frozenset({JobKind.COLLECT})) is None
    kinds = [c.args[0] for c in fake_repositories.claim_jobs.call_args_list]
    assert JobKind.COLLECT not in kinds
    assert len(kinds) == len(worker.PRIORITY) - 1


@pytest.mark.parametrize(
    ("capabilities", "paused"),
    [(AVAILABLE, frozenset()), (NOT_LOGGED_IN, frozenset({JobKind.COLLECT})), (None, frozenset())],
)
def test_collect_pauses_only_while_analysis_is_unavailable(
    capabilities: WorkerCapabilities | None, paused: frozenset[JobKind]
) -> None:
    """Without Claude Code no new audit starts; unknown capabilities pause nothing."""
    assert worker.paused_kinds(capabilities) == paused


def test_claim_next_returns_none_on_empty_queue(fake_repositories: MagicMock) -> None:
    """All four kinds are asked once, then None."""
    assert worker.claim_next(fake_repositories) is None
    assert fake_repositories.claim_jobs.call_count == len(worker.PRIORITY)


async def test_process_job_marks_success(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished job is marked done."""
    job = make_job(JobKind.REPORT)
    monkeypatch.setattr(worker, "run_job", AsyncMock())

    await worker.process_job(job, fake_repositories, settings)

    fake_repositories.finish_job.assert_called_once_with(job.id, JobStatus.DONE)


async def test_process_job_requeues_before_last_attempt(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An early failure puts the job back with the error message."""
    job = make_job(JobKind.COLLECT, attempts=1)
    monkeypatch.setattr(worker, "run_job", AsyncMock(side_effect=ValueError("site down")))

    await worker.process_job(job, fake_repositories, settings)

    fake_repositories.requeue_job.assert_called_once_with(job.id, "ValueError: site down")
    fake_repositories.finish_job.assert_not_called()
    fake_repositories.set_audit_status.assert_not_called()


async def test_process_job_fails_audit_on_last_attempt(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last failed attempt fails the job and the audit."""
    job = make_job(JobKind.COLLECT, attempts=worker.MAX_ATTEMPTS)
    monkeypatch.setattr(worker, "run_job", AsyncMock(side_effect=StorageError("bucket gone")))

    await worker.process_job(job, fake_repositories, settings)

    message = "StorageError: bucket gone"
    fake_repositories.finish_job.assert_called_once_with(job.id, JobStatus.FAILED, message)
    fake_repositories.set_audit_status.assert_called_once_with(
        job.audit_id, AuditStatus.FAILED, message
    )
    fake_repositories.requeue_job.assert_not_called()


async def test_process_job_survives_unrecordable_failure(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Supabase is down the failure is only logged."""
    job = make_job(JobKind.COLLECT)
    monkeypatch.setattr(worker, "run_job", AsyncMock(side_effect=ValueError("x")))
    fake_repositories.requeue_job.side_effect = StorageError("offline")

    await worker.process_job(job, fake_repositories, settings)


async def test_process_job_survives_unmarkable_success(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure to mark the job done is only logged."""
    monkeypatch.setattr(worker, "run_job", AsyncMock())
    fake_repositories.finish_job.side_effect = StorageError("offline")

    await worker.process_job(make_job(JobKind.REPORT), fake_repositories, settings)


async def test_collect_uploads_raw_audit_and_enqueues_score(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_raw_audit: RawAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COLLECT stores the RawAudit, advances the audit and queues scoring."""
    job = make_job(JobKind.COLLECT)
    collect = AsyncMock(return_value=sample_raw_audit)
    monkeypatch.setattr("radarauditor.collectors.run.collect_audit", collect)
    uploaded: list[str] = []

    def capture(bucket: str, remote_path: str, local_file: Path) -> str:
        uploaded.append(local_file.read_text(encoding="utf-8"))
        return remote_path

    fake_repositories.upload.side_effect = capture

    await worker.run_job(job, fake_repositories, settings)

    collect.assert_awaited_once()
    assert collect.await_args is not None
    assert collect.await_args.args[:3] == (job.audit_id, fake_repositories, settings)
    assert isinstance(collect.await_args.args[3], SourceGate)
    assert RawAudit.model_validate_json(uploaded[0]) == sample_raw_audit
    raw_path = f"{job.audit_id}/raw.json"
    fake_repositories.upload.assert_called_once()
    assert fake_repositories.upload.call_args.args[:2] == ("raw", raw_path)
    fake_repositories.update_audit.assert_called_once_with(job.audit_id, raw_data_path=raw_path)
    assert fake_repositories.set_audit_status.call_args_list == [
        call(job.audit_id, AuditStatus.COLLECTING),
        call(job.audit_id, AuditStatus.COLLECTED),
    ]
    fake_repositories.enqueue.assert_called_once_with(JobKind.SCORE, job.customer_id, job.audit_id)


async def test_score_saves_results_and_enqueues_analysis(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_raw_audit: RawAudit,
    sample_check_results: list[CheckResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SCORE evaluates the stored RawAudit and hands the audit to analysis."""
    job = make_job(JobKind.SCORE)
    catalogue = object()
    load_catalogue = MagicMock(return_value=catalogue)
    evaluate = MagicMock(return_value=sample_check_results)
    total_score = MagicMock(return_value=Decimal("61.50"))
    enriched = [
        result.model_copy(update={"potential_points": Decimal(1)})
        for result in sample_check_results
    ]
    with_potential = MagicMock(return_value=enriched)
    monkeypatch.setattr("radarauditor.scoring.catalog.load_catalogue", load_catalogue)
    monkeypatch.setattr("radarauditor.scoring.evaluator.evaluate", evaluate)
    monkeypatch.setattr("radarauditor.scoring.evaluator.total_score", total_score)
    monkeypatch.setattr("radarauditor.scoring.potential.with_potential", with_potential)

    def write(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(sample_raw_audit.model_dump_json(), encoding="utf-8")
        return local_file

    fake_repositories.download.side_effect = write

    await worker.run_job(job, fake_repositories, settings)

    load_catalogue.assert_called_once_with(settings.catalogue_path)
    evaluate.assert_called_once_with(
        job.audit_id,
        sample_raw_audit,
        catalogue,
        paid_enabled=True,
        with_access=False,
        disabled_checks=frozenset(),
    )
    with_potential.assert_called_once_with(sample_check_results, catalogue, (0.0, 0.0))
    fake_repositories.save_check_results.assert_called_once_with(enriched)
    total_score.assert_called_once_with(enriched, catalogue)
    fake_repositories.update_audit.assert_called_once_with(
        job.audit_id, total_score=Decimal("61.50")
    )
    assert fake_repositories.set_audit_status.call_args_list == [
        call(job.audit_id, AuditStatus.SCORED),
        call(job.audit_id, AuditStatus.AWAITING_ANALYSIS),
    ]
    fake_repositories.enqueue.assert_called_once_with(
        JobKind.ANALYSE, job.customer_id, job.audit_id
    )


async def test_score_passes_paid_and_access_context(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_audit: Audit,
    sample_raw_audit: RawAudit,
    sample_check_results: list[CheckResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paid rankings come from the settings, customer access from the audit kind."""
    fake_repositories.audit.return_value = sample_audit.model_copy(
        update={"kind": AuditKind.WITH_ACCESS}
    )
    evaluate = MagicMock(return_value=sample_check_results)
    monkeypatch.setattr("radarauditor.scoring.catalog.load_catalogue", MagicMock())
    monkeypatch.setattr("radarauditor.scoring.evaluator.evaluate", evaluate)
    monkeypatch.setattr("radarauditor.scoring.evaluator.total_score", MagicMock())
    monkeypatch.setattr(
        "radarauditor.scoring.potential.with_potential", lambda results, *_: results
    )

    def write(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(sample_raw_audit.model_dump_json(), encoding="utf-8")
        return local_file

    fake_repositories.download.side_effect = write
    await worker.run_job(make_job(JobKind.SCORE), fake_repositories, settings)

    assert evaluate.call_args.kwargs == {
        "paid_enabled": True,
        "with_access": True,
        "disabled_checks": frozenset(),
    }


async def test_score_applies_the_app_settings(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_raw_audit: RawAudit,
    sample_check_results: list[CheckResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dataforseo switch is read from table settings; disabled checks come from the audit."""
    fake_repositories.settings_values.return_value = {
        "sources": {"dataforseo": {"enabled": False}},
    }
    evaluate = MagicMock(return_value=sample_check_results)
    monkeypatch.setattr("radarauditor.scoring.catalog.load_catalogue", MagicMock())
    monkeypatch.setattr("radarauditor.scoring.evaluator.evaluate", evaluate)
    monkeypatch.setattr("radarauditor.scoring.evaluator.total_score", MagicMock())

    def write(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(sample_raw_audit.model_dump_json(), encoding="utf-8")
        return local_file

    fake_repositories.download.side_effect = write
    await worker.run_job(make_job(JobKind.SCORE), fake_repositories, settings)

    fake_repositories.settings_values.assert_called_once_with(("sources",))
    assert evaluate.call_args.kwargs["paid_enabled"] is False
    assert evaluate.call_args.kwargs["disabled_checks"] == frozenset()


async def test_collect_gate_counts_against_the_database(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_raw_audit: RawAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate handed to the collectors uses the job's caps and consume_source_quota."""
    fake_repositories.settings_values.return_value = {
        "sources": {"google_search": {"daily_cap": 7}}
    }
    collect = AsyncMock(return_value=sample_raw_audit)
    monkeypatch.setattr("radarauditor.collectors.run.collect_audit", collect)

    await worker.run_job(make_job(JobKind.COLLECT), fake_repositories, settings)

    assert collect.await_args is not None
    gate: SourceGate = collect.await_args.args[3]
    gate.acquire(Source.GOOGLE_SEARCH)
    fake_repositories.consume_source_quota.assert_called_once_with("google_search", 7)


async def test_collect_gate_enforces_the_monthly_cost_cap(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_raw_audit: RawAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate reads and adds the DataForSEO spend through the repositories."""
    fake_repositories.settings_values.return_value = {
        "sources": {"dataforseo": {"monthly_cost_cap_eur": 5}}
    }
    fake_repositories.month_source_cost.return_value = Decimal("4.99")
    fake_repositories.add_source_cost.return_value = Decimal("5.01")
    collect = AsyncMock(return_value=sample_raw_audit)
    monkeypatch.setattr("radarauditor.collectors.run.collect_audit", collect)

    await worker.run_job(make_job(JobKind.COLLECT), fake_repositories, settings)

    assert collect.await_args is not None
    gate: SourceGate = collect.await_args.args[3]
    gate.acquire(Source.DATAFORSEO)
    gate.record_cost(Source.DATAFORSEO, Decimal("0.02"))
    fake_repositories.month_source_cost.assert_called_once_with("dataforseo")
    fake_repositories.add_source_cost.assert_called_once_with("dataforseo", Decimal("0.02"))
    assert gate.cost_capped == {Source.DATAFORSEO}


async def test_malformed_settings_fail_the_job(
    fake_repositories: MagicMock, settings: Settings, make_job: JobFactory
) -> None:
    """A settings row of the wrong shape stops the job with a readable error."""
    fake_repositories.settings_values.return_value = {"sources": []}

    with pytest.raises(ConfigurationError, match="sources"):
        await worker.run_job(make_job(JobKind.COLLECT), fake_repositories, settings)


async def test_beat_writes_the_heartbeat(fake_repositories: MagicMock) -> None:
    """Each beat stores time, version and capabilities under worker_heartbeat."""
    capabilities = NOT_LOGGED_IN
    probe = CapabilityProbe(lambda: capabilities)

    assert await worker.beat(fake_repositories, probe) == capabilities

    key, value = fake_repositories.set_setting.call_args.args
    assert key == "worker_heartbeat"
    heartbeat = WorkerHeartbeat.model_validate(value)
    assert heartbeat.capabilities == capabilities
    assert heartbeat.version == radarauditor.__version__


async def test_beat_survives_storage_errors(fake_repositories: MagicMock) -> None:
    """A failed heartbeat write is logged, not raised."""
    fake_repositories.set_setting.side_effect = StorageError("offline")
    probe = CapabilityProbe(lambda: AVAILABLE)

    assert await worker.beat(fake_repositories, probe) == AVAILABLE

    fake_repositories.set_setting.assert_called_once()


async def test_beat_without_detection_writes_nothing(fake_repositories: MagicMock) -> None:
    """A failed detection is logged and reported as unknown."""

    def broken() -> WorkerCapabilities:
        raise OSError("no process table")

    assert await worker.beat(fake_repositories, CapabilityProbe(broken)) is None
    fake_repositories.set_setting.assert_not_called()


async def test_serve_claims_no_collect_jobs_without_claude_code(
    fake_repositories: MagicMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While the heartbeat says analysis is unavailable, collect jobs stay queued."""
    stop = asyncio.Event()
    monkeypatch.setattr(worker, "detect_capabilities", lambda settings: NOT_LOGGED_IN)
    kinds: list[JobKind] = []

    def claim(kind: JobKind, limit: int) -> list[Job]:
        kinds.append(kind)
        if kind is worker.PRIORITY[-1]:
            stop.set()
        return []

    fake_repositories.claim_jobs.side_effect = claim

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    assert kinds == [kind for kind in worker.PRIORITY if kind is not JobKind.COLLECT]


class FakeReader:
    """Stands in for MailReader: hands out the given outcomes and records the order of calls."""

    def __init__(self, outcomes: list[ReadOutcome], stop: asyncio.Event, order: list[str]) -> None:
        """Remember the outcomes, the event that ends the loop and the shared order list."""
        self.outcomes = outcomes
        self.stop = stop
        self.order = order
        self.calls = 0

    def read_one(self, now: datetime) -> ReadOutcome:
        """Return the next outcome; the last one stops the loop."""
        self.calls += 1
        self.order.append("mail")
        if self.calls >= len(self.outcomes):
            self.stop.set()
        return self.outcomes[min(self.calls, len(self.outcomes)) - 1]


def use_reader(monkeypatch: pytest.MonkeyPatch, reader: FakeReader) -> None:
    monkeypatch.setattr(
        "radarauditor.jobs.mail_triage.MailReader", lambda repositories, settings: reader
    )


READ = ReadOutcome(handled=True, problem=None, claude_unavailable=False)
NO_LOGIN = ReadOutcome(
    handled=False, problem="Claude Code ist nicht angemeldet", claude_unavailable=True
)


async def test_idle_polls_issue_documents_before_reading_one_mail(
    fake_repositories: MagicMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requested PDFs never wait for mail reading, and each idle poll reads one mail."""
    stop = asyncio.Event()
    order: list[str] = []
    reader = FakeReader([READ, READ], stop, order)
    use_reader(monkeypatch, reader)
    monkeypatch.setattr(
        "radarauditor.documents.requests.process_pending_requests",
        lambda repositories, current: order.append("documents") or 0,
    )

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    assert order == ["documents", "mail", "documents", "mail"]


@pytest.mark.parametrize(
    ("capabilities", "batch"), [(NOT_LOGGED_IN, 5), (AVAILABLE, 0)], ids=["no_claude", "off"]
)
async def test_no_mails_are_read_without_claude_code_or_when_switched_off(
    fake_repositories: MagicMock,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capabilities: WorkerCapabilities,
    batch: int,
) -> None:
    """Claimed mails would only lose an attempt, so none are claimed."""
    stop = asyncio.Event()
    monkeypatch.setattr(worker, "detect_capabilities", lambda current: capabilities)
    reader = FakeReader([READ], asyncio.Event(), [])
    use_reader(monkeypatch, reader)
    polls: list[int] = []

    def claim(kind: JobKind, limit: int) -> list[Job]:
        if kind is worker.PRIORITY[-1]:
            polls.append(1)
            if len(polls) >= 2:
                stop.set()
        return []

    fake_repositories.claim_jobs.side_effect = claim
    quiet = settings.model_copy(update={"mail_triage_batch": batch})

    await asyncio.wait_for(worker.serve(fake_repositories, quiet, stop), timeout=2)

    assert reader.calls == 0


async def test_a_mail_problem_pauses_reading_until_claude_code_is_checked_again(
    fake_repositories: MagicMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a lost login the probe runs again at once, and reading waits for its answer."""
    stop = asyncio.Event()
    order: list[str] = []
    reader = FakeReader([NO_LOGIN, READ], stop, order)
    use_reader(monkeypatch, reader)
    detections: list[str] = []

    def detect(current: Settings) -> WorkerCapabilities:
        detections.append("detect")
        order.append("detect")
        return AVAILABLE

    monkeypatch.setattr(worker, "detect_capabilities", detect)

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    first_mail = order.index("mail")
    second_mail = order.index("mail", first_mail + 1)
    assert "detect" in order[first_mail:second_mail]
    assert len(detections) >= 2


async def test_a_mail_problem_reaches_the_heartbeat(
    fake_repositories: MagicMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app learns from the heartbeat that mails are not read and why."""
    stop = asyncio.Event()
    problem = "Die Mails im CRM lassen sich nicht abholen"
    outcome = ReadOutcome(handled=False, problem=problem, claude_unavailable=False)
    use_reader(monkeypatch, FakeReader([outcome, outcome], asyncio.Event(), []))
    reasons: list[str | None] = []

    def store(key: str, value: dict[str, object]) -> None:
        capabilities = WorkerHeartbeat.model_validate(value).capabilities
        reasons.append(capabilities.mail_reading_reason)
        if capabilities.mail_reading_reason == problem:
            stop.set()

    fake_repositories.set_setting.side_effect = store

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    assert reasons[0] is None
    assert reasons[-1] == problem


async def test_a_mail_run_that_fails_outright_is_logged(
    fake_repositories: MagicMock,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unexpected error while reading mails does not stop the worker."""
    stop = asyncio.Event()

    class Broken:
        def read_one(self, now: datetime) -> ReadOutcome:
            stop.set()
            raise StorageError("offline")

    monkeypatch.setattr(
        "radarauditor.jobs.mail_triage.MailReader", lambda repositories, settings: Broken()
    )

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    assert "Reading mails failed: offline" in caplog.text


async def test_collect_saves_rankings_derived_from_raw_audit(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    raw_audit_full: RawAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COLLECT stores one ranking row per searched keyword and subject before scoring."""
    job = make_job(JobKind.COLLECT)
    monkeypatch.setattr(
        "radarauditor.collectors.run.collect_audit", AsyncMock(return_value=raw_audit_full)
    )
    keyword = Keyword(
        id=uuid4(),
        customer_id=job.customer_id,
        phrase="Dachdecker",
        city="Münster",
        monthly_volume=None,
        is_primary=True,
    )
    fake_repositories.keywords.return_value = [keyword]
    fake_repositories.customer.return_value = fake_repositories.customer.return_value.model_copy(
        update={"website": HttpUrl("https://www.dachdeckerei-muster.de/")}
    )

    await worker.run_job(job, fake_repositories, settings)

    (rankings,) = fake_repositories.save_rankings.call_args.args
    assert [(r.subject, r.organic_position, r.local_pack_position) for r in rankings] == [
        ("customer", 2, 1)
    ]
    assert rankings[0].keyword_id == keyword.id


async def test_collect_without_website_uses_crawled_host_or_none(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    raw_audit_full: RawAudit,
    sample_customer: Customer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a website the crawl's host is used; without a profile the customer name is."""
    job = make_job(JobKind.COLLECT)
    collect = AsyncMock(return_value=raw_audit_full)
    monkeypatch.setattr("radarauditor.collectors.run.collect_audit", collect)
    fake_repositories.keywords.return_value = [
        Keyword(
            id=uuid4(),
            customer_id=job.customer_id,
            phrase="Dachdecker",
            city="Münster",
            monthly_volume=None,
            is_primary=True,
        )
    ]
    fake_repositories.customer.return_value = sample_customer.model_copy(update={"website": None})

    await worker.run_job(job, fake_repositories, settings)
    assert fake_repositories.save_rankings.call_args.args[0][0].organic_position == 2

    collect.return_value = raw_audit_full.model_copy(update={"website": None, "place": None})
    await worker.run_job(job, fake_repositories, settings)
    (ranking,) = fake_repositories.save_rankings.call_args.args[0]
    assert (ranking.organic_position, ranking.local_pack_position) == (None, 1)


async def test_score_without_raw_data_fails(
    fake_repositories: MagicMock, settings: Settings, make_job: JobFactory, sample_audit: Audit
) -> None:
    """An audit without raw data cannot be scored."""
    fake_repositories.audit.return_value = sample_audit.model_copy(update={"raw_data_path": None})

    with pytest.raises(RadarError, match="no raw data"):
        await worker.run_job(make_job(JobKind.SCORE), fake_repositories, settings)


@pytest.mark.parametrize(
    ("purpose", "has_previous", "expected"),
    [
        (AuditPurpose.SALES, False, "sales"),
        (AuditPurpose.SALES, True, "sales"),
        (AuditPurpose.MONTHLY, False, "sales"),
        (AuditPurpose.MONTHLY, True, "monthly"),
    ],
)
async def test_report_picks_report_type_and_finishes_audit(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_audit: Audit,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    purpose: AuditPurpose,
    has_previous: bool,
    expected: str,
) -> None:
    """Monthly audits with an earlier report get the before-after report, all others sales."""
    job = make_job(JobKind.REPORT)
    pdf = tmp_path / "bericht.pdf"
    builders = {"sales": MagicMock(return_value=pdf), "monthly": MagicMock(return_value=pdf)}
    monkeypatch.setattr("radarauditor.reporting.report.build_sales_report", builders["sales"])
    monkeypatch.setattr("radarauditor.reporting.report.build_monthly_report", builders["monthly"])
    fake_repositories.audit.return_value = sample_audit.model_copy(update={"purpose": purpose})
    fake_repositories.previous_audit.return_value = sample_audit if has_previous else None

    await worker.run_job(job, fake_repositories, settings)

    builders[expected].assert_called_once_with(job.audit_id, fake_repositories, settings)
    report_path = f"{job.audit_id}/bericht.pdf"
    fake_repositories.upload.assert_called_once_with("reports", report_path, pdf)
    fake_repositories.update_audit.assert_called_once_with(job.audit_id, report_path=report_path)
    fake_repositories.set_audit_status.assert_called_once_with(job.audit_id, AuditStatus.REPORTED)
    kind, payload = fake_repositories.new_event.call_args.args
    assert kind == "audit.reported"
    assert payload["audit_id"] == str(job.audit_id)


@pytest.mark.parametrize("kind", [JobKind.COLLECT, JobKind.SCORE, JobKind.REPORT])
async def test_audit_jobs_need_an_audit(
    fake_repositories: MagicMock, settings: Settings, make_job: JobFactory, kind: JobKind
) -> None:
    """Collect, score and report jobs without audit id are rejected."""
    with pytest.raises(RadarError, match="no audit id"):
        await worker.run_job(make_job(kind, audit_id=None), fake_repositories, settings)


async def test_analyse_jobs_run_the_analysis_step(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ANALYSE jobs go to the analysis step with the job, repositories and settings."""
    analyse_job = MagicMock()
    monkeypatch.setattr("radarauditor.jobs.analysis.analyse_job", analyse_job)
    job = make_job(JobKind.ANALYSE)

    await worker.run_job(job, fake_repositories, settings)

    analyse_job.assert_called_once_with(job, fake_repositories, settings)


@pytest.mark.parametrize(
    ("mode", "attempts", "reason", "wait"),
    [
        ("not_logged_in", 1, "Claude Code ist nicht angemeldet", timedelta(minutes=10)),
        ("sleep", 2, "Claude Code hat nicht rechtzeitig geantwortet", timedelta(minutes=30)),
        ("invalid", 3, "Claude Code hat ungültige Dateien geschrieben", timedelta(hours=2)),
        ("error", 4, "Claude Code hat mit einem Fehler geantwortet", timedelta(hours=6)),
        ("not_logged_in", 9, "Claude Code ist nicht angemeldet", timedelta(hours=6)),
    ],
)
async def test_analyse_job_without_answer_is_requeued_with_backoff(
    fake_repositories: MagicMock,
    claude_settings: Settings,
    make_job: JobFactory,
    sample_raw_audit: RawAudit,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    attempts: int,
    reason: str,
    wait: timedelta,
) -> None:
    """With the fake CLI: the job waits with a German reason, the audit stays awaiting_analysis."""

    def write(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(sample_raw_audit.model_dump_json(), encoding="utf-8")
        return local_file

    fake_repositories.download.side_effect = write
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    quick = claude_settings.model_copy(update={"analysis_timeout_seconds": 1})
    job = make_job(JobKind.ANALYSE, attempts=attempts)

    before = datetime.now(UTC)
    await worker.process_job(job, fake_repositories, quick)
    after = datetime.now(UTC)

    fake_repositories.requeue_job.assert_called_once()
    assert fake_repositories.requeue_job.call_args.args == (job.id, reason)
    run_after = fake_repositories.requeue_job.call_args.kwargs["run_after"]
    assert before + wait <= run_after <= after + wait
    fake_repositories.set_audit_status.assert_not_called()
    fake_repositories.finish_job.assert_not_called()
    fake_repositories.save_findings.assert_not_called()


async def test_analyse_job_with_claude_code_end_to_end(
    fake_repositories: MagicMock,
    claude_settings: Settings,
    make_job: JobFactory,
    sample_raw_audit: RawAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fake CLI writes valid files: findings saved, report queued, job done."""

    def write(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(sample_raw_audit.model_dump_json(), encoding="utf-8")
        return local_file

    fake_repositories.download.side_effect = write
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    job = make_job(JobKind.ANALYSE)

    await worker.process_job(job, fake_repositories, claude_settings)

    saved = fake_repositories.save_findings.call_args.args[0]
    assert [finding.check_id for finding in saved] == ["a1"]
    fake_repositories.enqueue.assert_called_once_with(JobKind.REPORT, job.customer_id, job.audit_id)
    fake_repositories.finish_job.assert_called_once_with(job.id, JobStatus.DONE)


async def test_requeue_failure_of_an_analyse_job_is_only_logged(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Supabase is down the waiting job cannot be stored; the loop goes on."""
    failing = AsyncMock(side_effect=AnalysisUnavailableError("Claude Code ist nicht angemeldet"))
    monkeypatch.setattr(worker, "run_job", failing)
    fake_repositories.requeue_job.side_effect = StorageError("offline")

    await worker.process_job(make_job(JobKind.ANALYSE), fake_repositories, settings)

    fake_repositories.finish_job.assert_not_called()


async def test_score_stores_potential_and_uplift(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    raw_audit_full: RawAudit,
    report_check_results: list[CheckResult],
    sample_customer: Customer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scoring with the real catalogue stores potential points and uplift on every result."""
    monkeypatch.setattr(
        "radarauditor.scoring.evaluator.evaluate", MagicMock(return_value=report_check_results)
    )
    keyword = Keyword(
        id=uuid4(),
        customer_id=sample_customer.id,
        phrase="Dachdecker",
        city="Münster",
        monthly_volume=1000,
        is_primary=True,
    )
    fake_repositories.keywords.return_value = [keyword]
    fake_repositories.rankings.return_value = [
        Ranking(
            id=uuid4(),
            audit_id=uuid4(),
            keyword_id=keyword.id,
            subject="customer",
            organic_position=8,
            local_pack_position=None,
            grid_point=None,
            measured_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    ]

    def write(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(raw_audit_full.model_dump_json(), encoding="utf-8")
        return local_file

    fake_repositories.download.side_effect = write
    real_catalogue = settings.model_copy(update={"catalogue_path": CATALOGUE_PATH})

    await worker.run_job(make_job(JobKind.SCORE), fake_repositories, real_catalogue)

    saved = fake_repositories.save_check_results.call_args.args[0]
    assert all(result.potential_points is not None for result in saved)
    assert all(result.uplift_low is not None and result.uplift_high is not None for result in saved)
    open_results = [result for result in saved if result.outcome.value in {"failed", "partial"}]
    assert sum(result.potential_points or 0 for result in open_results) > 0
    assert sum(result.uplift_high or 0 for result in saved) > 0


async def test_serve_runs_claimed_job_then_stops(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claimed job is processed; the loop ends once the stop event is set."""
    stop = asyncio.Event()
    job = make_job(JobKind.COLLECT)
    claims = iter([job])
    monkeypatch.setattr(worker, "claim_next", lambda repositories, paused: next(claims, None))

    async def process(job_to_run: Job, repositories: MagicMock, current: Settings) -> None:
        stop.set()

    process_job = AsyncMock(side_effect=process)
    monkeypatch.setattr(worker, "process_job", process_job)

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    process_job.assert_awaited_once_with(job, fake_repositories, settings)


async def test_serve_requeues_jobs_left_running_before_claiming(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    """Jobs a restart cut off go back into the queue before the first claim."""
    stop = asyncio.Event()
    order: list[str] = []
    fake_repositories.requeue_running_jobs.side_effect = lambda error: order.append("requeue") or 1

    def claim(kind: JobKind, limit: int) -> list[Job]:
        order.append("claim")
        stop.set()
        return []

    fake_repositories.claim_jobs.side_effect = claim

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    fake_repositories.requeue_running_jobs.assert_called_once_with(worker.INTERRUPTED_ERROR)
    assert order[0] == "requeue"


async def test_serve_starts_when_requeueing_interrupted_jobs_fails(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    """A failing requeue is logged; the worker still claims jobs."""
    stop = asyncio.Event()
    fake_repositories.requeue_running_jobs.side_effect = StorageError("offline")

    def claim(kind: JobKind, limit: int) -> list[Job]:
        stop.set()
        return []

    fake_repositories.claim_jobs.side_effect = claim

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    fake_repositories.claim_jobs.assert_called()


async def test_serve_keeps_polling_after_claim_errors(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    """A failing claim is logged and retried after the poll interval."""
    stop = asyncio.Event()
    attempts: list[int] = []

    def failing_claim(kind: JobKind, limit: int) -> list[Job]:
        attempts.append(1)
        if len(attempts) >= 2:
            stop.set()
        raise StorageError("offline")

    fake_repositories.claim_jobs.side_effect = failing_claim

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    assert len(attempts) == 2


async def test_serve_delivers_events_when_idle(
    fake_repositories: MagicMock, environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an endpoint configured, idle polls deliver outbox events."""
    monkeypatch.setenv("EVENT_ENDPOINT", "https://crm.example/hooks")
    settings = load_settings()
    stop = asyncio.Event()

    async def deliver(repositories: MagicMock, endpoint: str) -> int:
        stop.set()
        return 2

    deliver_pending = AsyncMock(side_effect=deliver)
    monkeypatch.setattr("radarauditor.integrations.events.deliver_pending", deliver_pending)

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    deliver_pending.assert_awaited_once_with(fake_repositories, "https://crm.example/hooks")


async def test_serve_survives_event_delivery_errors(
    fake_repositories: MagicMock, environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing outbox read does not stop the worker."""
    monkeypatch.setenv("EVENT_ENDPOINT", "https://crm.example/hooks")
    settings = load_settings()
    stop = asyncio.Event()

    async def deliver(repositories: MagicMock, endpoint: str) -> int:
        stop.set()
        raise StorageError("offline")

    monkeypatch.setattr(
        "radarauditor.integrations.events.deliver_pending", AsyncMock(side_effect=deliver)
    )

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)


async def test_serve_handles_document_requests_when_idle(
    fake_repositories: MagicMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each idle poll issues the documents the app requested."""
    stop = asyncio.Event()

    def issue(repositories: MagicMock, current: Settings) -> int:
        stop.set()
        return 1

    documents = MagicMock(side_effect=issue)
    monkeypatch.setattr("radarauditor.documents.requests.process_pending_requests", documents)

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    documents.assert_called_once_with(fake_repositories, settings)


def test_document_request_errors_are_logged_not_raised(
    fake_repositories: MagicMock,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing request listing does not reach the loop."""
    monkeypatch.setattr(
        "radarauditor.documents.requests.process_pending_requests",
        MagicMock(side_effect=StorageError("offline")),
    )

    worker.run_document_requests(fake_repositories, settings)

    assert "Document requests could not be processed: offline" in caplog.text


def test_document_requests_with_real_modules_and_nothing_pending(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    """With no requests nothing is written."""
    worker.run_document_requests(fake_repositories, settings)

    fake_repositories.set_document_data.assert_not_called()
    fake_repositories.set_setting.assert_not_called()


async def test_run_forever_stops_on_sigterm(
    fake_repositories: MagicMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGTERM ends the loop gracefully and removes the handlers."""
    monkeypatch.setattr(worker, "Repositories", MagicMock(return_value=fake_repositories))

    def claim_and_terminate(kind: JobKind, limit: int) -> list[Job]:
        os.kill(os.getpid(), signal.SIGTERM)
        return []

    fake_repositories.claim_jobs.side_effect = claim_and_terminate

    await asyncio.wait_for(worker.run_forever(settings), timeout=2)

    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


async def test_beat_marks_the_alive_file_after_a_stored_heartbeat(
    fake_repositories: MagicMock, tmp_path: Path
) -> None:
    """The container health check sees a fresh file only while heartbeats reach the database."""
    alive_file = tmp_path / "worker-alive"
    probe = CapabilityProbe(lambda: AVAILABLE)
    fake_repositories.set_setting.side_effect = StorageError("offline")

    await worker.beat(fake_repositories, probe, alive_file)
    assert not alive_file.exists()

    fake_repositories.set_setting.side_effect = None
    await worker.beat(fake_repositories, probe, alive_file)
    assert alive_file.is_file()


async def test_serve_keeps_beating_while_a_job_runs(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long job does not let the heartbeat go stale, so app and health check stay green."""
    stop = asyncio.Event()
    claims = iter([make_job(JobKind.ANALYSE)])
    monkeypatch.setattr(worker, "claim_next", lambda repositories, paused: next(claims, None))

    async def long_job(job: Job, repositories: MagicMock, current: Settings) -> None:
        beats_at_start = fake_repositories.set_setting.call_count
        while fake_repositories.set_setting.call_count < beats_at_start + 3:
            await asyncio.sleep(0.005)
        stop.set()

    monkeypatch.setattr(worker, "process_job", long_job)

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    assert settings.worker_alive_file.is_file()


async def test_other_error_of_an_analyse_job_waits_instead_of_failing(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected error after several backoffs still re-queues; an analyse job never fails."""
    monkeypatch.setattr(worker, "run_job", AsyncMock(side_effect=StorageError("timeout")))
    job = make_job(JobKind.ANALYSE, attempts=7)

    await worker.process_job(job, fake_repositories, settings)

    fake_repositories.requeue_job.assert_called_once()
    assert fake_repositories.requeue_job.call_args.kwargs["run_after"] is not None
    fake_repositories.finish_job.assert_not_called()
    fake_repositories.set_audit_status.assert_not_called()


@pytest.mark.parametrize(
    ("own", "expected"),
    [
        (["b3", "k1"], frozenset({"b3", "k1"})),
        ([], frozenset()),
    ],
)
async def test_score_uses_the_checks_selected_for_the_audit(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_audit: Audit,
    sample_raw_audit: RawAudit,
    sample_check_results: list[CheckResult],
    monkeypatch: pytest.MonkeyPatch,
    own: list[str],
    expected: frozenset[str],
) -> None:
    """Only the audit's own selection counts; an empty one runs every check."""
    fake_repositories.audit.return_value = sample_audit.model_copy(update={"disabled_checks": own})
    evaluate = MagicMock(return_value=sample_check_results)
    monkeypatch.setattr("radarauditor.scoring.catalog.load_catalogue", MagicMock())
    monkeypatch.setattr("radarauditor.scoring.evaluator.evaluate", evaluate)
    monkeypatch.setattr("radarauditor.scoring.evaluator.total_score", MagicMock())
    monkeypatch.setattr(
        "radarauditor.scoring.potential.with_potential", lambda results, *_: results
    )

    def write(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(sample_raw_audit.model_dump_json(), encoding="utf-8")
        return local_file

    fake_repositories.download.side_effect = write
    await worker.run_job(make_job(JobKind.SCORE), fake_repositories, settings)

    assert evaluate.call_args.kwargs["disabled_checks"] == expected


@pytest.mark.parametrize(
    ("own", "expected"),
    [
        (["b3", "k1", "b3"], frozenset({"b3", "k1"})),
        ([], frozenset()),
    ],
)
def test_disabled_checks_of_returns_the_audit_selection(
    sample_audit: Audit, own: list[str], expected: frozenset[str]
) -> None:
    audit = sample_audit.model_copy(update={"disabled_checks": own})

    assert worker.disabled_checks_of(audit) == expected


async def test_missing_city_fails_the_audit_at_once(
    fake_repositories: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A business without a findable city is not retried; the audit shows the German message."""
    from radarauditor.collectors import identify
    from radarauditor.errors import IdentificationError

    async def no_city(*args: object, **kwargs: object) -> RawAudit:
        raise IdentificationError(identify.CITY_NOT_FOUND)

    monkeypatch.setattr("radarauditor.collectors.run.collect_audit", no_city)
    job = make_job(JobKind.COLLECT, attempts=1)

    await worker.process_job(job, fake_repositories, settings)

    fake_repositories.requeue_job.assert_not_called()
    fake_repositories.finish_job.assert_called_once_with(
        job.id, JobStatus.FAILED, identify.CITY_NOT_FOUND
    )
    fake_repositories.set_audit_status.assert_called_with(
        job.audit_id, AuditStatus.FAILED, identify.CITY_NOT_FOUND
    )


async def test_the_cockpit_assistant_runs_beside_the_jobs(
    fake_repositories: MagicMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request typed in the Cockpit does not wait for a long audit to finish."""
    stop = asyncio.Event()
    started: list[tuple[object, int]] = []
    job_started = asyncio.Event()

    async def run_assistant(desk: object, poll_seconds: int, current: asyncio.Event) -> None:
        started.append((desk, poll_seconds))
        await job_started.wait()
        stop.set()

    async def long_job(job: Job, repositories: MagicMock, current: Settings) -> None:
        job_started.set()
        await stop.wait()

    monkeypatch.setattr("radarauditor.jobs.assistant.run_assistant", run_assistant)
    monkeypatch.setattr(worker, "process_job", long_job)
    fake_repositories.claim_jobs.return_value = [MagicMock(spec=Job)]

    await asyncio.wait_for(worker.serve(fake_repositories, settings, stop), timeout=2)

    ((desk, poll_seconds),) = started
    assert type(desk).__name__ == "AssistantDesk"
    assert poll_seconds == settings.cockpit_assistant_poll_seconds


async def test_the_cockpit_assistant_can_be_switched_off(
    fake_repositories: MagicMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = asyncio.Event()
    started: list[int] = []

    async def run_assistant(desk: object, poll_seconds: int, current: asyncio.Event) -> None:
        started.append(poll_seconds)

    def claim(kind: JobKind, limit: int) -> list[Job]:
        stop.set()
        return []

    monkeypatch.setattr("radarauditor.jobs.assistant.run_assistant", run_assistant)
    fake_repositories.claim_jobs.side_effect = claim
    off = settings.model_copy(update={"cockpit_assistant_poll_seconds": 0})

    await asyncio.wait_for(worker.serve(fake_repositories, off, stop), timeout=2)

    assert started == []
