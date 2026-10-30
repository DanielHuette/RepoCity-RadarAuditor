"""Retry the Google searches that were deferred because Google blocked the server browser.

Context: COLLECT keeps the deferred searches in the raw data of the audit and schedules one
serp_retry job 15 minutes later. Each run searches what is still deferred and merges what it finds
into the raw data at once. A run that is blocked again, or fails, is requeued after 1 h, 4 h and
12 h; the fourth unsuccessful run fails the job while the audit keeps its other results.
Input: RawAudit, the shared BrowserService and the job's SourceGate.
Output: The merged RawAudit with the searches still deferred; scheduling of the job.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from radarauditor.collectors import serp
from radarauditor.collectors.browser import BrowserService
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import DeferredSearch, RawAudit, SerpResult
from radarauditor.db.app_settings import Source
from radarauditor.db.models import Job, JobKind, JobStatus
from radarauditor.db.repositories import Repositories

logger = logging.getLogger(__name__)

BACKOFF = (timedelta(minutes=15), timedelta(hours=1), timedelta(hours=4), timedelta(hours=12))
CAP_REASON = "daily cap of Google searches reached"


@dataclass(frozen=True)
class RetryRun:
    """Outcome of one retry run: merged raw data, how many searches it added, what is left."""

    raw: RawAudit
    found: int
    remaining: list[DeferredSearch]
    reason: str | None


def first_run_at(now: datetime) -> datetime:
    """Return when the first retry of a fresh block may run."""
    return now + BACKOFF[0]


def next_run_at(attempts: int, now: datetime) -> datetime | None:
    """Return when the job may run again after its attempts-th run, or None when it gives up.

    Args:
        attempts: Runs so far, counted by the claim.
        now: Current time.

    Returns:
        now plus the next backoff step, or None after the last step.
    """
    if attempts < 1 or attempts >= len(BACKOFF):
        return None
    return now + BACKOFF[attempts]


def schedule(repositories: Repositories, customer_id: UUID, audit_id: UUID, now: datetime) -> Job:
    """Queue the serp_retry job of an audit for the first backoff step.

    The job carries the audit id, so the app shows "Google-Suche wird nachgeholt" while it waits.

    Raises:
        StorageError: If the job cannot be written.
    """
    run_after = first_run_at(now)
    logger.info("Google searches of audit %s deferred, retry after %s", audit_id, run_after)
    return repositories.enqueue(JobKind.SERP_RETRY, customer_id, audit_id, run_after=run_after)


def record_failure(job: Job, repositories: Repositories, message: str, now: datetime) -> None:
    """Requeue an unsuccessful retry for the next backoff step, or fail it after the last.

    The audit is left alone: it keeps every other result and the rankings found so far.

    Raises:
        StorageError: If the job cannot be updated.
    """
    run_after = next_run_at(job.attempts, now)
    if run_after is not None:
        logger.info("Retry job %s requeued for %s: %s", job.id, run_after, message)
        repositories.requeue_job(job.id, message, run_after=run_after)
        return
    reason = f"Google search still blocked after {job.attempts} attempts, giving up: {message}"
    logger.warning("Retry job %s failed: %s", job.id, reason)
    repositories.finish_job(job.id, JobStatus.FAILED, reason)


async def search_deferred(raw: RawAudit, browser: BrowserService, gate: SourceGate) -> RetryRun:
    """Search the deferred phrases within the daily cap and merge the results into the raw data.

    Web searches run first; once Google blocks, the image searches are not tried in that run.

    Args:
        raw: Raw data of the audit with deferred_searches.
        browser: Shared browser of the job.
        gate: Source switches and caps of the job.

    Returns:
        The merged raw data, the number of new results and the searches still deferred.

    Raises:
        SourceDisabledError: If Google search is switched off in the app settings.
        StorageError: If the quota counter cannot be updated.
    """
    pending = raw.deferred_searches
    granted = gate.acquire_up_to(Source.GOOGLE_SEARCH, len(pending))
    allowed, over_cap = pending[:granted], pending[granted:]
    web = [search.phrase for search in allowed if search.vertical == "web"]
    images = [search.phrase for search in allowed if search.vertical == "images"]
    web_batch = await serp.search_all(browser, web)
    results = list(web_batch.results)
    remaining = [DeferredSearch(phrase=phrase) for phrase in web_batch.deferred]
    reason = web_batch.reason
    if web_batch.deferred:
        remaining.extend(DeferredSearch(phrase=phrase, vertical="images") for phrase in images)
    else:
        image_batch = await serp.search_images(browser, images)
        results.extend(image_batch.results)
        remaining.extend(
            DeferredSearch(phrase=phrase, vertical="images") for phrase in image_batch.deferred
        )
        reason = image_batch.reason
    if over_cap:
        remaining.extend(over_cap)
        reason = reason or CAP_REASON
    return RetryRun(merge(raw, results, remaining, reason), len(results), remaining, reason)


def merge(
    raw: RawAudit, results: list[SerpResult], remaining: list[DeferredSearch], reason: str | None
) -> RawAudit:
    """Add new search results and replace the deferral warning with the current state.

    A result replaces an older one of the same engine, vertical and phrase.

    Args:
        raw: Raw data before the run.
        results: Results the run found.
        remaining: Searches still deferred.
        reason: Why they are still deferred.

    Returns:
        A new RawAudit.
    """
    new_keys = {(result.engine, result.vertical, result.phrase) for result in results}
    serps = [
        result
        for result in raw.serps
        if (result.engine, result.vertical, result.phrase) not in new_keys
    ]
    warnings = [warning for warning in raw.warnings if serp.DEFERRAL_NOTE not in warning]
    if remaining:
        warnings.append(f"serps: {serp.deferral_note(len(remaining), reason)}")
    return raw.model_copy(
        update={
            "serps": [*serps, *results],
            "deferred_searches": list(remaining),
            "warnings": warnings,
        }
    )
