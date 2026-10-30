"""Tests for the checks of area i (links).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

import pytest

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.errors import ScoringError
from radarauditor.scoring.checks.i_links import is_social_network, run

PAID = ["i1", "i2", "i3", "i5"]


@pytest.mark.parametrize("check_id", PAID)
def test_paid_checks_not_measurable_without_paid_data(
    raw_audit_full: RawAudit, check_id: str
) -> None:
    raw = raw_audit_full.model_copy(update={"paid": None})
    assert run(check_id, raw, {}) == (CheckOutcome.NOT_MEASURABLE, {"reason": "no_paid_data"})


@pytest.mark.parametrize("check_id", PAID)
def test_paid_checks_pass_for_full_audit(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, {})[0] is CheckOutcome.PASSED


def _with_links_of(raw: RawAudit, **updates: object) -> RawAudit:
    assert raw.paid is not None and raw.paid.customer_domain is not None
    own = raw.paid.backlinks[raw.paid.customer_domain].model_copy(update=updates)
    backlinks = {**raw.paid.backlinks, own.domain: own}
    return raw.model_copy(update={"paid": raw.paid.model_copy(update={"backlinks": backlinks})})


@pytest.mark.parametrize(
    ("domains", "expected"),
    [(42, CheckOutcome.PASSED), (21, CheckOutcome.PARTIAL), (20, CheckOutcome.FAILED)],
)
def test_i1_compares_with_best_competitor(
    raw_audit_full: RawAudit, domains: int, expected: CheckOutcome
) -> None:
    outcome, measured = run("i1", _with_links_of(raw_audit_full, referring_domains=domains), {})
    assert outcome is expected
    assert measured["best_competitor"] == "dach-schmidt.de"


def test_i1_needs_own_and_competitor_profiles(raw_audit_full: RawAudit) -> None:
    assert raw_audit_full.paid is not None
    no_own = raw_audit_full.paid.model_copy(update={"customer_domain": None})
    assert run("i1", raw_audit_full.model_copy(update={"paid": no_own}), {})[1] == {
        "reason": "no_backlink_data"
    }
    alone = raw_audit_full.paid.model_copy(
        update={
            "backlinks": {
                key: value
                for key, value in raw_audit_full.paid.backlinks.items()
                if key == raw_audit_full.paid.customer_domain
            }
        }
    )
    assert run("i1", raw_audit_full.model_copy(update={"paid": alone}), {})[1] == {
        "reason": "no_competitor_backlinks"
    }


@pytest.mark.parametrize(
    ("share", "expected"),
    [(0.3, CheckOutcome.PASSED), (0.15, CheckOutcome.PARTIAL), (0.1, CheckOutcome.FAILED)],
)
def test_i2_local_share(raw_audit_full: RawAudit, share: float, expected: CheckOutcome) -> None:
    raw = _with_links_of(raw_audit_full, referring_domains_local_share=share)
    assert run("i2", raw, {})[0] is expected
    unknown = _with_links_of(raw_audit_full, referring_domains_local_share=None)
    assert run("i2", unknown, {})[1] == {"reason": "no_backlink_data"}


@pytest.mark.parametrize(
    ("share", "expected"),
    [(0.1, CheckOutcome.PASSED), (0.25, CheckOutcome.PARTIAL), (0.3, CheckOutcome.FAILED)],
)
def test_i3_spam_share(raw_audit_full: RawAudit, share: float, expected: CheckOutcome) -> None:
    raw = _with_links_of(raw_audit_full, referring_domains_spam_share=share)
    outcome, measured = run("i3", raw, {})
    assert outcome is expected
    assert measured["spam_share"] == share
    unknown = _with_links_of(raw_audit_full, referring_domains_spam_share=None)
    assert run("i3", unknown, {})[1] == {"reason": "no_backlink_data"}


@pytest.mark.parametrize(
    ("last", "expected"),
    [(20.0, CheckOutcome.PASSED), (16.0, CheckOutcome.PARTIAL), (15.9, CheckOutcome.FAILED)],
)
def test_i5_compares_last_with_first_month(
    raw_audit_full: RawAudit, last: float, expected: CheckOutcome
) -> None:
    assert raw_audit_full.paid is not None
    history = [("2025-10", 20.0), ("2026-03", 25.0), ("2026-09", last)]
    raw = raw_audit_full.model_copy(
        update={"paid": raw_audit_full.paid.model_copy(update={"historic_visibility": history})}
    )
    outcome, measured = run("i5", raw, {})
    assert outcome is expected
    assert measured["first_month"] == "2025-10"
    short = raw_audit_full.paid.model_copy(update={"historic_visibility": history[:1]})
    assert run("i5", raw_audit_full.model_copy(update={"paid": short}), {})[1] == {
        "reason": "short_history"
    }


def test_i5_reports_the_wayback_domain_age(raw_audit_full: RawAudit) -> None:
    first = raw_audit_full.collected_at.date().replace(year=raw_audit_full.collected_at.year - 10)
    raw = raw_audit_full.model_copy(update={"domain_first_capture": first})

    outcome, measured = run("i5", raw, {})

    assert outcome is CheckOutcome.PASSED
    assert measured["domain_first_capture"] == first.isoformat()
    assert measured["domain_age_years"] == 10.0


@pytest.mark.parametrize(("years_back", "expected"), [(3, "failed"), (0, "not_measurable")])
def test_i5_without_any_history_depends_on_the_domain_age(
    raw_audit_full: RawAudit, years_back: int, expected: str
) -> None:
    """A domain older than a year with no top-10 keyword all year fails; a new one is open."""
    assert raw_audit_full.paid is not None
    collected = raw_audit_full.collected_at.date()
    raw = raw_audit_full.model_copy(
        update={
            "paid": raw_audit_full.paid.model_copy(update={"historic_visibility": []}),
            "domain_first_capture": collected.replace(year=collected.year - years_back),
        }
    )

    outcome, measured = run("i5", raw, {})

    assert outcome.value == expected
    if outcome is CheckOutcome.FAILED:
        assert measured["top10_keywords"] == 0
    else:
        assert measured["reason"] == "short_history"


def test_i4_lists_foreign_brand_results(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("i4", raw_audit_full, {})
    assert outcome is CheckOutcome.NOT_MEASURABLE
    assert measured == {
        "reason": "paid_disabled",
        "mentions": ["facebook.com"],
        "mention_count": 1,
    }


def test_i4_without_brand_search(raw_audit_full: RawAudit) -> None:
    raw = raw_audit_full.model_copy(update={"serps": raw_audit_full.serps[:2]})
    assert run("i4", raw, {})[1] == {"reason": "no_brand_search"}


@pytest.mark.parametrize("check_id", ["i4", "i6"])
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, {})[0] is CheckOutcome.NOT_MEASURABLE


def _with_links(raw: RawAudit, links: list[str]) -> RawAudit:
    assert raw.website is not None
    pages = [page.model_copy(update={"external_links": links}) for page in raw.website.pages]
    return raw.model_copy(update={"website": raw.website.model_copy(update={"pages": pages})})


def test_i6_counts_partner_sites(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("i6", raw_audit_full, {})
    assert outcome is CheckOutcome.PASSED
    assert measured == {
        "partner_hosts": ["dachdecker.org", "hwk-muenster.de"],
        "partner_host_count": 2,
    }


def test_i6_ignores_social_networks_maps_and_own_domains(raw_audit_full: RawAudit) -> None:
    links = [
        "https://www.facebook.com/dachdeckereimuster",
        "https://de.linkedin.com/company/muster",
        "https://www.google.com/maps/place/Muster",
        "https://shop.dachdeckerei-muster.de/",
    ]
    outcome, measured = run("i6", _with_links(raw_audit_full, links), {})
    assert outcome is CheckOutcome.FAILED
    assert measured["partner_hosts"] == []


def test_is_social_network_matches_subdomains() -> None:
    assert is_social_network("de-de.facebook.com") is True
    assert is_social_network("notfacebook.com") is False


def test_unknown_check_raises(raw_audit_full: RawAudit) -> None:
    with pytest.raises(ScoringError):
        run("i9", raw_audit_full, {})
