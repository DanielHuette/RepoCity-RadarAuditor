"""Tests for the checks of area e (strukturierte_daten).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable

import pytest

from radarauditor.collectors.snapshot import PageSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import schema_objects, schema_types
from radarauditor.scoring.checks.e_strukturierte_daten import is_local_business, run

CHECK_IDS = ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8"]
Edit = Callable[..., RawAudit]
CONTEXT = "https://schema.org"


def _pages(raw: RawAudit) -> list[PageSnapshot]:
    assert raw.website is not None
    return list(raw.website.pages)


def _with_start_schema(raw: RawAudit, edit: Edit, json_ld: list[dict[str, object]]) -> RawAudit:
    pages = _pages(raw)
    pages[0] = pages[0].model_copy(update={"json_ld": json_ld})
    return edit(raw, pages=pages)


@pytest.mark.parametrize("check_id", CHECK_IDS)
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, {})[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", CHECK_IDS)
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, {})[0] is CheckOutcome.NOT_MEASURABLE


def test_schema_objects_flatten_graph_and_nesting() -> None:
    json_ld: list[dict[str, object]] = [
        {
            "@context": CONTEXT,
            "@graph": [{"@type": ["Plumber", "LocalBusiness"], "x": [{"@type": "A"}]}],
        }
    ]
    kinds = {kind for item in schema_objects(json_ld) for kind in schema_types(item)}
    assert kinds == {"Plumber", "LocalBusiness", "A"}
    assert is_local_business({"@type": "ProfessionalService"})
    assert not is_local_business({"@type": "Service"})


def test_e1_incomplete_and_missing(raw_audit_full: RawAudit, with_website: Edit) -> None:
    partial = [{"@context": CONTEXT, "@type": "Plumber", "name": "Muster", "address": "Weg 1"}]
    outcome, measured = run("e1", _with_start_schema(raw_audit_full, with_website, partial), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["missing_properties"] == [
        "telephone",
        "url",
        "geo",
        "openingHoursSpecification",
    ]
    outcome, _ = run("e1", _with_start_schema(raw_audit_full, with_website, []), {})
    assert outcome is CheckOutcome.FAILED


def test_e2_organization_without_logo(raw_audit_full: RawAudit, with_website: Edit) -> None:
    organization = [{"@context": CONTEXT, "@type": "Organization", "name": "Muster"}]
    raw = _with_start_schema(raw_audit_full, with_website, organization)
    assert run("e2", raw, {})[0] is CheckOutcome.PARTIAL
    assert run("e2", _with_start_schema(raw_audit_full, with_website, []), {})[0] is (
        CheckOutcome.FAILED
    )


def test_e3_service_pages_without_area(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [page.model_copy(update={"json_ld": []}) for page in _pages(raw_audit_full)]
    outcome, measured = run("e3", with_website(raw_audit_full, pages=pages), {})
    assert outcome is CheckOutcome.FAILED
    assert measured["service_pages_total"] == 2


def test_e4_faq_section_without_schema(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [page.model_copy(update={"json_ld": []}) for page in _pages(raw_audit_full)]
    assert run("e4", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.FAILED
    no_faq = [page.model_copy(update={"headings": [(1, "Titel")]}) for page in pages]
    assert run("e4", with_website(raw_audit_full, pages=no_faq), {})[1]["reason"] == (
        "no_faq_sections"
    )


def test_e5_half_without_breadcrumb_is_partial(
    raw_audit_full: RawAudit, with_website: Edit
) -> None:
    pages = _pages(raw_audit_full)
    stripped = [
        page.model_copy(update={"json_ld": []}) if index in (1, 2, 3) else page
        for index, page in enumerate(pages)
    ]
    outcome, measured = run("e5", with_website(raw_audit_full, pages=stripped), {})
    assert outcome is CheckOutcome.PARTIAL
    assert len(measured["pages_without_breadcrumb_schema"]) == 3


def test_e6_rating_markup_without_visible_reviews_fails(
    raw_audit_full: RawAudit, with_website: Edit
) -> None:
    rating = [
        {
            "@context": CONTEXT,
            "@type": "LocalBusiness",
            "name": "Muster",
            "address": "Weg 1",
            "aggregateRating": {"@type": "AggregateRating", "ratingValue": 5},
        }
    ]
    raw = _with_start_schema(raw_audit_full, with_website, rating)
    outcome, measured = run("e6", raw, {})
    assert outcome is CheckOutcome.FAILED
    assert measured["pages_with_rating_markup"] == 1


def test_e7_errors_listed(raw_audit_full: RawAudit, with_website: Edit) -> None:
    broken = [{"@type": "FAQPage"}, {"@context": CONTEXT, "@type": "BreadcrumbList"}]
    pages = [page.model_copy(update={"json_ld": broken}) for page in _pages(raw_audit_full)]
    outcome, measured = run("e7", with_website(raw_audit_full, pages=pages), {})
    assert outcome is CheckOutcome.FAILED
    problems = {(error["type"], error["problem"]) for error in measured["errors"]}
    assert {("FAQPage", "@context"), ("FAQPage", "mainEntity")} <= problems
    empty = [page.model_copy(update={"json_ld": []}) for page in pages]
    assert run("e7", with_website(raw_audit_full, pages=empty), {})[1]["reason"] == (
        "no_structured_data"
    )


def test_e8_same_as_partial_and_failed(raw_audit_full: RawAudit, with_website: Edit) -> None:
    only_facebook = [
        {
            "@context": CONTEXT,
            "@type": "Organization",
            "name": "Muster",
            "sameAs": "https://www.facebook.com/muster",
        }
    ]
    outcome, measured = run(
        "e8", _with_start_schema(raw_audit_full, with_website, only_facebook), {}
    )
    assert outcome is CheckOutcome.PARTIAL
    assert measured["missing_targets"] == ["google", "wikidata"]
    assert run("e8", _with_start_schema(raw_audit_full, with_website, []), {})[0] is (
        CheckOutcome.FAILED
    )
