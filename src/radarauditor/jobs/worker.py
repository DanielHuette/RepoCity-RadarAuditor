"""Poll the queue and run the audit, serp_retry, optimize and apply jobs.

Context: Long-running process in the container; one job at a time; failures are recorded on the job
and the audit. A serp_retry job follows its own backoff and never fails the audit. An analyse job
without an answer from Claude Code is re-queued with its own backoff and the audit keeps waiting.
A background pulse writes the heartbeat every poll interval, also while a long job runs, and touches
the alive file for the container health check; while it reports analysis_available false, no new
collect jobs are claimed (the other kinds go on). Every job starts by reading the app settings
(source switches and caps). While the queue is empty it first issues the documents the app
requested, delivers events, and then lets Claude Code read one of the mails the CRM queued (only
while the heartbeat says mails can be read; after a problem only once Claude Code was checked again,
which happens at once when it lost its login or its subscription is used up). An audit with
purpose monthly (queued by the CRM for a subscription) that has an earlier reported audit gets the
before-after report, every other audit the sales report.
Before every claim a waiting customer search runs (prospects/search.py), so it waits at most for
the job that is running. Document requests run in a thread, because the review reply drafts let
Claude Code write for minutes and the heartbeat must go on meanwhile. A reported monthly audit
queues the monthly service (public.queue_monthly_service): one optimize job that drafts replies to
new reviews and profile care for the order of the customer's subscription.
An audit carries its own selection of checks (audits.disabled_checks), the only one that counts;
an empty selection runs every check. A collect job whose business has no findable city fails its
audit at once with a German message instead of being retried. Beside the jobs, the requests of the
Cockpit assistant are answered one after the other (jobs/assistant.py), unless
COCKPIT_ASSISTANT_POLL_SECONDS is 0.
Input: Settings.
Output: Nothing; side effects in Supabase.
"""

import asyncio
import contextlib
import functools
import logging
import signal
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID

from radarauditor.collectors.browser import BrowserService
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import RawAudit
from radarauditor.config import Settings
from radarauditor.db.app_settings import (
    WORKER_SETTINGS_KEYS,
    WorkerCapabilities,
    WorkerSettings,
    parse_worker_settings,
)
from radarauditor.db.models import (
    Audit,
    AuditKind,
    AuditPurpose,
    AuditStatus,
    Job,
    JobKind,
    JobStatus,
)
from radarauditor.db.repositories import Repositories
from radarauditor.errors import (
    AnalysisUnavailableError,
    IdentificationError,
    RadarError,
    SearchBlockedError,
)
from radarauditor.jobs import serp_retry
from radarauditor.jobs.analysis import retry_at as analysis_retry_at
from radarauditor.jobs.heartbeat import (
    CapabilityProbe,
    build_heartbeat,
    detect_capabilities,
    touch_alive_file,
    with_mail_problem,
    write_heartbeat,
)

if TYPE_CHECKING:
    from radarauditor.jobs.mail_triage import MailReader, ReadOutcome

logger = logging.getLogger(__name__)

POLL_SECONDS = 15
MAX_ATTEMPTS = 3
PRIORITY = (
    JobKind.COLLECT,
    JobKind.SCORE,
    JobKind.ANALYSE,
    JobKind.REPORT,
    JobKind.SERP_RETRY,
    JobKind.APPLY,
    JobKind.OPTIMIZE,
)
# Without Claude Code an audit could not be analysed, so none is started.
NEEDS_ANALYSIS = frozenset({JobKind.COLLECT})
RESCORED_STATUSES = frozenset(
    {AuditStatus.SCORED, AuditStatus.AWAITING_ANALYSIS, AuditStatus.ANALYSED, AuditStatus.REPORTED}
)
# Only a failure of these marks the audit failed; optimize and apply belong to an order.
AUDIT_PIPELINE = frozenset({JobKind.COLLECT, JobKind.SCORE, JobKind.ANALYSE, JobKind.REPORT})
RAW_BUCKET = "raw"
REPORTS_BUCKET = "reports"
RAW_FILE = "raw.json"
AUDIT_REPORTED_EVENT = "audit.reported"
INTERRUPTED_ERROR = "Der Server wurde während des Auftrags neu gestartet"


