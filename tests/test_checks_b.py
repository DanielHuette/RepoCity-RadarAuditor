"""Tests for the checks of area b (struktur).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable
from typing import Any

import pytest

from radarauditor.collectors.snapshot import PageSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.b_struktur import run

THRESHOLDS: dict[str, dict[str, float | int | str]] = {
    "b1": {},
    "b2": {"min_chars": 30, "max_chars": 60},
    "b3": {"min_chars": 70, "max_chars": 155},
    "b4": {},
    "b5": {"max_depth": 3},
    "b6": {},
    "b7": {},
    "b8": {"max_similarity": 0.8},
    "b9": {},
    "b10": {},
}
Edit = Callable[..., RawAudit]
SITE = "https://www.dachdeckerei-muster.de"


def _pages(raw: RawAudit) -> list[PageSnapshot]:
    assert raw.website is not None
    return list(raw.website.pages)


def _content_count(raw: RawAudit) -> int:
    return sum(1 for page in _pages(raw) if page.status_code == 200)


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, THRESHOLDS[check_id])[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    outcome, measured = run(check_id, raw_audit_minimal, THRESHOLDS[check_id])
    assert outcome is CheckOutcome.NOT_MEASURABLE
    assert measured["reason"] == "no_website"


def test_b1_two_h1_and_missing_city(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    pages[0] = pages[0].model_copy(update={"headings": [(1, "Willkommen"), (2, "Leistungen")]})
    outcome, measured = run("b1", with_website(raw_audit_full, pages=pages), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["start_page_h1_has_city"] is False
    pages = [page.model_copy(update={"headings": [(1, "A"), (1, "B")]}) for page in pages]
    assert run("b1", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.FAILED


def test_b2_counts_missing_short_long_and_duplicate_titles(
    raw_audit_full: RawAudit, with_website: Edit
) -> None:
    pages = _pages(raw_audit_full)
    pages[1] = pages[1].model_copy(update={"title": None})
    pages[2] = pages[2].model_copy(update={"title": "Kontakt"})
    pages[3] = pages[3].model_copy(update={"title": "x" * 61})
    duplicate = "Dachdeckerei Muster – Ihr Dachdecker in Münster"
    pages[4] = pages[4].model_copy(update={"title": duplicate})
    pages[5] = pages[5].model_copy(update={"title": duplicate})
    outcome, measured = run("b2", with_website(raw_audit_full, pages=pages), THRESHOLDS["b2"])
    total = _content_count(raw_audit_full)
    assert measured["pages_missing"] == [str(pages[1].url)]
    assert measured["pages_too_short"] == [str(pages[2].url)]
    assert measured["pages_too_long"] == [str(pages[3].url)]
    assert measured["pages_duplicate"] == [str(pages[4].url), str(pages[5].url)]
    assert measured["pages_ok"] == total - 5
    assert measured["pages_total"] == total
    assert outcome is CheckOutcome.PARTIAL


def test_b2_boundaries_are_inclusive(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    pages[0] = pages[0].model_copy(update={"title": "a" * 30})
    pages[1] = pages[1].model_copy(update={"title": "b" * 60})
    assert run("b2", with_website(raw_audit_full, pages=pages), THRESHOLDS["b2"])[1][
        "pages_ok"
    ] == _content_count(raw_audit_full)


def test_b2_mostly_bad_titles_fail(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [page.model_copy(update={"title": "Kurz"}) for page in _pages(raw_audit_full)]
    assert run("b2", with_website(raw_audit_full, pages=pages), THRESHOLDS["b2"])[0] is (
        CheckOutcome.FAILED
    )


def test_b3_meta_description_length(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    pages[0] = pages[0].model_copy(update={"meta_description": "z" * 69})
    pages[1] = pages[1].model_copy(update={"meta_description": "  "})
    pages[2] = pages[2].model_copy(update={"meta_description": "y" * 156})
    outcome, measured = run("b3", with_website(raw_audit_full, pages=pages), THRESHOLDS["b3"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["pages_too_short"] == [str(pages[0].url)]
    assert measured["pages_missing"] == [str(pages[1].url)]
    assert measured["pages_too_long"] == [str(pages[2].url)]
    assert measured["pages_ok"] == _content_count(raw_audit_full) - 3
    without = [page.model_copy(update={"meta_description": None}) for page in pages]
    assert run("b3", with_website(raw_audit_full, pages=without), THRESHOLDS["b3"])[0] is (
        CheckOutcome.FAILED
    )


def test_b4_heading_jump_detected(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [
        page.model_copy(update={"headings": [(1, "A"), (2, "B"), (4, "C")]})
        for page in _pages(raw_audit_full)
    ]
    outcome, measured = run("b4", with_website(raw_audit_full, pages=pages), {})
    assert outcome is CheckOutcome.FAILED
    assert measured["pages_with_jumps"]


def test_b5_unlinked_page_is_too_deep(
    raw_audit_full: RawAudit, with_website: Edit, make_page: Callable[..., Any]
) -> None:
    orphan = make_page(url=f"{SITE}/versteckt/")
    outcome, measured = run(
        "b5",
        with_website(raw_audit_full, pages=[*_pages(raw_audit_full), orphan]),
        {"max_depth": 3},
    )
    assert outcome is CheckOutcome.PARTIAL
    assert measured["pages_too_deep"] == [f"{SITE}/versteckt/"]
    assert measured["deepest_click_depth"] == 2


def test_b6_cryptic_urls(
    raw_audit_full: RawAudit, with_website: Edit, make_page: Callable[..., Any]
) -> None:
    cryptic = [
        make_page(url=f"{SITE}/index.php?id=17"),
        make_page(url=f"{SITE}/seite/12345/"),
    ]
    outcome, measured = run("b6", with_website(raw_audit_full, pages=cryptic), {})
    assert outcome is CheckOutcome.FAILED
    assert len(measured["cryptic_urls"]) == 2


def test_b7_missing_image(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [
        page.model_copy(update={"open_graph": {"title": "T", "description": "D"}})
        for page in _pages(raw_audit_full)
    ]
    outcome, measured = run("b7", with_website(raw_audit_full, pages=pages), {})
    assert outcome is CheckOutcome.FAILED
    assert all(value == ["image"] for value in measured["pages_incomplete"].values())
    assert measured["pages_incomplete_count"] == measured["pages_total"]


def test_b8_duplicate_pair(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    pages[2] = pages[2].model_copy(update={"text_excerpt": pages[1].text_excerpt})
    outcome, measured = run("b8", with_website(raw_audit_full, pages=pages), THRESHOLDS["b8"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["duplicate_pairs"] == [[str(pages[1].url), str(pages[2].url), 1.0]]
    assert measured["pages_involved"] == 2


def test_b8_single_page_not_measurable(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)[:1]
    outcome, _ = run("b8", with_website(raw_audit_full, pages=pages), THRESHOLDS["b8"])
    assert outcome is CheckOutcome.NOT_MEASURABLE


def test_b9_visible_breadcrumb_text_counts(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [page.model_copy(update={"json_ld": []}) for page in _pages(raw_audit_full)]
    assert run("b9", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.FAILED
    pages = [page.model_copy(update={"text_excerpt": "Startseite > Leistungen"}) for page in pages]
    assert run("b9", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.PASSED


def test_b9_start_page_only_not_measurable(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)[:1]
    assert run("b9", with_website(raw_audit_full, pages=pages), {})[1]["reason"] == "no_subpages"


def test_b10_error_page_without_links_fails(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    pages[-1] = pages[-1].model_copy(update={"internal_links": []})
    assert run("b10", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.FAILED
    outcome, measured = run("b10", with_website(raw_audit_full, pages=pages[:-1]), {})
    assert outcome is CheckOutcome.NOT_MEASURABLE
    assert measured["reason"] == "needs_error_page"
