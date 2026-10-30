"""Tests for the checks of area a (technik).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable
from typing import Any

import pytest

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.errors import ScoringError
from radarauditor.scoring.checks.a_technik import run

THRESHOLDS: dict[str, dict[str, float | int | str]] = {
    "a1": {},
    "a2": {"lcp_ms_max": 2500, "cls_max": 0.1, "inp_ms_max": 200, "mobile_score_min": 70},
    "a3": {},
    "a4": {},
    "a5": {"max_broken": 0, "max_redirect_hops": 2},
    "a6": {},
    "a7": {"max_image_kb": 300, "max_missing_alt": 0},
    "a8": {},
    "a9": {},
    "a10": {},
}
Edit = Callable[..., RawAudit]


def _speed(raw: RawAudit, **updates: Any) -> RawAudit:
    assert raw.pagespeed is not None
    return raw.model_copy(update={"pagespeed": raw.pagespeed.model_copy(update=updates)})


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    outcome, _ = run(check_id, raw_audit_full, THRESHOLDS[check_id])
    assert outcome is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    outcome, measured = run(check_id, raw_audit_minimal, THRESHOLDS[check_id])
    assert outcome is CheckOutcome.NOT_MEASURABLE
    assert measured["reason"] in {"no_website", "no_pagespeed"}


def test_unknown_check_raises(raw_audit_full: RawAudit) -> None:
    with pytest.raises(ScoringError):
        run("a99", raw_audit_full, {})


def test_missing_threshold_raises(raw_audit_full: RawAudit) -> None:
    with pytest.raises(ScoringError, match="lcp_ms_max"):
        run("a2", raw_audit_full, {})


def test_a1_partial_and_failed(raw_audit_full: RawAudit, with_website: Edit) -> None:
    partial = with_website(raw_audit_full, redirects_to_single_host=False)
    assert run("a1", partial, {})[0] is CheckOutcome.PARTIAL
    failed = with_website(raw_audit_full, redirects_to_single_host=False, uses_https=False)
    assert run("a1", failed, {})[0] is CheckOutcome.FAILED


def test_a2_slow_lcp_within_tolerance_is_partial(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("a2", _speed(raw_audit_full, lcp_ms=2900), THRESHOLDS["a2"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["lcp_ms"] == 2900
    assert measured["lcp_ms_max"] == 2500


def test_a2_one_metric_far_off_among_good_ones_is_partial(raw_audit_full: RawAudit) -> None:
    outcome, _ = run("a2", _speed(raw_audit_full, lcp_ms=6000), THRESHOLDS["a2"])
    assert outcome is CheckOutcome.PARTIAL


def test_a2_all_metrics_far_off_fail(raw_audit_full: RawAudit) -> None:
    slow = _speed(raw_audit_full, lcp_ms=6000, cls=0.4, inp_ms=600, mobile_performance=30)
    assert run("a2", slow, THRESHOLDS["a2"])[0] is CheckOutcome.FAILED


def test_a2_missing_inp_is_ignored(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("a2", _speed(raw_audit_full, inp_ms=None), THRESHOLDS["a2"])
    assert outcome is CheckOutcome.PASSED
    assert measured["inp_ms"] is None


def _without_viewport(raw: RawAudit, count: int) -> RawAudit:
    assert raw.website is not None
    pages = [
        page.model_copy(update={"has_viewport_meta": False}) if index < count else page
        for index, page in enumerate(raw.website.pages)
    ]
    return raw.model_copy(update={"website": raw.website.model_copy(update={"pages": pages})})


def test_a3_missing_viewport_on_some_pages_is_partial(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("a3", _without_viewport(raw_audit_full, 2), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["pages_without_viewport_count"] == 2
    assert measured["mobile_performance"] == 85


def test_a3_no_viewport_and_poor_mobile_score_fails(raw_audit_full: RawAudit) -> None:
    raw = _speed(_without_viewport(raw_audit_full, 99), mobile_performance=20)
    assert run("a3", raw, {})[0] is CheckOutcome.FAILED


def test_a3_poor_mobile_score_with_viewport_is_partial(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("a3", _speed(raw_audit_full, mobile_performance=20), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["mobile_score_min"] == 50


def test_a3_catalogue_minimum_overrides_default(raw_audit_full: RawAudit) -> None:
    raw = _speed(raw_audit_full, mobile_performance=60)
    assert run("a3", raw, {"mobile_score_min": 90})[0] is CheckOutcome.PARTIAL


def test_a3_without_pagespeed_judges_viewport_only(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("a3", raw_audit_full.model_copy(update={"pagespeed": None}), {})
    assert outcome is CheckOutcome.PASSED
    assert measured["mobile_performance"] is None


def test_a4_robots_blocking_everything_fails(raw_audit_full: RawAudit, with_website: Edit) -> None:
    blocked = with_website(raw_audit_full, robots_txt="User-agent: *\nDisallow: /\n")
    outcome, measured = run("a4", blocked, {})
    assert outcome is CheckOutcome.FAILED
    assert measured["blocks_google"] is True


def test_a4_without_sitemap_is_partial(raw_audit_full: RawAudit, with_website: Edit) -> None:
    assert run("a4", with_website(raw_audit_full, sitemap_urls=[]), {})[0] is CheckOutcome.PARTIAL


def test_a5_linked_error_page_fails_threshold(raw_audit_full: RawAudit, with_website: Edit) -> None:
    assert raw_audit_full.website is not None
    pages = list(raw_audit_full.website.pages)
    pages[1] = pages[1].model_copy(update={"status_code": 500})
    outcome, measured = run("a5", with_website(raw_audit_full, pages=pages), THRESHOLDS["a5"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["broken_count"] == 1
    assert measured["broken_urls"] == [str(pages[1].url)]


def test_a5_mostly_broken_fails(raw_audit_full: RawAudit, with_website: Edit) -> None:
    errors = [f"timeout {index}" for index in range(20)]
    outcome, _ = run("a5", with_website(raw_audit_full, crawl_errors=errors), THRESHOLDS["a5"])
    assert outcome is CheckOutcome.FAILED


def test_a6_missing_canonical_on_most_pages_fails(
    raw_audit_full: RawAudit, with_website: Edit
) -> None:
    assert raw_audit_full.website is not None
    pages = [page.model_copy(update={"canonical": None}) for page in raw_audit_full.website.pages]
    outcome, measured = run("a6", with_website(raw_audit_full, pages=pages), {})
    assert outcome is CheckOutcome.FAILED
    assert measured["pages_missing_count"] == measured["pages_total"]


def test_a7_one_page_with_missing_alt_is_partial(
    raw_audit_full: RawAudit, with_website: Edit
) -> None:
    assert raw_audit_full.website is not None
    pages = list(raw_audit_full.website.pages)
    pages[0] = pages[0].model_copy(update={"images_without_alt": 3, "oversized_images": 1})
    outcome, measured = run("a7", with_website(raw_audit_full, pages=pages), THRESHOLDS["a7"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["images_without_alt"] == 3
    assert measured["oversized_images"] == 1


def test_a8_script_only_pages_fail(raw_audit_full: RawAudit, with_website: Edit) -> None:
    assert raw_audit_full.website is not None
    pages = [
        page.model_copy(update={"word_count": 0, "headings": []})
        for page in raw_audit_full.website.pages
    ]
    assert run("a8", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.FAILED


def test_a9_english_lang_fails(raw_audit_full: RawAudit, with_website: Edit) -> None:
    assert raw_audit_full.website is not None
    pages = [page.model_copy(update={"lang": "en"}) for page in raw_audit_full.website.pages]
    assert run("a9", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.FAILED


def test_a10_open_admin_fails_and_http_links_partial(
    raw_audit_full: RawAudit, with_website: Edit, make_page: Callable[..., Any]
) -> None:
    assert raw_audit_full.website is not None
    pages = list(raw_audit_full.website.pages)
    admin = make_page(url="https://www.dachdeckerei-muster.de/wp-admin/")
    outcome, measured = run("a10", with_website(raw_audit_full, pages=[*pages, admin]), {})
    assert outcome is CheckOutcome.FAILED
    assert measured["open_admin_pages"] == ["https://www.dachdeckerei-muster.de/wp-admin/"]
    insecure = make_page(internal_links=["http://www.dachdeckerei-muster.de/kontakt/"])
    outcome, measured = run("a10", with_website(raw_audit_full, pages=[*pages, insecure]), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["insecure_link_count"] == 1