async def run_forever(settings: Settings) -> None:
    """Claim and run jobs until stopped."""
    repositories = Repositories(settings)
    stop = asyncio.Event()
    installed = _install_stop_handlers(stop)
    logger.info("Worker started, polling every %d seconds", POLL_SECONDS)
    try:
        await serve(repositories, settings, stop)
    finally:
        _remove_stop_handlers(installed)
    logger.info("Worker stopped")


def _install_stop_handlers(stop: asyncio.Event) -> list[signal.Signals]:
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(stop_signal, stop.set)
        except NotImplementedError:
            # Windows event loops have no add_signal_handler; fall back to the plain handler.
            signal.signal(stop_signal, lambda *_: loop.call_soon_threadsafe(stop.set))
            continue
        installed.append(stop_signal)
    return installed


def _remove_stop_handlers(installed: list[signal.Signals]) -> None:
    loop = asyncio.get_running_loop()
    for stop_signal in installed:
        loop.remove_signal_handler(stop_signal)


async def serve(repositories: Repositories, settings: Settings, stop: asyncio.Event) -> None:
    """Run jobs until the stop event is set; the current job always finishes first.

    Args:
        repositories: Database access.
        settings: Runtime settings.
        stop: Set by a signal handler or a test to end the loop.
    """
    _requeue_interrupted(repositories)
    probe = CapabilityProbe(functools.partial(detect_capabilities, settings))
    pulse = Pulse(repositories, probe, settings.worker_alive_file)
    await pulse.beat()
    background = [asyncio.create_task(pulse.run(stop))]
    if settings.cockpit_assistant_poll_seconds > 0:
        background.append(asyncio.create_task(_answer_assistant(repositories, settings, stop)))
    try:
        await _claim_and_run(repositories, settings, stop, pulse)
    finally:
        # A request being answered in its thread finishes there; asyncio.run waits for it.
        for task in background:
            task.cancel()
        for task in background:
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _answer_assistant(
    repositories: Repositories, settings: Settings, stop: asyncio.Event
) -> None:
    """Answer the Cockpit assistant beside the jobs, so a request never waits for an audit."""
    from radarauditor.jobs import assistant

    desk = assistant.AssistantDesk(repositories, settings)
    await assistant.run_assistant(desk, settings.cockpit_assistant_poll_seconds, stop)


def _requeue_interrupted(repositories: Repositories) -> None:
    """Put jobs a previous run of the container left running back into the queue."""
    try:
        count = repositories.requeue_running_jobs(INTERRUPTED_ERROR)
    except RadarError as exc:
        logger.error("Interrupted jobs could not be put back into the queue: %s", exc)
        return
    if count:
        logger.warning("Put %d jobs interrupted by the last stop back into the queue", count)


async def _claim_and_run(
    repositories: Repositories, settings: Settings, stop: asyncio.Event, pulse: "Pulse"
) -> None:
    from radarauditor.jobs.mail_triage import MailReader

    mails = MailReader(repositories, settings)
    paused: frozenset[JobKind] = frozenset()
    while not stop.is_set():
        await _run_customer_search(repositories, settings)
        now_paused = paused_kinds(pulse.capabilities)
        if now_paused != paused:
            _log_pause(now_paused, pulse.capabilities)
            paused = now_paused
        try:
            job = claim_next(repositories, paused)
        except RadarError as exc:
            logger.error("Claiming jobs failed: %s", exc)
            job = None
        if job is not None:
            await process_job(job, repositories, settings)
            continue
        await asyncio.to_thread(run_document_requests, repositories, settings)
        await _deliver_events(repositories, settings)
        await _read_mail(mails, settings, pulse)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)


