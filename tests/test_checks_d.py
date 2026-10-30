"""Tests for the checks of area d (firmendaten).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable

import pytest

from radarauditor.collectors.snapshot import PageSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import nap_value
from radarauditor.scoring.checks.d_firmendaten import run

CHECK_IDS = ["d1", "d2", "d3", "d4", "d5", "d6"]
Edit = Callable[..., RawAudit]


def _pages(raw: RawAudit) -> list[PageSnapshot]:
    assert raw.website is not None
    return list(raw.website.pages)


@pytest.mark.parametrize("check_id", CHECK_IDS)
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, {})[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", CHECK_IDS)
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, {})[0] is CheckOutcome.NOT_MEASURABLE


def test_nap_value_tolerates_key_spelling() -> None:
    nap = {"Telefon": " 0251 1 ", "street_name": "Weg 1", "PLZ": "48143", "firma": "X"}
    assert nap_value(nap, "phone") == "0251 1"
    assert nap_value(nap, "postal_code") == "48143"
    assert nap_value(nap, "name") == "X"
    assert nap_value({}, "street") is None


def test_d1_partial_and_failed(raw_audit_full: RawAudit, with_website: Edit) -> None:
    outcome, measured = run("d1", with_website(raw_audit_full, footer_nap={"name": "Muster"}), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["missing_fields"] == ["street", "postal_code", "phone"]
    assert run("d1", with_website(raw_audit_full, footer_nap={}), {})[0] is CheckOutcome.FAILED


def test_d2_mismatching_phone(raw_audit_full: RawAudit, with_place: Edit) -> None:
    outcome, measured = run("d2", with_place(raw_audit_full, phone="0251 999999"), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["mismatches"] == ["phone"]


def test_d2_everything_different_fails(raw_audit_full: RawAudit, with_place: Edit) -> None:
    other = with_place(
        raw_audit_full, phone="030 1", formatted_address="Ringweg 9, 10115 Berlin, Deutschland"
    )
    outcome, measured = run("d2", other, {})
    assert outcome is CheckOutcome.FAILED
    assert measured["mismatches"] == ["phone", "postal_code", "street"]


def test_d2_without_footer_nap(raw_audit_full: RawAudit, with_website: Edit) -> None:
    raw = with_website(raw_audit_full, footer_nap={})
    assert run("d2", raw, {})[1]["reason"] == "no_footer_nap"


def test_d3_hours_only_on_website(raw_audit_full: RawAudit, with_place: Edit) -> None:
    outcome, measured = run("d3", with_place(raw_audit_full, has_opening_hours=False), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["hours_on_google"] is False


def test_d3_no_hours_without_place_fails(raw_audit_full: RawAudit, with_website: Edit) -> None:
    raw = with_website(raw_audit_full, opening_hours_text=None).model_copy(update={"place": None})
    assert run("d3", raw, {})[0] is CheckOutcome.FAILED


def test_d4_no_page_with_address(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [page.model_copy(update={"text_excerpt": "Text"}) for page in _pages(raw_audit_full)]
    outcome, measured = run("d4", with_website(raw_audit_full, pages=pages), {})
    assert outcome is CheckOutcome.FAILED
    assert measured["postal_code"] == "48143"


def test_d5_without_any_profile_link_fails(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [
        page.model_copy(update={"json_ld": [], "external_links": []})
        for page in _pages(raw_audit_full)
    ]
    outcome, measured = run("d5", with_website(raw_audit_full, pages=pages), {})
    assert outcome is CheckOutcome.FAILED
    assert measured == {"google_profile_links": []}


@pytest.mark.parametrize(
    "link",
    [
        "https://www.google.com/maps/place/Dachdeckerei+Muster",
        "https://maps.app.goo.gl/abc123",
        "https://g.page/dachdeckerei-muster",
        "https://www.google.com/search?kgmid=/g/11abc",
    ],
)
def test_d5_page_link_to_profile_passes(
    raw_audit_full: RawAudit, with_website: Edit, with_place: Edit, link: str
) -> None:
    pages = [
        page.model_copy(update={"json_ld": [], "external_links": [link]})
        for page in _pages(raw_audit_full)
    ]
    raw = with_place(with_website(raw_audit_full, pages=pages), google_maps_uri=link)
    outcome, measured = run("d5", raw, {})
    assert outcome is CheckOutcome.PASSED
    assert measured["google_profile_links"] == [link]


def test_d5_ignores_other_external_links(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [
        page.model_copy(
            update={"json_ld": [], "external_links": ["https://www.google.com/search?q=x"]}
        )
        for page in _pages(raw_audit_full)
    ]
    assert run("d5", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.FAILED


def test_d6_partial_and_failed(raw_audit_full: RawAudit, with_website: Edit) -> None:
    pages = [page.model_copy(update={"mailto_links": []}) for page in _pages(raw_audit_full)]
    assert run("d6", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.PARTIAL
    pages = [page.model_copy(update={"tel_links": []}) for page in pages]
    assert run("d6", with_website(raw_audit_full, pages=pages), {})[0] is CheckOutcome.FAILED
