"""Tests for the deferred Google search retry: backoff, scheduling and merging.

Context: Unit tests with fake repositories and a fake browser, no network. | Input: Fixtures. |
Output: Assertions.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import CAPTCHA_PAGE, BrowserFactory, JobFactory

from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import DeferredSearch, RawAudit, SerpResult
from radarauditor.db.app_settings import SourcesSettings
from radarauditor.db.models import JobKind, JobStatus
from radarauditor.errors import SourceDisabledError
from radarauditor.jobs import serp_retry

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"
IMAGES_HTML = '<div id="search"><div data-lpage="https://bilder.example.de/a"></div></div>'


@pytest.fixture
def google_html() -> str:
    """Saved Google result page."""
    return (FIXTURES / "google_dachdecker_muenster.html").read_text(encoding="utf-8")


def _serp(phrase: str, vertical: str = "web") -> SerpResult:
    return SerpResult(
        phrase=phrase,
        organic=[(1, "old.example.de")],
        local_pack=[],
        ai_overview_present=False,
        ai_overview_mentions=[],
        people_also_ask=[],
        measured_at=NOW,
        vertical=vertical,
    )


def _deferred_raw(raw: RawAudit, searches: list[DeferredSearch]) -> RawAudit:
    return raw.model_copy(
        update={
            "serps": [_serp("Dachdecker Münster")],
            "deferred_searches": searches,
            "warnings": ["website: slow", "serps: 2 searches deferred to a retry: bot check"],
        }
    )


def _gate(allowed: int = 1000, enabled: bool = True) -> SourceGate:
    sources = SourcesSettings.model_validate({"google_search": {"enabled": enabled}})
    used: list[str] = []

    def consume(source: str, cap: int) -> bool:
        used.append(source)
        return len(used) <= allowed

    return SourceGate(sources, consume)


@pytest.mark.parametrize(
    ("attempts", "delay"),
    [(1, timedelta(hours=1)), (2, timedelta(hours=4)), (3, timedelta(hours=12))],
)
def test_next_run_follows_the_backoff(attempts: int, delay: timedelta) -> None:
    """After the first three runs the job waits 1 h, 4 h and 12 h."""
    assert serp_retry.next_run_at(attempts, NOW) == NOW + delay


def test_backoff_starts_after_fifteen_minutes_and_ends_after_four_runs() -> None:
    """The first run waits 15 min; after the fourth run there is no further step."""
    assert serp_retry.first_run_at(NOW) == NOW + timedelta(minutes=15)
    assert serp_retry.next_run_at(4, NOW) is None
    assert serp_retry.next_run_at(0, NOW) is None


def test_schedule_queues_a_deferred_job_for_the_audit(
    fake_repositories: MagicMock, make_job: JobFactory
) -> None:
    """The retry job carries audit and customer and waits for the first backoff step."""
    job = make_job(JobKind.SERP_RETRY)

    serp_retry.schedule(fake_repositories, job.customer_id, job.audit_id, NOW)

    fake_repositories.enqueue.assert_called_once_with(
        JobKind.SERP_RETRY, job.customer_id, job.audit_id, run_after=NOW + timedelta(minutes=15)
    )


def test_record_failure_requeues_for_the_next_step(
    fake_repositories: MagicMock, make_job: JobFactory
) -> None:
    """An unsuccessful second run is requeued for four hours later; the audit is untouched."""
    job = make_job(JobKind.SERP_RETRY, attempts=2)

    serp_retry.record_failure(job, fake_repositories, "still blocked", NOW)

    fake_repositories.requeue_job.assert_called_once_with(
        job.id, "still blocked", run_after=NOW + timedelta(hours=4)
    )
    fake_repositories.set_audit_status.assert_not_called()


def test_record_failure_gives_up_after_the_last_step(
    fake_repositories: MagicMock, make_job: JobFactory
) -> None:
    """The fourth unsuccessful run fails the job with the reason; the audit is untouched."""
    job = make_job(JobKind.SERP_RETRY, attempts=4)

    serp_retry.record_failure(job, fake_repositories, "still blocked", NOW)

    fake_repositories.requeue_job.assert_not_called()
    fake_repositories.finish_job.assert_called_once()
    job_id, status, reason = fake_repositories.finish_job.call_args.args
    assert (job_id, status) == (job.id, JobStatus.FAILED)
    assert reason == "Google search still blocked after 4 attempts, giving up: still blocked"
    fake_repositories.set_audit_status.assert_not_called()


async def test_search_deferred_runs_everything_and_clears_the_deferral(
    fake_browser: BrowserFactory, sample_raw_audit: RawAudit, google_html: str
) -> None:
    """Web and image searches run; results replace older ones and the deferral warning goes."""
    raw = _deferred_raw(
        sample_raw_audit,
        [
            DeferredSearch(phrase="Dachdecker Münster"),
            DeferredSearch(phrase="Dachdecker Münster", vertical="images"),
        ],
    )
    fake = fake_browser([google_html, IMAGES_HTML])

    async with fake.service as service:
        run = await serp_retry.search_deferred(raw, service, _gate())

    assert run.found == 2
    assert run.remaining == []
    assert run.reason is None
    assert [(serp.phrase, serp.vertical) for serp in run.raw.serps] == [
        ("Dachdecker Münster", "web"),
        ("Dachdecker Münster", "images"),
    ]
    assert run.raw.serps[0].organic[0] == (1, "dachdecker-hollenbeck.de")
    assert run.raw.deferred_searches == []
    assert run.raw.warnings == ["website: slow"]


async def test_search_deferred_keeps_what_google_still_blocks(
    fake_browser: BrowserFactory, sample_raw_audit: RawAudit, google_html: str
) -> None:
    """A block keeps the results so far, skips the image searches and updates the warning."""
    raw = _deferred_raw(
        sample_raw_audit,
        [
            DeferredSearch(phrase="Dachdecker Münster"),
            DeferredSearch(phrase="Flachdach Münster"),
            DeferredSearch(phrase="Dachdecker Münster", vertical="images"),
        ],
    )
    fake = fake_browser([google_html, CAPTCHA_PAGE])

    async with fake.service as service:
        run = await serp_retry.search_deferred(raw, service, _gate())

    assert run.found == 1
    assert run.remaining == [
        DeferredSearch(phrase="Flachdach Münster"),
        DeferredSearch(phrase="Dachdecker Münster", vertical="images"),
    ]
    assert run.reason == "www.google.de shows a bot check"
    assert len(fake.page.visited) == 2
    assert run.raw.deferred_searches == run.remaining
    assert run.raw.warnings == [
        "website: slow",
        "serps: 2 searches deferred to a retry: www.google.de shows a bot check",
    ]


async def test_search_deferred_leaves_searches_over_the_cap_deferred(
    fake_browser: BrowserFactory, sample_raw_audit: RawAudit, google_html: str
) -> None:
    """Searches beyond the daily cap are not run and stay deferred with the cap as reason."""
    raw = _deferred_raw(
        sample_raw_audit,
        [DeferredSearch(phrase="Dachdecker Münster"), DeferredSearch(phrase="Flachdach Münster")],
    )
    fake = fake_browser([google_html])

    async with fake.service as service:
        run = await serp_retry.search_deferred(raw, service, _gate(allowed=1))

    assert run.found == 1
    assert run.remaining == [DeferredSearch(phrase="Flachdach Münster")]
    assert run.reason == serp_retry.CAP_REASON


async def test_search_deferred_refuses_when_google_search_is_switched_off(
    fake_browser: BrowserFactory, sample_raw_audit: RawAudit
) -> None:
    """A switched-off source raises, so the job follows the backoff with that reason."""
    raw = _deferred_raw(sample_raw_audit, [DeferredSearch(phrase="Dachdecker Münster")])
    fake = fake_browser([])

    async with fake.service as service:
        with pytest.raises(SourceDisabledError):
            await serp_retry.search_deferred(raw, service, _gate(enabled=False))

    assert fake.context.opened_with == []


def test_merge_keeps_other_engines_and_verticals(sample_raw_audit: RawAudit) -> None:
    """Only results of the same engine, vertical and phrase are replaced."""
    bing = _serp("Dachdecker Münster").model_copy(update={"engine": "bing"})
    raw = sample_raw_audit.model_copy(update={"serps": [_serp("Dachdecker Münster"), bing]})
    fresh = _serp("Dachdecker Münster").model_copy(update={"organic": [(1, "new.example.de")]})

    merged = serp_retry.merge(raw, [fresh], [], None)

    assert [(serp.engine, serp.organic) for serp in merged.serps] == [
        ("bing", [(1, "old.example.de")]),
        ("google", [(1, "new.example.de")]),
    ]
