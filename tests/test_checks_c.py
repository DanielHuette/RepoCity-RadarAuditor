"""Tests for the checks of area c (inhalt).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable

import pytest

from radarauditor.collectors.snapshot import GapKeyword, PageSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.c_inhalt import run

THRESHOLDS: dict[str, dict[str, float | int | str]] = {
    "c1": {},
    "c2": {"min_volume": 10},
    "c3": {"min_words": 300},
    "c4": {"max_density": 0.02},
    "c5": {},
    "c6": {},
    "c7": {},
    "c8": {},
    "c9": {},
    "c10": {"min_posts": 5},
    "c11": {"min_words": 100},
    "c12": {},
}
PAID = {"c2", "c12"}
Edit = Callable[..., RawAudit]
SITE = "https://www.dachdeckerei-muster.de"


def _pages(raw: RawAudit) -> list[PageSnapshot]:
    assert raw.website is not None
    return list(raw.website.pages)


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, THRESHOLDS[check_id])[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, THRESHOLDS[check_id])[0] is (
        CheckOutcome.NOT_MEASURABLE
    )


@pytest.mark.parametrize("check_id", sorted(PAID))
def test_paid_checks_without_paid_data(raw_audit_full: RawAudit, check_id: str) -> None:
    raw = raw_audit_full.model_copy(update={"paid": None})
    assert run(check_id, raw, THRESHOLDS[check_id])[1] == {"reason": "no_paid_data"}


def _with_paid(raw: RawAudit, **updates: object) -> RawAudit:
    assert raw.paid is not None
    return raw.model_copy(update={"paid": raw.paid.model_copy(update=updates)})


def test_c2_lists_demanded_phrases_and_their_pages(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("c2", raw_audit_full, THRESHOLDS["c2"])
    assert outcome is CheckOutcome.PASSED
    assert measured == {
        "phrases_with_demand": {"Dachdecker Münster": 880},
        "phrases_without_page": [],
        "min_volume": 10.0,
    }


def test_c2_phrase_without_page_fails(raw_audit_full: RawAudit) -> None:
    raw = _with_paid(
        raw_audit_full,
        primary_phrases=["Dachdecker Münster", "Dachfenster Münster", "Carport Münster"],
        volumes={"Dachdecker Münster": 880, "Dachfenster Münster": 90, "Carport Münster": 4},
    )
    outcome, measured = run("c2", raw, THRESHOLDS["c2"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["phrases_without_page"] == ["Dachfenster Münster"]
    assert "Carport Münster" not in measured["phrases_with_demand"]
    alone = _with_paid(raw, primary_phrases=["Dachfenster Münster"])
    assert run("c2", alone, THRESHOLDS["c2"])[0] is CheckOutcome.FAILED


def test_c2_needs_volumes_and_website(raw_audit_full: RawAudit) -> None:
    assert run("c2", _with_paid(raw_audit_full, volumes={}), THRESHOLDS["c2"])[1] == {
        "reason": "no_volumes"
    }
    no_site = raw_audit_full.model_copy(update={"website": None})
    assert run("c2", no_site, THRESHOLDS["c2"])[1] == {"reason": "no_website"}


def _gap(count: int, volume: int) -> list[GapKeyword]:
    return [
        GapKeyword(
            phrase=f"begriff {index}",
            volume=volume,
            competitor_domain="dach-schmidt.de",
            competitor_position=index + 1,
        )
        for index in range(count)
    ]


@pytest.mark.parametrize(
    ("gap", "expected"),
    [
        (_gap(3, 9), CheckOutcome.PASSED),
        (_gap(9, 10), CheckOutcome.PARTIAL),
        (_gap(12, 50), CheckOutcome.FAILED),
    ],
)
def test_c12_counts_gap_keywords_with_volume(
    raw_audit_full: RawAudit, gap: list[GapKeyword], expected: CheckOutcome
) -> None:
    outcome, measured = run("c12", _with_paid(raw_audit_full, keyword_gap=gap), {})
    assert outcome is expected
    assert len(measured["top_gap_keywords"]) <= 10


def test_c12_without_gap_data(raw_audit_full: RawAudit) -> None:
    assert run("c12", _with_paid(raw_audit_full, keyword_gap=None), {})[1] == {
        "reason": "no_keyword_gap"
    }


def test_c1_service_without_page(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [page for page in _pages(raw_audit_full) if "dachsanierung" not in str(page.url)]
    outcome, measured = run("c1", with_website(raw_audit_full, pages=pages), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["services_without_page"] == ["Dachsanierung Münster"]
    assert measured["services_total"] == 2


def test_c1_without_phrases_not_measurable(raw_audit_full: RawAudit) -> None:
    raw = raw_audit_full.model_copy(update={"serps": []})
    assert run("c1", raw, {})[1]["reason"] == "no_search_phrases"


def test_c3_word_count_arithmetic(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    pages[0] = pages[0].model_copy(update={"word_count": 250})
    outcome, measured = run("c3", with_website(raw_audit_full, pages=pages), THRESHOLDS["c3"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["pages_below_min_words"] == {f"{SITE}/": 250}
    assert measured["service_pages_total"] == 2
    assert measured["lowest_word_count"] == 250


def test_c3_all_short_but_close_is_partial(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [page.model_copy(update={"word_count": 240}) for page in _pages(raw_audit_full)]
    raw = with_website(raw_audit_full, pages=pages)
    assert run("c3", raw, THRESHOLDS["c3"])[0] is CheckOutcome.PARTIAL
    pages = [page.model_copy(update={"word_count": 239}) for page in pages]
    raw = with_website(raw_audit_full, pages=pages)
    assert run("c3", raw, THRESHOLDS["c3"])[0] is CheckOutcome.FAILED


def test_c3_without_service_pages_not_measurable(
    raw_audit_full: RawAudit, with_website: Edit
) -> None:
    pages = [page for page in _pages(raw_audit_full) if "ratgeber" in str(page.url)]
    assert run("c3", with_website(raw_audit_full, pages=pages), THRESHOLDS["c3"])[1] == {
        "reason": "no_service_pages"
    }


def test_c4_keyword_stuffing_fails_part(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    stuffed = " ".join(["dachdecker"] * 10 + ["wort"] * 90)
    pages[3] = pages[3].model_copy(update={"text_excerpt": stuffed})
    outcome, measured = run("c4", with_website(raw_audit_full, pages=pages), THRESHOLDS["c4"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["pages_over_density"] == {str(pages[3].url): 0.1}


def test_c4_missing_terms_fail(raw_audit_full: RawAudit) -> None:
    raw = raw_audit_full.model_copy(
        update={
            "serps": [
                serp.model_copy(update={"phrase": "Zimmerei Münster"})
                for serp in raw_audit_full.serps
            ]
        }
    )
    outcome, measured = run("c4", raw, THRESHOLDS["c4"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["phrases_missing_in_text"] == ["Zimmerei Münster"]


def test_c5_unanswered_question(raw_audit_full: RawAudit) -> None:
    serps = list(raw_audit_full.serps)
    serps[0] = serps[0].model_copy(
        update={"people_also_ask": ["Welche Förderung gibt es für Gründächer?"]}
    )
    outcome, measured = run("c5", raw_audit_full.model_copy(update={"serps": serps}), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["unanswered_questions"] == ["Welche Förderung gibt es für Gründächer?"]
    no_questions = [serp.model_copy(update={"people_also_ask": []}) for serp in serps]
    raw = raw_audit_full.model_copy(update={"serps": no_questions})
    assert run("c5", raw, {})[1]["reason"] == "no_questions"


def test_c6_service_page_without_city(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [
        page.model_copy(update={"title": "Dachdecker", "text_excerpt": "Text", "headings": []})
        for page in _pages(raw_audit_full)
    ]
    outcome, measured = run("c6", with_website(raw_audit_full, pages=pages), {})
    assert outcome is CheckOutcome.FAILED
    assert measured["city"] == "Münster"


def test_c7_old_years(raw_audit_full: RawAudit, with_website: Edit) -> None:
    def year_pages(text: str) -> RawAudit:
        pages = [page.model_copy(update={"text_excerpt": text}) for page in _pages(raw_audit_full)]
        return with_website(raw_audit_full, pages=pages)

    assert run("c7", year_pages("© 2024"), {})[0] is CheckOutcome.PARTIAL
    assert run("c7", year_pages("© 2019"), {})[0] is CheckOutcome.FAILED
    outcome, measured = run("c7", year_pages("ohne Jahr"), {})
    assert outcome is CheckOutcome.FAILED
    assert measured["latest_year_on_site"] is None


def test_c8_about_page_without_qualification(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    pages[3] = pages[3].model_copy(update={"text_excerpt": "Wir sind nett."})
    assert run("c8", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.PARTIAL
    pages.pop(3)
    assert run("c8", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.FAILED


def test_c9_contact_page_without_phone(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    pages[2] = pages[2].model_copy(update={"tel_links": []})
    assert run("c9", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.PARTIAL
    pages.pop(2)
    assert run("c9", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.FAILED


def test_c10_post_count_tolerance(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    posts = [
        page for page in pages if "/ratgeber/" in str(page.url) and str(page.url).count("/") > 4
    ]
    others = [page for page in pages if page not in posts]
    four = with_website(raw_audit_full, pages=[*others, *posts[:4]])
    outcome, measured = run("c10", four, THRESHOLDS["c10"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["guide_posts"] == 4
    three = with_website(raw_audit_full, pages=[*others, *posts[:3]])
    assert run("c10", three, THRESHOLDS["c10"])[0] is CheckOutcome.FAILED


def test_c11_thin_pages(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = _pages(raw_audit_full)
    pages[2] = pages[2].model_copy(update={"word_count": 40})
    outcome, measured = run("c11", with_website(raw_audit_full, pages=pages), THRESHOLDS["c11"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["thin_pages"] == {str(pages[2].url): 40}
    thin = [page.model_copy(update={"word_count": 10}) for page in pages]
    assert run("c11", with_website(raw_audit_full, pages=thin), THRESHOLDS["c11"])[0] is (
        CheckOutcome.FAILED
    )


def test_service_pages_fallback_without_phrases(raw_audit_full: RawAudit) -> None:
    raw = raw_audit_full.model_copy(update={"serps": []})
    outcome, measured = run("c3", raw, THRESHOLDS["c3"])
    assert outcome is CheckOutcome.PASSED
    assert measured["service_pages_total"] == 1
