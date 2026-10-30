"""Tests for the checks of area g (bewertungen).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from radarauditor.collectors.snapshot import (
    AccessSnapshot,
    ListedBusiness,
    MapResultList,
    RawAudit,
)
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.g_bewertungen import (
    owner_reply,
    parse_time,
    reply_data_available,
    reply_delay_days,
    review_text,
    run,
)

THRESHOLDS: dict[str, dict[str, float | int | str]] = {
    "g1": {},
    "g2": {"min_rating": 4.3},
    "g3": {"min_per_month": 2},
    "g4": {"min_reply_rate": 0.8, "max_reply_days": 7},
    "g5": {},
    "g6": {},
    "g7": {},
    "g8": {},
}
Edit = Callable[..., RawAudit]


@pytest.fixture
def raw_audit_full(raw_audit_full: RawAudit) -> RawAudit:
    """The full audit without access data, so g4 and g5 read the Places sample."""
    return raw_audit_full.model_copy(update={"access": None})


def _review(
    day: str, rating: int = 5, text: str = "Gut", reply: object = None
) -> dict[str, object]:
    review: dict[str, object] = {"publishTime": f"{day}T10:00:00Z", "rating": rating, "text": text}
    if reply is not None:
        review["ownerReply"] = reply
    return review


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, THRESHOLDS[check_id])[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, THRESHOLDS[check_id])[0] is (
        CheckOutcome.NOT_MEASURABLE
    )


def test_review_helpers_are_defensive() -> None:
    assert parse_time("kaputt") is None
    assert parse_time(42) is None
    assert parse_time("2026-09-01T10:00:00") == datetime(2026, 9, 1, 10, tzinfo=UTC)
    assert review_text({"text": {"text": "Hallo"}}) == "Hallo"
    assert review_text({"originalText": "Original"}) == "Original"
    assert review_text({}) == ""
    assert owner_reply({"reviewReply": {"comment": "Danke"}}) == {"comment": "Danke"}
    assert owner_reply({"ownerResponse": ""}) is None
    reply = {"publishTime": "2026-09-04T10:00:00Z"}
    assert reply_delay_days(_review("2026-09-01", reply=reply)) == 3.0
    assert reply_delay_days(_review("2026-09-01", reply="Danke")) is None


def test_g1_review_count_against_competitor_average(
    raw_audit_full: RawAudit, with_place: Edit
) -> None:
    outcome, measured = run("g1", with_place(raw_audit_full, review_count=11), {})
    assert outcome is CheckOutcome.FAILED
    assert measured["customer_reviews"] == 11
    assert measured["competitors"] == {
        "Dach Schmidt": 84,
        "Bedachungen Meier": 95,
        "Bau Krause": 60,
    }
    assert measured["competitor_average"] == 79.7
    assert run("g1", with_place(raw_audit_full, review_count=64), {})[0] is CheckOutcome.PARTIAL
    assert run("g1", with_place(raw_audit_full, review_count=63), {})[0] is CheckOutcome.FAILED
    alone = raw_audit_full.model_copy(update={"competitor_places": []})
    assert run("g1", alone, {})[1] == {"reason": "no_competitors"}


@pytest.mark.parametrize(
    ("rating", "expected"),
    [
        (4.3, CheckOutcome.PASSED),
        (3.44, CheckOutcome.PARTIAL),
        (3.43, CheckOutcome.FAILED),
        (None, CheckOutcome.FAILED),
    ],
)
def test_g2_rating_threshold(
    raw_audit_full: RawAudit, with_place: Edit, rating: float | None, expected: CheckOutcome
) -> None:
    outcome, measured = run("g2", with_place(raw_audit_full, rating=rating), THRESHOLDS["g2"])
    assert outcome is expected
    assert measured["rating"] == rating
    assert measured["min_rating"] == 4.3


def test_g3_rate_over_sample_span(raw_audit_full: RawAudit, with_place: Edit) -> None:
    reviews = [_review("2026-07-03"), _review("2026-08-15"), _review("2026-09-20")]
    outcome, measured = run(
        "g3", with_place(raw_audit_full, recent_reviews=reviews), {"min_per_month": 2}
    )
    assert outcome is CheckOutcome.FAILED
    assert measured["reviews_per_month"] == 1.01
    assert measured["oldest_review"] == "2026-07-03"
    no_reviews = with_place(raw_audit_full, review_count=0, recent_reviews=[])
    assert run("g3", no_reviews, THRESHOLDS["g3"])[0] is CheckOutcome.FAILED
    undated = with_place(raw_audit_full, recent_reviews=[{"rating": 5}])
    assert run("g3", undated, THRESHOLDS["g3"])[1] == {"reason": "no_review_dates"}


def test_g3_short_span_counts_as_one_month(raw_audit_full: RawAudit, with_place: Edit) -> None:
    reviews = [_review("2026-09-25"), _review("2026-09-28")]
    outcome, measured = run(
        "g3", with_place(raw_audit_full, recent_reviews=reviews), THRESHOLDS["g3"]
    )
    assert outcome is CheckOutcome.PASSED
    assert measured["reviews_per_month"] == 2.0


def test_g4_reply_rate_and_delay(raw_audit_full: RawAudit, with_place: Edit) -> None:
    slow = {"publishTime": "2026-09-20T10:00:00Z"}
    reviews = [_review("2026-09-01", reply=slow) for _ in range(4)] + [_review("2026-09-02")]
    outcome, measured = run(
        "g4", with_place(raw_audit_full, recent_reviews=reviews), THRESHOLDS["g4"]
    )
    assert outcome is CheckOutcome.PARTIAL
    assert measured["reply_rate"] == 0.8
    assert measured["average_reply_days"] == 19.0
    sparse = [_review("2026-09-01", reply="Danke")] + [_review("2026-09-02") for _ in range(4)]
    outcome, measured = run(
        "g4", with_place(raw_audit_full, recent_reviews=sparse), THRESHOLDS["g4"]
    )
    assert outcome is CheckOutcome.FAILED
    assert measured["reply_rate"] == 0.2
    assert measured["average_reply_days"] is None
    assert run("g4", with_place(raw_audit_full, recent_reviews=[]), THRESHOLDS["g4"])[1] == {
        "reason": "no_reviews"
    }


def test_g4_rate_within_tolerance_is_partial(raw_audit_full: RawAudit, with_place: Edit) -> None:
    reviews = [_review("2026-09-01", reply="Danke") for _ in range(13)] + [
        _review("2026-09-02") for _ in range(7)
    ]
    outcome, measured = run(
        "g4", with_place(raw_audit_full, recent_reviews=reviews), THRESHOLDS["g4"]
    )
    assert measured["reply_rate"] == 0.65
    assert outcome is CheckOutcome.PARTIAL


def test_g5_unanswered_criticism(raw_audit_full: RawAudit, with_place: Edit) -> None:
    reviews = [_review("2026-09-01", rating=1), _review("2026-09-02", rating=2, reply="Sorry")]
    outcome, measured = run("g5", with_place(raw_audit_full, recent_reviews=reviews), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["negative_reviews"] == 2
    happy = with_place(raw_audit_full, recent_reviews=[_review("2026-09-01")])
    assert run("g5", happy, {})[1]["reason"] == "no_negative_reviews"
    ignored = [_review("2026-09-01", rating=1), _review("2026-09-02", reply="Danke")]
    assert run("g5", with_place(raw_audit_full, recent_reviews=ignored), {})[0] is (
        CheckOutcome.FAILED
    )


def test_g4_and_g5_without_reply_data_are_partial(
    raw_audit_full: RawAudit, with_place: Edit
) -> None:
    reviews = [_review("2026-09-01", rating=1), _review("2026-09-02")]
    raw = with_place(raw_audit_full, recent_reviews=reviews)
    outcome, measured = run("g4", raw, THRESHOLDS["g4"])
    assert outcome is CheckOutcome.PARTIAL
    assert measured["reply_data_available"] is False
    assert "reply_rate" not in measured
    outcome, measured = run("g5", raw, {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured == {
        "negative_reviews": 1,
        "reply_data_available": False,
        "tone_checked": False,
        "review_source": "places_api",
        "route": "places_api",
    }


ReviewFactory = Callable[[str, int, str | None], dict[str, object]]


def _with_owner_reviews(
    raw: RawAudit, access: AccessSnapshot, reviews: list[dict[str, object]]
) -> RawAudit:
    return raw.model_copy(update={"access": access.model_copy(update={"owner_reviews": reviews})})


def test_g4_and_g5_prefer_the_profile_reviews(
    raw_audit_full: RawAudit, access_snapshot: AccessSnapshot, make_owner_review: ReviewFactory
) -> None:
    reviews = [
        make_owner_review("2026-09-01", 5, "2026-09-02"),
        make_owner_review("2026-09-03", 1, None),
        make_owner_review("2026-09-05", 4, "2026-09-20"),
        make_owner_review("2026-09-07", 5, None),
    ]
    raw = _with_owner_reviews(raw_audit_full, access_snapshot, reviews)
    outcome, measured = run("g4", raw, THRESHOLDS["g4"])
    assert outcome is CheckOutcome.FAILED
    assert measured["review_source"] == "profile"
    assert measured["reviews_in_sample"] == 4
    assert measured["reply_rate"] == 0.5
    assert measured["average_reply_days"] == 8.0
    outcome, measured = run("g5", raw, {})
    assert outcome is CheckOutcome.FAILED
    assert measured["review_source"] == "profile"
    without_place = raw.model_copy(update={"place": None})
    assert run("g4", without_place, THRESHOLDS["g4"])[1]["review_source"] == "profile"


def test_g4_falls_back_to_places_when_profile_has_no_reviews(
    raw_audit_full: RawAudit, access_snapshot: AccessSnapshot
) -> None:
    raw = _with_owner_reviews(raw_audit_full, access_snapshot, [])
    outcome, measured = run("g4", raw, THRESHOLDS["g4"])
    assert outcome is CheckOutcome.PASSED
    assert measured["review_source"] == "places_api"


def test_reply_data_available_counts_empty_reply_fields() -> None:
    assert reply_data_available([{"rating": 5, "ownerResponse": ""}]) is True
    assert reply_data_available([{"rating": 5, "text": "Gut"}]) is False
    assert reply_data_available([]) is False


def test_g6_keywords_in_review_texts(raw_audit_full: RawAudit, with_place: Edit) -> None:
    reviews = [
        _review("2026-09-01", text="Toll in Münster"),
        _review("2026-09-01"),
        _review("2026-09-01"),
    ]
    assert run("g6", with_place(raw_audit_full, recent_reviews=reviews), {})[0] is (
        CheckOutcome.PARTIAL
    )
    plain = [_review("2026-09-01")]
    assert run("g6", with_place(raw_audit_full, recent_reviews=plain), {})[0] is CheckOutcome.FAILED
    empty = [_review("2026-09-01", text="")]
    assert run("g6", with_place(raw_audit_full, recent_reviews=empty), {})[1]["reason"] == (
        "no_review_texts"
    )


def test_g7_review_platform_missing(raw_audit_full: RawAudit) -> None:
    hits = [
        hit.model_copy(update={"found": False}) if hit.directory == "ProvenExpert" else hit
        for hit in raw_audit_full.directories
    ]
    raw = raw_audit_full.model_copy(update={"directories": hits})
    assert run("g7", raw, {}) == (
        CheckOutcome.FAILED,
        {"found_on": [], "checked": ["ProvenExpert"]},
    )


def test_g8_suspicious_patterns(raw_audit_full: RawAudit, with_place: Edit) -> None:
    perfect = with_place(raw_audit_full, rating=5.0)
    outcome, measured = run("g8", perfect, {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["flags"] == ["perfect_rating_many_reviews"]
    empty_five = [_review("2026-09-01", text="") for _ in range(5)]
    both = with_place(raw_audit_full, rating=5.0, recent_reviews=empty_five)
    assert run("g8", both, {})[0] is CheckOutcome.FAILED


def test_g3_g4_g5_measure_the_newest_browser_reviews(
    raw_audit_full: RawAudit, with_place: Edit
) -> None:
    """Reviews read on Google Maps carry an empty reply field when unanswered."""
    unanswered: dict[str, object] = {**_review("2026-10-10", rating=2), "reviewReply": None}
    answered = {
        **_review("2026-10-01"),
        "reviewReply": {"text": "Danke", "publishTime": "2026-10-03T10:00:00Z"},
    }
    raw = with_place(
        raw_audit_full,
        recent_reviews=[unanswered, answered],
        value_routes={"recent_reviews": "browser"},
    )

    outcome, measured = run("g4", raw, THRESHOLDS["g4"])
    assert outcome is CheckOutcome.FAILED
    assert measured["reply_data_available"] is True
    assert measured["reply_rate"] == 0.5
    assert measured["review_source"] == "browser"
    assert measured["route"] == "browser"
    assert run("g5", raw, {})[0] is CheckOutcome.FAILED
    assert run("g3", raw, THRESHOLDS["g3"])[1]["review_source"] == "browser"


def test_g1_names_the_routes_of_customer_and_competitors(raw_audit_full: RawAudit) -> None:
    rival = raw_audit_full.competitor_places[0].model_copy(
        update={"value_routes": {"review_count": "browser"}}
    )
    raw = raw_audit_full.model_copy(
        update={"competitor_places": [rival, *raw_audit_full.competitor_places[1:]]}
    )

    measured = run("g1", raw, {})[1]

    assert measured["routes"] == ["browser", "places_api"]


def test_profile_reviews_carry_no_route(raw_audit_full: RawAudit) -> None:
    owner_reviews: list[dict[str, object]] = [_review("2026-10-01", reply={"text": "Danke"})]
    access = AccessSnapshot(
        owner_reviews=owner_reviews, collected_at=datetime(2026, 10, 16, tzinfo=UTC)
    )
    measured = run("g4", raw_audit_full.model_copy(update={"access": access}), THRESHOLDS["g4"])[1]
    assert measured["review_source"] == "profile"
    assert "route" not in measured


def _listed(name: str, reviews: int | None, **extra: object) -> ListedBusiness:
    return ListedBusiness(name=name, review_count=reviews, **extra)


def _with_list(raw: RawAudit, businesses: list[ListedBusiness], route: str = "browser") -> RawAudit:
    results = MapResultList(phrase="Dachdecker Münster", businesses=businesses, route=route)
    return raw.model_copy(update={"map_results": results})


def test_g1_compares_with_every_business_on_the_maps_list(
    raw_audit_full: RawAudit, with_place: Edit
) -> None:
    """All listed businesses count, not only three; the customer is left out by its mark."""
    businesses = [
        _listed("Dach Schmidt", 84),
        _listed("Dachdeckerei Muster", 120, is_customer=True),
        _listed("Bedachungen Meier", 95),
        _listed("Bau Krause", 60),
        _listed("Dach Neu", 0),
        _listed("Zimmerei Lüttke", 41),
    ]
    raw = _with_list(with_place(raw_audit_full, review_count=56), businesses)
    outcome, measured = run("g1", raw, THRESHOLDS["g1"])
    assert outcome is CheckOutcome.PASSED
    assert measured == {
        "customer_reviews": 56,
        "competitors": {
            "Dach Schmidt": 84,
            "Bedachungen Meier": 95,
            "Bau Krause": 60,
            "Dach Neu": 0,
            "Zimmerei Lüttke": 41,
        },
        "competitor_average": 56.0,
        "compared_businesses": 5,
        "comparison_source": "maps_result_list",
        "phrase": "Dachdecker Münster",
        # Own count from the fixture profile (Places API), the list read in the browser.
        "routes": ["browser", "places_api"],
    }
    below = _with_list(with_place(raw_audit_full, review_count=44), businesses)
    assert run("g1", below, THRESHOLDS["g1"])[0] is CheckOutcome.FAILED


def test_g1_recognises_the_customer_by_place_id_or_name(
    raw_audit_full: RawAudit, with_place: Edit
) -> None:
    businesses = [
        _listed("Muster Dach", 500, place_id="place-customer"),
        _listed("Dachdeckerei Muster GmbH", 500),
        _listed("Dach Schmidt", 84),
        _listed("Ohne Zahl", None),
    ]
    raw = _with_list(raw_audit_full, businesses, route="places_api")
    outcome, measured = run("g1", raw, THRESHOLDS["g1"])
    assert outcome is CheckOutcome.PASSED
    assert measured["competitors"] == {"Dach Schmidt": 84}
    assert measured["without_review_count"] == ["Ohne Zahl"]
    # The fixture profile carries no recorded routes and counts as read by the Places API.
    assert measured["route"] == "places_api"


def test_g1_keeps_two_businesses_of_the_same_name(raw_audit_full: RawAudit) -> None:
    businesses = [_listed("Dach Schmidt", 10), _listed("Dach Schmidt", 30)]
    measured = run("g1", _with_list(raw_audit_full, businesses), THRESHOLDS["g1"])[1]
    assert measured["competitors"] == {"Dach Schmidt": 10, "Dach Schmidt (2)": 30}
    assert measured["competitor_average"] == 20.0


def test_g1_list_without_others_or_counts_is_not_measurable(raw_audit_full: RawAudit) -> None:
    only_customer = _with_list(raw_audit_full, [_listed("Dachdeckerei Muster", 120)])
    assert run("g1", only_customer, THRESHOLDS["g1"]) == (
        CheckOutcome.NOT_MEASURABLE,
        {"reason": "no_listed_businesses", "phrase": "Dachdecker Münster"},
    )
    uncounted = _with_list(raw_audit_full, [_listed("Dach Schmidt", None)])
    assert run("g1", uncounted, THRESHOLDS["g1"]) == (
        CheckOutcome.NOT_MEASURABLE,
        {"reason": "no_review_counts", "without_review_count": ["Dach Schmidt"]},
    )


def test_g4_lists_the_newest_unanswered_reviews(raw_audit_full: RawAudit, with_place: Edit) -> None:
    long_text = "Das Dach ist dicht. " * 20
    reviews = [
        _review("2026-08-01", rating=5, text="Alt und ohne Antwort"),
        _review("2026-09-20", rating=4, text="Beantwortet", reply="Danke"),
        _review("2026-09-18", rating=2, text=long_text),
        {"rating": 3, "text": "Ohne Datum", "relativePublishTimeDescription": "vor 5 Monaten"},
        _review("2026-09-10", rating=1, text="  Termin geplatzt  "),
        _review("2026-07-01", rating=5, text="Zu alt für die Liste"),
    ]
    measured = run("g4", with_place(raw_audit_full, recent_reviews=reviews), THRESHOLDS["g4"])[1]
    assert measured["unanswered_examples"] == [
        {"date": "2026-09-18", "stars": 2, "text": long_text.strip()[:160]},
        {"date": "2026-09-10", "stars": 1, "text": "Termin geplatzt"},
        {"date": "2026-08-01", "stars": 5, "text": "Alt und ohne Antwort"},
    ]
    assert len(str(measured["unanswered_examples"][0]["text"])) == 160  # type: ignore[index]


def test_g4_examples_fall_back_to_the_relative_date(
    raw_audit_full: RawAudit, with_place: Edit
) -> None:
    reviews = [
        _review("2026-09-20", reply="Danke"),
        {"rating": 4.5, "text": {"text": "Gut"}, "relativePublishTimeDescription": "vor 5 Monaten"},
        {"rating": None, "text": "", "reviewReply": None},
    ]
    measured = run("g4", with_place(raw_audit_full, recent_reviews=reviews), THRESHOLDS["g4"])[1]
    assert measured["unanswered_examples"] == [
        {"date": "vor 5 Monaten", "stars": 4.5, "text": "Gut"},
        {"date": None, "stars": None, "text": ""},
    ]


def test_g4_full_audit_has_no_unanswered_examples(raw_audit_full: RawAudit) -> None:
    assert run("g4", raw_audit_full, THRESHOLDS["g4"])[1]["unanswered_examples"] == []