async def _run_customer_search(repositories: Repositories, settings: Settings) -> None:
    """Run the oldest waiting customer search; it records its own failures."""
    from radarauditor.prospects import search

    await search.run_next_search(repositories, settings)


class MailProblem(NamedTuple):
    """Why mail reading pauses, and since when."""

    reason: str
    since: datetime


class Pulse:
    """Writes the heartbeat every poll interval, independent of how long the current job takes."""

    def __init__(
        self, repositories: Repositories, probe: CapabilityProbe, alive_file: Path | None
    ) -> None:
        """Remember where to write; capabilities stay unknown until the first beat."""
        self._repositories = repositories
        self._probe = probe
        self._alive_file = alive_file
        self.capabilities: WorkerCapabilities | None = None
        self.mail_problem: MailProblem | None = None

    async def beat(self) -> None:
        """Write one heartbeat with the last mail problem and keep the capabilities it detected."""
        problem = self.mail_problem.reason if self.mail_problem is not None else None
        self.capabilities = await beat(self._repositories, self._probe, self._alive_file, problem)

    def may_read_mail(self, enabled: bool) -> bool:
        """Whether mails may be read: switched on, readable, no problem since the last check."""
        if not enabled or self.capabilities is None or not self.capabilities.mail_reading_available:
            return False
        if self.mail_problem is None:
            return True
        detected = self._probe.detected_at
        return detected is not None and detected > self.mail_problem.since

    def record_mail(self, outcome: "ReadOutcome", now: datetime) -> None:
        """Keep the problem of a mail poll; have Claude Code checked again when it was missing."""
        self.mail_problem = None if outcome.problem is None else MailProblem(outcome.problem, now)
        if outcome.claude_unavailable:
            self._probe.invalidate()

    async def run(self, stop: asyncio.Event) -> None:
        """Beat after every poll interval until the stop event is set."""
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
            if not stop.is_set():
                await self.beat()


async def beat(
    repositories: Repositories,
    probe: CapabilityProbe,
    alive_file: Path | None = None,
    mail_problem: str | None = None,
) -> WorkerCapabilities | None:
    """Write the heartbeat and, once it is stored, touch the alive file; failures are logged.

    Args:
        repositories: Database access.
        probe: Cached capability detection.
        alive_file: File the container health check reads; None writes none.
        mail_problem: German reason why the last mail run could not read mails, or None.

    Returns:
        The detected capabilities without the mail problem, or None when detection failed.
    """
    now = datetime.now(UTC)
    try:
        detected = await asyncio.to_thread(probe.current, now)
    except (RadarError, OSError) as exc:
        logger.error("Capabilities could not be detected: %s", exc)
        return None
    try:
        write_heartbeat(
            repositories, build_heartbeat(now, with_mail_problem(detected, mail_problem))
        )
    except (RadarError, OSError) as exc:
        logger.error("Heartbeat could not be written: %s", exc)
        return detected
    if alive_file is not None:
        try:
            touch_alive_file(alive_file)
        except OSError as exc:
            logger.error("Alive file %s could not be written: %s", alive_file, exc)
    return detected


def _log_pause(paused: frozenset[JobKind], capabilities: WorkerCapabilities | None) -> None:
    if paused:
        reason = capabilities.analysis_reason if capabilities is not None else None
        logger.warning("No new audits while Claude Code is unavailable: %s", reason)
    else:
        logger.info("Claude Code is available again, new audits are claimed")


def paused_kinds(capabilities: WorkerCapabilities | None) -> frozenset[JobKind]:
    """Return the job kinds not to claim now: collect while Claude Code cannot analyse.

    Args:
        capabilities: Current capabilities; None (unknown) pauses nothing.

    Returns:
        Kinds to skip when claiming.
    """
    return frozenset() if claude_code_answers(capabilities) else NEEDS_ANALYSIS


