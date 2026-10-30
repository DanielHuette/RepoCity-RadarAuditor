"""Tests for the checks of area f (google_profil).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable
from datetime import date

import pytest

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.f_google_profil import run

THRESHOLDS: dict[str, dict[str, float | int | str]] = {
    "f1": {},
    "f2": {},
    "f3": {"min": 3, "max": 9},
    "f4": {"min_chars": 600, "max_chars": 750},
    "f5": {"max": 50},
    "f6": {},
    "f7": {},
    "f8": {"min_photos": 10},
    "f9": {"max_age_days": 30},
    "f10": {},
    "f11": {"max": 20},
    "f12": {},
    "f13": {},
    "f14": {},
    "f15": {"max_age_days": 90},
}
MAPS_ONLY = {
    "f4": ("description", "description_not_read"),
    "f5": ("services", "services_not_read"),
    "f6": ("products", "products_not_read"),
    "f9": ("post_ages_days", "posts_not_read"),
    "f10": ("question_count", "questions_not_shown"),
    "f11": ("service_areas", "service_areas_not_read"),
    "f13": ("attributes", "attributes_not_read"),
    "f15": ("photo_dated_count", "photos_not_read"),
}
Edit = Callable[..., RawAudit]


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, THRESHOLDS[check_id])[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", [key for key in THRESHOLDS if key != "f1"])
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, THRESHOLDS[check_id])[1] == {"reason": "no_place"}


@pytest.mark.parametrize("check_id", list(MAPS_ONLY))
def test_maps_only_fields_not_read_are_not_measurable(
    raw_audit_full: RawAudit, with_place: Edit, check_id: str
) -> None:
    """A profile from the Places API lacks the fields only the Maps page shows."""
    field, reason = MAPS_ONLY[check_id]
    raw = with_place(raw_audit_full, **{field: None})
    assert run(check_id, raw, THRESHOLDS[check_id]) == (
        CheckOutcome.NOT_MEASURABLE,
        {"reason": reason},
    )


@pytest.mark.parametrize(
    ("check_id", "field"),
    [("f5", "services"), ("f6", "products"), ("f11", "service_areas"), ("f13", "attributes")],
)
def test_maps_lists_shown_empty_fail(
    raw_audit_full: RawAudit, with_place: Edit, check_id: str, field: str
) -> None:
    raw = with_place(raw_audit_full, **{field: []})
    assert run(check_id, raw, THRESHOLDS[check_id])[0] is CheckOutcome.FAILED


def test_f4_measures_length_and_front_words(raw_audit_full: RawAudit, with_place: Edit) -> None:
    outcome, measured = run("f4", raw_audit_full, THRESHOLDS["f4"])
    assert outcome is CheckOutcome.PASSED
    assert measured["city_in_front"] is True
    assert measured["service_in_front"] is True
    short = with_place(raw_audit_full, description="Dachdecker in Münster.")
    assert run("f4", short, THRESHOLDS["f4"])[0] is CheckOutcome.PARTIAL
    late = with_place(raw_audit_full, description="x " * 200 + "Dachdecker Münster " * 10)
    outcome, measured = run("f4", late, THRESHOLDS["f4"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["city_in_front"] is False
    assert run("f4", with_place(raw_audit_full, description=""), THRESHOLDS["f4"])[0] is (
        CheckOutcome.FAILED
    )


def test_f5_partial_without_tracked_service_or_above_maximum(
    raw_audit_full: RawAudit, with_place: Edit
) -> None:
    outcome, measured = run("f5", raw_audit_full, THRESHOLDS["f5"])
    assert outcome is CheckOutcome.PASSED
    assert measured["phrases_covered"] == ["Dachdecker Münster", "Dachsanierung Münster"]
    assert measured["services_order_checked"] is False
    unrelated = with_place(raw_audit_full, services=["Gartenpflege"])
    assert run("f5", unrelated, THRESHOLDS["f5"])[0] is CheckOutcome.PARTIAL
    many = with_place(raw_audit_full, services=[f"Dachsanierung {i}" for i in range(51)])
    assert run("f5", many, THRESHOLDS["f5"])[0] is CheckOutcome.PARTIAL


def test_f9_old_post_is_partial_and_no_post_fails(
    raw_audit_full: RawAudit, with_place: Edit
) -> None:
    assert run("f9", raw_audit_full, THRESHOLDS["f9"])[1]["newest_post_days"] == 12
    old = with_place(raw_audit_full, post_ages_days=[45, 90])
    assert run("f9", old, THRESHOLDS["f9"])[0] is CheckOutcome.PARTIAL
    assert run("f9", with_place(raw_audit_full, post_ages_days=[]), THRESHOLDS["f9"]) == (
        CheckOutcome.FAILED,
        {"post_count": 0, "newest_post_days": None, "max_age_days": 30.0},
    )


def test_f10_needs_questions(raw_audit_full: RawAudit, with_place: Edit) -> None:
    assert run("f10", with_place(raw_audit_full, question_count=0), {})[0] is CheckOutcome.FAILED


def test_f11_too_many_service_areas(raw_audit_full: RawAudit, with_place: Edit) -> None:
    areas = [f"Ort {index}" for index in range(30)]
    outcome, measured = run("f11", with_place(raw_audit_full, service_areas=areas), {"max": 20})
    assert outcome is CheckOutcome.FAILED
    assert measured["service_area_count"] == 30
    assert len(measured["service_areas"]) == 10


def test_f3_categories_not_shown_in_the_browser(raw_audit_full: RawAudit, with_place: Edit) -> None:
    browser_only = with_place(raw_audit_full, value_routes={"primary_type": "browser"})
    assert run("f3", browser_only, THRESHOLDS["f3"])[1] == {"reason": "categories_not_read"}


def test_f2_compares_only_categories_of_the_same_route(
    raw_audit_full: RawAudit, with_place: Edit
) -> None:
    browser_only = with_place(
        raw_audit_full, primary_type="Dachdecker", value_routes={"primary_type": "browser"}
    )
    assert run("f2", browser_only, {})[1] == {"reason": "mixed_category_sources"}


def test_checks_name_the_route_of_their_data(raw_audit_full: RawAudit, with_place: Edit) -> None:
    """Places profiles report the paid route; browser values report the free one."""
    assert run("f8", raw_audit_full, THRESHOLDS["f8"])[1]["route"] == "places_api"
    assert "route" not in run("f4", raw_audit_full, THRESHOLDS["f4"])[1]
    read = with_place(raw_audit_full, value_routes={"photo_count": "browser"})
    assert run("f8", read, THRESHOLDS["f8"])[1]["route"] == "browser"


def test_f1_missing_profile_fails_unless_collector_failed(raw_audit_minimal: RawAudit) -> None:
    assert run("f1", raw_audit_minimal, {}) == (CheckOutcome.FAILED, {"profile_found": False})
    failed = raw_audit_minimal.model_copy(update={"warnings": ["places: HTTP 503"]})
    assert run("f1", failed, {})[1] == {"reason": "collector_failed"}


@pytest.mark.parametrize(
    ("status", "expected"),
    [("CLOSED_TEMPORARILY", CheckOutcome.PARTIAL), ("CLOSED_PERMANENTLY", CheckOutcome.FAILED)],
)
def test_f1_closed(
    raw_audit_full: RawAudit, with_place: Edit, status: str, expected: CheckOutcome
) -> None:
    assert run("f1", with_place(raw_audit_full, business_status=status), {})[0] is expected


def test_f2_minority_and_foreign_category(raw_audit_full: RawAudit, with_place: Edit) -> None:
    minority = with_place(raw_audit_full, primary_type="general_contractor")
    outcome, measured = run("f2", minority, {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["competitor_types"]["Bau Krause"] == "general_contractor"
    assert run("f2", with_place(raw_audit_full, primary_type="plumber"), {})[0] is (
        CheckOutcome.FAILED
    )
    alone = raw_audit_full.model_copy(update={"competitor_places": []})
    assert run("f2", alone, {})[1] == {"reason": "no_competitors"}


@pytest.mark.parametrize(
    ("secondary", "expected"),
    [(2, CheckOutcome.FAILED), (3, CheckOutcome.PASSED), (10, CheckOutcome.PARTIAL)],
)
def test_f3_category_count(
    raw_audit_full: RawAudit, with_place: Edit, secondary: int, expected: CheckOutcome
) -> None:
    types = ["roofing_contractor", "establishment", *[f"type_{n}" for n in range(secondary)]]
    outcome, measured = run("f3", with_place(raw_audit_full, types=types), THRESHOLDS["f3"])
    assert outcome is expected
    assert measured["count"] == secondary


def test_f3_far_above_max_fails(raw_audit_full: RawAudit, with_place: Edit) -> None:
    types = [f"type_{n}" for n in range(11)]
    assert run("f3", with_place(raw_audit_full, types=types), THRESHOLDS["f3"])[0] is (
        CheckOutcome.FAILED
    )


def test_f7_without_hours_fails(raw_audit_full: RawAudit, with_place: Edit) -> None:
    assert run("f7", with_place(raw_audit_full, has_opening_hours=False), {})[0] is (
        CheckOutcome.FAILED
    )


@pytest.mark.parametrize(
    ("photos", "expected"), [(8, CheckOutcome.PARTIAL), (7, CheckOutcome.FAILED)]
)
def test_f8_photo_tolerance(
    raw_audit_full: RawAudit, with_place: Edit, photos: int, expected: CheckOutcome
) -> None:
    raw = with_place(raw_audit_full, photo_count=photos)
    assert run("f8", raw, THRESHOLDS["f8"])[0] is expected


def test_f12_website_link(raw_audit_full: RawAudit, with_place: Edit) -> None:
    assert run("f12", with_place(raw_audit_full, website=None), {})[0] is CheckOutcome.FAILED
    other = with_place(raw_audit_full, website="https://muster.example.com/")
    assert run("f12", other, {})[0] is CheckOutcome.PARTIAL


def test_f14_keyword_stuffed_name_fails(raw_audit_full: RawAudit, with_place: Edit) -> None:
    stuffed = with_place(raw_audit_full, name="Dachdeckerei Muster GmbH Dachdecker Münster")
    outcome, measured = run("f14", stuffed, {})
    assert outcome is CheckOutcome.FAILED
    assert measured["keywords_in_name"] == ["dachdecker", "muenster"]


def test_f14_city_in_name_without_website_name(
    raw_audit_full: RawAudit, with_place: Edit, with_website: Edit
) -> None:
    raw = with_website(with_place(raw_audit_full, name="Muster Münster"), footer_nap={})
    outcome, measured = run("f14", raw, {})
    assert outcome is CheckOutcome.FAILED
    assert measured["keywords_in_name"] == ["muenster"]


@pytest.mark.parametrize(
    ("newest", "expected", "days"),
    [
        (date(2026, 7, 3), CheckOutcome.PASSED, 90),
        (date(2026, 7, 2), CheckOutcome.FAILED, 91),
        (date(2022, 4, 30), CheckOutcome.FAILED, 1615),
    ],
)
def test_f15_newest_photo_age_against_the_limit(
    raw_audit_full: RawAudit, with_place: Edit, newest: date, expected: CheckOutcome, days: int
) -> None:
    """Collected on 1 October 2026: 90 days back is 3 July."""
    raw = with_place(
        raw_audit_full,
        photo_newest_date=newest,
        photo_dated_count=4,
        value_routes={"photo_newest_date": "browser", "photo_dated_count": "browser"},
    )
    outcome, measured = run("f15", raw, THRESHOLDS["f15"])
    assert outcome is expected
    assert measured == {
        "newest_photo_date": newest.isoformat(),
        "newest_photo_days": days,
        "photos_with_date": 4,
        "max_age_days": 90.0,
        "route": "browser",
    }


def test_f15_without_dated_photo_is_not_measurable(
    raw_audit_full: RawAudit, with_place: Edit
) -> None:
    raw = with_place(raw_audit_full, photo_newest_date=None, photo_dated_count=0)
    assert run("f15", raw, THRESHOLDS["f15"]) == (
        CheckOutcome.NOT_MEASURABLE,
        {"reason": "no_dated_photos", "photos_with_date": 0},
    )


def test_f8_counts_photos_without_judging_their_age(raw_audit_full: RawAudit) -> None:
    outcome, measured = run("f8", raw_audit_full, THRESHOLDS["f8"])
    assert outcome is CheckOutcome.PASSED
    assert "photo_age_checked" not in measured
    assert measured["logo_and_cover_checked"] is False
