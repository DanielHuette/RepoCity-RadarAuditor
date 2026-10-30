"""Tests for the checks of area m (wirkung mit Kundenzugang).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable

import pytest

from radarauditor.collectors.snapshot import AnalyticsSnapshot, RawAudit, SearchQuery
from radarauditor.db.models import CheckOutcome
from radarauditor.errors import ScoringError
from radarauditor.scoring.checks.m_wirkung import change_percent, run

CHECKS = ["m1", "m2", "m3", "m4"]
ReviewFactory = Callable[[str, int, str | None], dict[str, object]]


def _with_access(raw: RawAudit, **updates: object) -> RawAudit:
    assert raw.access is not None
    return raw.model_copy(update={"access": raw.access.model_copy(update=updates)})


@pytest.mark.parametrize("check_id", CHECKS)
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, {})[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", CHECKS)
def test_without_access_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, {}) == (
        CheckOutcome.NOT_MEASURABLE,
        {"reason": "no_access"},
    )


def test_change_percent() -> None:
    assert change_percent(42, 35) == 20.0
    assert change_percent(1, 3) == -66.7
    assert change_percent(5, 0) is None


def test_m1_measures_all_metrics_and_their_change(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("m1", raw_audit_full, {})
    assert outcome is CheckOutcome.PASSED
    assert measured["actions"] == 123
    assert measured["actions_previous"] == 115
    assert measured["actions_change_percent"] == 7.0
    change = measured["change_percent"]
    assert isinstance(change, dict)
    assert change["CALL_CLICKS"] == 20.0
    assert change["BUSINESS_BOOKINGS"] is None


@pytest.mark.parametrize(
    ("calls", "expected"),
    [(27, CheckOutcome.PARTIAL), (26, CheckOutcome.FAILED)],
)
def test_m1_judges_the_drop_in_actions(
    raw_audit_full: RawAudit, calls: int, expected: CheckOutcome
) -> None:
    assert raw_audit_full.access is not None
    metrics = {**raw_audit_full.access.profile_metrics, "CALL_CLICKS": 0}
    metrics.update({"WEBSITE_CLICKS": 65, "BUSINESS_DIRECTION_REQUESTS": calls})
    assert run("m1", _with_access(raw_audit_full, profile_metrics=metrics), {})[0] is expected


def test_m1_without_profile_metrics(raw_audit_full: RawAudit) -> None:
    raw = _with_access(raw_audit_full, profile_metrics={}, profile_metrics_previous={})
    assert run("m1", raw, {})[1] == {"reason": "no_profile_metrics"}


def test_m2_lists_matching_queries(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("m2", raw_audit_full, {})
    assert outcome is CheckOutcome.PASSED
    assert measured["matching_queries"] == {"dachdecker münster": 31, "dachsanierung münster": 12}
    assert measured["queries_total"] == 3


def test_m2_partial_and_failed(raw_audit_full: RawAudit) -> None:
    brand_only = [
        SearchQuery(query="dachdeckerei muster", clicks=40, impressions=90, ctr=0.4, position=1),
        SearchQuery(query="dachdecker münster", clicks=0, impressions=300, ctr=0, position=14),
    ]
    raw = _with_access(raw_audit_full, search_queries=brand_only)
    assert run("m2", raw, {})[0] is CheckOutcome.PARTIAL
    assert run("m2", _with_access(raw_audit_full, search_queries=[]), {})[0] is (
        CheckOutcome.FAILED
    )


def test_m2_needs_search_console_and_phrases(raw_audit_full: RawAudit) -> None:
    assert run("m2", _with_access(raw_audit_full, search_totals={}), {})[1] == {
        "reason": "no_search_console"
    }
    no_phrases = raw_audit_full.model_copy(update={"serps": []})
    assert run("m2", no_phrases, {})[1] == {"reason": "no_search_phrases"}


@pytest.mark.parametrize(
    ("sessions", "key_events", "expected"),
    [
        (640, 23, CheckOutcome.PASSED),
        (640, 0, CheckOutcome.PARTIAL),
        (0, 0, CheckOutcome.FAILED),
    ],
)
def test_m3_judges_enquiries(
    raw_audit_full: RawAudit, sessions: int, key_events: int, expected: CheckOutcome
) -> None:
    analytics = AnalyticsSnapshot(
        property_id="1", sessions=sessions, engaged_sessions=0, key_events=key_events
    )
    outcome, measured = run("m3", _with_access(raw_audit_full, analytics=analytics), {})
    assert outcome is expected
    assert measured["key_events"] == key_events


def test_m3_without_analytics(raw_audit_full: RawAudit) -> None:
    assert run("m3", _with_access(raw_audit_full, analytics=None), {})[1] == {
        "reason": "no_analytics"
    }


@pytest.mark.parametrize(
    ("replies", "expected"),
    [
        (["2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", None], CheckOutcome.PASSED),
        (["2026-09-02", "2026-09-03", "2026-09-04", "2026-09-30", None], CheckOutcome.PARTIAL),
        (["2026-09-02", "2026-09-30", None, None, None], CheckOutcome.FAILED),
    ],
)
def test_m4_reply_share_within_seven_days(
    raw_audit_full: RawAudit,
    make_owner_review: ReviewFactory,
    replies: list[str | None],
    expected: CheckOutcome,
) -> None:
    reviews = [make_owner_review("2026-09-01", 5, reply) for reply in replies]
    outcome, measured = run("m4", _with_access(raw_audit_full, owner_reviews=reviews), {})
    assert outcome is expected
    assert measured["reviews"] == 5
    assert measured["replied"] == sum(1 for reply in replies if reply)


def test_m4_without_reviews(raw_audit_full: RawAudit) -> None:
    raw = _with_access(raw_audit_full, owner_reviews=[])
    assert run("m4", raw, {})[1] == {"reason": "no_owner_reviews"}
    outcome, measured = run("m4", raw_audit_full, {})
    assert measured["average_reply_days"] == 1.2


def test_unknown_check_raises(raw_audit_full: RawAudit) -> None:
    with pytest.raises(ScoringError):
        run("m9", raw_audit_full, {})