def claude_code_answers(capabilities: WorkerCapabilities | None) -> bool:
    """Whether Claude Code answered the last check; unknown capabilities count as answering."""
    return capabilities is None or capabilities.analysis_available


def read_worker_settings(repositories: Repositories) -> WorkerSettings:
    """Read the source switches with caps from table settings.

    Args:
        repositories: Database access.

    Returns:
        The validated switches; keys without a row keep their defaults.

    Raises:
        StorageError: If the rows cannot be read.
        ConfigurationError: If a row has the wrong shape.
    """
    return parse_worker_settings(repositories.settings_values(WORKER_SETTINGS_KEYS))


def claim_next(repositories: Repositories, paused: frozenset[JobKind] = frozenset()) -> Job | None:
    """Claim one job of the most urgent kind that has queued work.

    Args:
        repositories: Database access.
        paused: Kinds that are not claimed now.

    Returns:
        The claimed job, or None when the queue is empty.
    """
    for kind in PRIORITY:
        if kind in paused:
            continue
        jobs = repositories.claim_jobs(kind, 1)
        if jobs:
            return jobs[0]
    return None


async def _read_mail(mails: "MailReader", settings: Settings, pulse: Pulse) -> None:
    """Let Claude Code read one queued mail; claimed mails would only lose attempts otherwise."""
    if not pulse.may_read_mail(settings.mail_triage_batch > 0):
        return
    now = datetime.now(UTC)
    try:
        outcome = await asyncio.to_thread(mails.read_one, now)
    except RadarError as exc:
        logger.error("Reading mails failed: %s", exc)
        return
    pulse.record_mail(outcome, now)
    if outcome.problem is not None:
        logger.warning("Mail reading pauses: %s", outcome.problem)


async def _deliver_events(repositories: Repositories, settings: Settings) -> None:
    if settings.event_endpoint is None:
        return
    from radarauditor.integrations.events import deliver_pending

    try:
        delivered = await deliver_pending(repositories, str(settings.event_endpoint))
    except RadarError as exc:
        logger.error("Event delivery failed: %s", exc)
        return
    if delivered:
        logger.info("Delivered %d events", delivered)


def run_document_requests(repositories: Repositories, settings: Settings) -> None:
    """Issue the documents the app requested; errors are logged, not raised.

    The job_kind enum has no value for these, so the app leaves documents rows without a file
    instead of jobs.

    Args:
        repositories: Database access.
        settings: Runtime settings.
    """
    from radarauditor.documents.requests import process_pending_requests

    try:
        issued = process_pending_requests(repositories, settings)
    except RadarError as exc:
        logger.error("Document requests could not be processed: %s", exc)
        return
    if issued:
        logger.info("Issued %d requested documents", issued)


async def process_job(job: Job, repositories: Repositories, settings: Settings) -> None:
    """Run one job and record success, a retry or the final failure.

    Args:
        job: A claimed job; its attempts counter already includes this run.
        repositories: Database access.
        settings: Runtime settings.
    """
    logger.info("Running %s job %s (attempt %d)", job.kind.value, job.id, job.attempts)
    try:
        await run_job(job, repositories, settings)
    except SearchBlockedError as exc:
        logger.warning("Job %s: %s", job.id, exc)
        _record_failure(job, repositories, str(exc))
        return
    except AnalysisUnavailableError as exc:
        _requeue_analysis(job, repositories, str(exc))
        return
    except IdentificationError as exc:
        # Another attempt would read the same website; the German message goes to the app as is.
        logger.warning("Job %s: %s", job.id, exc)
        _fail_at_once(job, repositories, str(exc))
        return
    except Exception as exc:
        # The loop must survive any failure of a single job; the error is stored on the job.
        logger.exception("Job %s failed", job.id)
        _record_failure(job, repositories, f"{type(exc).__name__}: {exc}")
        return
    try:
        repositories.finish_job(job.id, JobStatus.DONE)
    except RadarError as exc:
        logger.error("Job %s finished but could not be marked done: %s", job.id, exc)


