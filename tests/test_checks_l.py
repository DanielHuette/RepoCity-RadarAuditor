"""Tests for the checks of area l (recht).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable

import pytest

from radarauditor.collectors.snapshot import PageSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.l_recht import run

THRESHOLDS: dict[str, dict[str, float | int | str]] = {
    "l1": {"max_clicks": 2},
    "l2": {},
    "l3": {},
    "l4": {"min_score": 80},
}
Edit = Callable[..., RawAudit]
SITE = "https://www.dachdeckerei-muster.de"


def _pages(raw: RawAudit) -> list[PageSnapshot]:
    assert raw.website is not None
    return list(raw.website.pages)


def _with_legal_page(raw: RawAudit, edit: Edit, **updates: object) -> RawAudit:
    pages = [
        page.model_copy(update=updates) if str(page.url).endswith("/impressum/") else page
        for page in _pages(raw)
    ]
    return edit(raw, pages=pages)


@pytest.mark.parametrize("check_id", ["l1", "l4"])
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, THRESHOLDS[check_id])[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, THRESHOLDS[check_id])[0] is (
        CheckOutcome.NOT_MEASURABLE
    )


def test_l1_incomplete_legal_notice_is_partial(
    raw_audit_full: RawAudit, with_website: Edit
) -> None:
    raw = _with_legal_page(
        raw_audit_full,
        with_website,
        text_excerpt="Angaben gemäß Paragraf 5",
        title="Impressum",
        tel_links=[],
        mailto_links=[],
    )
    raw = with_website(raw, footer_nap={})
    outcome, measured = run("l1", raw, THRESHOLDS["l1"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["missing_elements"] == ["postal_code", "phone_or_email", "responsible"]


def test_l1_too_deep_is_partial(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [page.model_copy(update={"internal_links": []}) for page in _pages(raw_audit_full)]
    outcome, measured = run("l1", with_website(raw_audit_full, pages=pages), THRESHOLDS["l1"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["click_depth"] is None


def test_l1_missing_legal_notice_fails(raw_audit_full: RawAudit, with_website: Edit) -> None:
    raw = with_website(raw_audit_full, legal_notice_url=None)
    assert run("l1", raw, THRESHOLDS["l1"]) == (CheckOutcome.FAILED, {"legal_notice_url": None})


def test_l1_legal_notice_not_crawled(raw_audit_full: RawAudit, with_website: Edit) -> None:
    raw = with_website(raw_audit_full, legal_notice_url=f"{SITE}/rechtliches/")
    assert run("l1", raw, THRESHOLDS["l1"])[1]["reason"] == "legal_notice_not_crawled"


def _with_page_updates(raw: RawAudit, path: str, **updates: object) -> RawAudit:
    assert raw.website is not None
    pages = [
        page.model_copy(update=updates) if str(page.url).endswith(path) else page
        for page in raw.website.pages
    ]
    return raw.model_copy(update={"website": raw.website.model_copy(update={"pages": pages})})


def test_l2_privacy_policy_names_services(raw_audit_full: RawAudit, with_website: Edit) -> None:
    outcome, measured = run("l2", raw_audit_full, {})
    assert outcome is CheckOutcome.PASSED
    assert measured["services_in_use"] == ["Google Maps"]
    assert measured["services_not_named"] == []
    assert run("l2", with_website(raw_audit_full, privacy_url=None), {})[0] is CheckOutcome.FAILED


def test_l2_partial_and_failed_when_services_are_not_named(raw_audit_full: RawAudit) -> None:
    hosts = ["maps.googleapis.com", "www.youtube-nocookie.com", "connect.facebook.net"]
    raw = _with_page_updates(raw_audit_full, "/kontakt/", third_party_hosts=hosts)
    outcome, measured = run("l2", raw, {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["services_not_named"] == ["Facebook", "YouTube"]
    silent = _with_page_updates(raw, "/datenschutz/", text_excerpt="Wir schützen Daten.")
    assert run("l2", silent, {})[0] is CheckOutcome.FAILED


def test_l2_analytics_counts_with_either_name(raw_audit_full: RawAudit) -> None:
    raw = _with_page_updates(
        raw_audit_full, "/kontakt/", third_party_hosts=["www.googletagmanager.com"]
    )
    raw = _with_page_updates(raw, "/datenschutz/", text_excerpt="Wir nutzen den Tag Manager.")
    assert run("l2", raw, {})[0] is CheckOutcome.PASSED


def test_l2_privacy_page_not_crawled(raw_audit_full: RawAudit, with_website: Edit) -> None:
    raw = with_website(raw_audit_full, privacy_url="https://www.dachdeckerei-muster.de/dsgvo/")
    assert run("l2", raw, {})[1] == {
        "reason": "privacy_not_crawled",
        "privacy_url": "https://www.dachdeckerei-muster.de/dsgvo/",
    }


def test_l3_passes_without_fonts_or_tracking(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("l3", raw_audit_full, {})
    assert outcome is CheckOutcome.PASSED
    assert measured["font_hosts"] == []
    assert measured["consent_hint"] is True


def test_l3_google_fonts_fail(raw_audit_full: RawAudit) -> None:
    raw = _with_page_updates(raw_audit_full, "/ratgeber/", third_party_hosts=["fonts.gstatic.com"])
    outcome, measured = run("l3", raw, {})
    assert outcome is CheckOutcome.FAILED
    assert measured["font_hosts"] == ["fonts.gstatic.com"]


def test_l3_tracking_needs_consent_hint(raw_audit_full: RawAudit) -> None:
    raw = _with_page_updates(
        raw_audit_full, "/ratgeber/", third_party_hosts=["www.google-analytics.com"]
    )
    assert run("l3", raw, {})[0] is CheckOutcome.PASSED
    without_hint = _with_page_updates(raw, "/datenschutz/", text_excerpt="Datenschutz.")
    outcome, measured = run("l3", without_hint, {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["tracking_hosts"] == ["www.google-analytics.com"]
    assert measured["consent_hint"] is False


@pytest.mark.parametrize(
    ("score", "expected"), [(64, CheckOutcome.PARTIAL), (63, CheckOutcome.FAILED)]
)
def test_l4_accessibility_tolerance(
    raw_audit_full: RawAudit, score: int, expected: CheckOutcome
) -> None:
    assert raw_audit_full.pagespeed is not None
    speed = raw_audit_full.pagespeed.model_copy(update={"accessibility": score})
    raw = raw_audit_full.model_copy(update={"pagespeed": speed})
    outcome, measured = run("l4", raw, THRESHOLDS["l4"])
    assert outcome is expected
    assert measured == {"accessibility_score": score, "min_score": 80.0}