def _requeue_analysis(job: Job, repositories: Repositories, reason: str) -> None:
    """Put an analyse job back after its backoff; the audit stays awaiting_analysis."""
    run_after = analysis_retry_at(job.attempts, datetime.now(UTC))
    logger.warning("Analyse job %s waits until %s: %s", job.id, run_after, reason)
    try:
        repositories.requeue_job(job.id, reason, run_after=run_after)
    except RadarError as exc:
        logger.error("Analyse job %s could not be re-queued: %s", job.id, exc)


def _fail_at_once(job: Job, repositories: Repositories, message: str) -> None:
    """Fail the job and its audit without another attempt."""
    try:
        repositories.finish_job(job.id, JobStatus.FAILED, message)
        if job.audit_id is not None and job.kind in AUDIT_PIPELINE:
            repositories.set_audit_status(job.audit_id, AuditStatus.FAILED, message)
    except RadarError as exc:
        logger.error("Failure of job %s could not be recorded: %s", job.id, exc)


def _record_failure(job: Job, repositories: Repositories, message: str) -> None:
    try:
        if job.kind is JobKind.SERP_RETRY:
            serp_retry.record_failure(job, repositories, message, datetime.now(UTC))
            return
        if job.kind is JobKind.ANALYSE:
            _requeue_analysis(job, repositories, message)
            return
        if job.attempts < MAX_ATTEMPTS:
            repositories.requeue_job(job.id, message)
            return
        repositories.finish_job(job.id, JobStatus.FAILED, message)
        if job.audit_id is not None and job.kind in AUDIT_PIPELINE:
            repositories.set_audit_status(job.audit_id, AuditStatus.FAILED, message)
    except RadarError as exc:
        logger.error("Failure of job %s could not be recorded: %s", job.id, exc)


async def run_job(job: Job, repositories: Repositories, settings: Settings) -> None:
    """Dispatch one job by kind.

    COLLECT stores the raw data and rankings and enqueues SCORE; SCORE stores check results with
    potential and uplift and enqueues ANALYSE; ANALYSE writes findings and texts with headless
    Claude Code and enqueues REPORT. SERP_RETRY runs the deferred Google searches and re-scores
    the audit with what it found. OPTIMIZE drafts changes for a CRM order with headless Claude
    Code; APPLY performs the approved ones. The app settings are read first, so a switch changed in
    the app applies from the next job on.

    Raises:
        SearchBlockedError: If a SERP_RETRY run leaves searches deferred.
    """
    job_settings = read_worker_settings(repositories)
    if job.kind is JobKind.OPTIMIZE:
        from radarauditor.optimization.prepare import optimize_order

        await optimize_order(job, repositories, settings)
        return
    if job.kind is JobKind.APPLY:
        from radarauditor.optimization.apply import apply_order

        await apply_order(job, repositories, settings)
        return
    if job.kind is JobKind.ANALYSE:
        await asyncio.to_thread(_analyse, job, repositories, settings)
        return
    if job.audit_id is None:
        raise RadarError(f"job {job.id}: {job.kind.value} job has no audit id")
    if job.kind is JobKind.COLLECT:
        await _collect(job.audit_id, job.customer_id, repositories, settings, job_settings)
    elif job.kind is JobKind.SCORE:
        _score(job.audit_id, job.customer_id, repositories, settings, job_settings)
    elif job.kind is JobKind.SERP_RETRY:
        await _retry_searches(job.audit_id, job.customer_id, repositories, settings, job_settings)
    else:
        _report(job.audit_id, job.customer_id, repositories, settings)


async def _collect(
    audit_id: UUID,
    customer_id: UUID,
    repositories: Repositories,
    settings: Settings,
    job_settings: WorkerSettings,
) -> None:
    from radarauditor.collectors.run import collect_audit

    repositories.set_audit_status(audit_id, AuditStatus.COLLECTING)
    gate = SourceGate(job_settings.sources, repositories.consume_source_quota, repositories)
    raw = await collect_audit(audit_id, repositories, settings, gate)
    _store_raw(audit_id, raw, repositories)
    _save_rankings(audit_id, customer_id, raw, repositories)
    if raw.deferred_searches:
        serp_retry.schedule(repositories, customer_id, audit_id, datetime.now(UTC))
    repositories.set_audit_status(audit_id, AuditStatus.COLLECTED)
    repositories.enqueue(JobKind.SCORE, customer_id, audit_id)


def _store_raw(audit_id: UUID, raw: RawAudit, repositories: Repositories) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        raw_file = Path(scratch) / RAW_FILE
        raw_file.write_text(raw.model_dump_json(), encoding="utf-8")
        raw_path = repositories.upload(RAW_BUCKET, f"{audit_id}/{RAW_FILE}", raw_file)
    repositories.update_audit(audit_id, raw_data_path=raw_path)


def _load_raw(audit: Audit, repositories: Repositories) -> RawAudit:
    if audit.raw_data_path is None:
        raise RadarError(f"audit {audit.id} has no raw data")
    with tempfile.TemporaryDirectory() as scratch:
        raw_file = repositories.download(RAW_BUCKET, audit.raw_data_path, Path(scratch) / RAW_FILE)
        return RawAudit.model_validate_json(raw_file.read_text(encoding="utf-8"))


async def _retry_searches(
    audit_id: UUID,
    customer_id: UUID,
    repositories: Repositories,
    settings: Settings,
    job_settings: WorkerSettings,
) -> None:
    audit = repositories.audit(audit_id)
    raw = _load_raw(audit, repositories)
    if not raw.deferred_searches:
        logger.info("Audit %s has no deferred Google searches left", audit_id)
        return
    gate = SourceGate(job_settings.sources, repositories.consume_source_quota, repositories)
    async with BrowserService(settings.browser_profile_dir) as browser:
        run = await serp_retry.search_deferred(raw, browser, gate)
    if run.found:
        logger.info("Retry found %d Google results for audit %s", run.found, audit_id)
        _store_raw(audit_id, run.raw, repositories)
        _save_rankings(audit_id, customer_id, run.raw, repositories)
        # An audit that was not scored yet picks the new data up with its queued SCORE job.
        if audit.status in RESCORED_STATUSES:
            _score(audit_id, customer_id, repositories, settings, job_settings)
    if run.remaining:
        raise SearchBlockedError(
            f"{len(run.remaining)} Google searches still deferred: {run.reason or 'unknown reason'}"
        )


def _save_rankings(
    audit_id: UUID, customer_id: UUID, raw: RawAudit, repositories: Repositories
) -> None:
    from radarauditor.scoring.rankings import rankings_from_raw

    customer = repositories.customer(customer_id)
    if customer.website is not None:
        hostname = customer.website.host
    elif raw.website is not None:
        hostname = raw.website.start_url.host
    else:
        hostname = None
    place_name = raw.place.name if raw.place is not None else customer.name
    rankings = rankings_from_raw(
        audit_id,
        raw,
        repositories.keywords(customer_id),
        repositories.competitors(customer_id),
        hostname,
        place_name,
    )
    repositories.save_rankings(rankings)


def _score(
    audit_id: UUID,
    customer_id: UUID,
    repositories: Repositories,
    settings: Settings,
    job_settings: WorkerSettings,
) -> None:
    from radarauditor.scoring.catalog import load_catalogue
    from radarauditor.scoring.evaluator import evaluate, total_score
    from radarauditor.scoring.potential import with_potential

    audit = repositories.audit(audit_id)
    raw = _load_raw(audit, repositories)
    catalogue = load_catalogue(settings.catalogue_path)
    results = evaluate(
        audit_id,
        raw,
        catalogue,
        paid_enabled=job_settings.sources.dataforseo.enabled,
        with_access=audit.kind is AuditKind.WITH_ACCESS,
        disabled_checks=disabled_checks_of(audit),
    )
    gain = _forecast_gain(audit_id, customer_id, repositories, settings)
    results = with_potential(results, catalogue, gain)
    repositories.save_check_results(results)
    repositories.update_audit(audit_id, total_score=total_score(results, catalogue))
    repositories.set_audit_status(audit_id, AuditStatus.SCORED)
    repositories.enqueue(JobKind.ANALYSE, customer_id, audit_id)
    repositories.set_audit_status(audit_id, AuditStatus.AWAITING_ANALYSIS)


def disabled_checks_of(audit: Audit) -> frozenset[str]:
    """Checks switched off for an audit: its own selection only.

    The app sends the selection with every audit and stores it in audits.disabled_checks (migration
    011); an empty selection means every check runs.

    Args:
        audit: The audit being scored.

    Returns:
        Ids of the checks to skip.
    """
    return frozenset(audit.disabled_checks)


def _analyse(job: Job, repositories: Repositories, settings: Settings) -> None:
    from radarauditor.jobs.analysis import analyse_job

    analyse_job(job, repositories, settings)


def _forecast_gain(
    audit_id: UUID, customer_id: UUID, repositories: Repositories, settings: Settings
) -> tuple[float, float]:
    from radarauditor.reporting.forecast import enquiry_gain, report_forecast

    forecast = report_forecast(
        repositories.customer(customer_id).industry,
        repositories.keywords(customer_id),
        repositories.rankings(audit_id),
        settings.forecast_path,
        settings.industries_path,
    )
    return enquiry_gain(forecast)


def is_follow_up(audit: Audit, repositories: Repositories) -> bool:
    """Whether an audit gets the before-after report instead of the sales report.

    Args:
        audit: The audit being reported.
        repositories: Database access, asked for an earlier reported audit only when needed.

    Returns:
        True for a monthly audit with an earlier reported audit of the same business.
    """
    if audit.purpose is not AuditPurpose.MONTHLY:
        return False
    return repositories.previous_audit(audit) is not None


def _queue_monthly_service(audit_id: UUID, repositories: Repositories) -> None:
    """Queue the drafts of the month; a failure fails the report job, which is then retried.

    Raises:
        StorageError: If the job cannot be queued.
    """
    job_id = repositories.queue_monthly_service(audit_id)
    if job_id is None:
        logger.warning(
            "Audit %s: no monthly drafts, the CRM has no subscription order or its first "
            "optimisation is not finished yet",
            audit_id,
        )
    else:
        logger.info("Audit %s: monthly service queued as job %s", audit_id, job_id)


def _report(
    audit_id: UUID, customer_id: UUID, repositories: Repositories, settings: Settings
) -> None:
    from radarauditor.reporting.report import build_monthly_report, build_sales_report

    audit = repositories.audit(audit_id)
    if is_follow_up(audit, repositories):
        pdf = build_monthly_report(audit_id, repositories, settings)
    else:
        pdf = build_sales_report(audit_id, repositories, settings)
    report_path = repositories.upload(REPORTS_BUCKET, f"{audit_id}/{pdf.name}", pdf)
    repositories.update_audit(audit_id, report_path=report_path)
    if audit.purpose is AuditPurpose.MONTHLY:
        _queue_monthly_service(audit_id, repositories)
    repositories.set_audit_status(audit_id, AuditStatus.REPORTED)
    repositories.new_event(
        AUDIT_REPORTED_EVENT,
        {"customer_id": str(customer_id), "audit_id": str(audit_id), "report_path": report_path},
    )
