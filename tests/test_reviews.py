"""Tests for picking the unanswered reviews an audit read.

Context: Pure function over RawAudit. | Input: Sample raw audits. | Output: Assertions.
"""

from datetime import UTC, datetime

from radarauditor.collectors.snapshot import AccessSnapshot, PlaceSnapshot, RawAudit
from radarauditor.reviews import unanswered_reviews

COLLECTED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _review(
    day: str | None, text: str, *, reply: object = None, **extra: object
) -> dict[str, object]:
    return {
        "publishTime": f"{day}T10:00:00Z" if day else None,
        "relativePublishTimeDescription": "vor einem Monat",
        "rating": 4,
        "text": text,
        "reviewReply": reply,
        **extra,
    }


def _place(reviews: list[dict[str, object]], route: str = "browser") -> PlaceSnapshot:
    return PlaceSnapshot(
        place_id="ChIJ-own",
        name="Dachdeckerei Muster",
        formatted_address="Hiltruper Str. 210, 48165 Münster",
        phone=None,
        website=None,
        primary_type=None,
        types=[],
        rating=4.5,
        review_count=len(reviews),
        has_opening_hours=True,
        photo_count=3,
        recent_reviews=reviews,
        business_status="OPERATIONAL",
        value_routes={"recent_reviews": route},  # type: ignore[dict-item]
    )


def _raw(place: PlaceSnapshot | None, access: AccessSnapshot | None = None) -> RawAudit:
    return RawAudit(
        website=None,
        pagespeed=None,
        place=place,
        competitor_places=[],
        serps=[],
        directories=[],
        assistant_answers=[],
        collected_at=COLLECTED,
        warnings=[],
        access=access,
    )


def test_unanswered_reviews_with_text_come_first_newest_first() -> None:
    raw = _raw(
        _place(
            [
                _review("2026-08-01", "Alt"),
                _review("2026-09-20", "", rating=5),
                _review("2026-09-10", "Neu"),
                _review("2026-09-15", "Beantwortet", reply={"text": "Danke"}),
            ]
        )
    )

    picked = unanswered_reviews(raw, limit=5)

    assert picked.reply_data_available is True
    assert picked.source == "browser"
    assert [review.text for review in picked.reviews] == ["Neu", "Alt", ""]
    assert picked.reviews[0].date_text == "10.09.2026"
    assert picked.reviews[0].stars == 4


def test_the_limit_keeps_the_newest() -> None:
    reviews = [_review(f"2026-09-{day:02d}", f"T{day}") for day in range(1, 9)]

    picked = unanswered_reviews(_raw(_place(reviews)), limit=5)

    assert [review.text for review in picked.reviews] == ["T8", "T7", "T6", "T5", "T4"]


def test_reviews_from_the_places_api_cannot_tell_whether_they_are_answered() -> None:
    reviews = [{"publishTime": "2026-09-01T10:00:00Z", "rating": 2, "text": "Schlecht"}]

    picked = unanswered_reviews(_raw(_place(reviews, route="places_api")), limit=5)

    assert picked.reply_data_available is False
    assert picked.reviews == []


def test_reviews_read_with_access_carry_their_id() -> None:
    access = AccessSnapshot(
        owner_reviews=[_review("2026-09-01", "Gut", reviewId="r-1")], collected_at=COLLECTED
    )

    picked = unanswered_reviews(_raw(None, access), limit=5)

    assert picked.source == "profile"
    assert picked.reviews[0].review_id == "r-1"


def test_since_keeps_only_reviews_after_the_previous_audit() -> None:
    reviews = [_review("2026-09-01", "Vorher"), _review("2026-09-25", "Neu"), _review(None, "?")]

    picked = unanswered_reviews(_raw(_place(reviews)), since=datetime(2026, 9, 10, tzinfo=UTC))

    assert [review.text for review in picked.reviews] == ["Neu"]


def test_a_review_without_date_shows_googles_relative_date() -> None:
    picked = unanswered_reviews(_raw(_place([_review(None, "Ohne Datum")])), limit=5)

    assert picked.reviews[0].date_text == "vor einem Monat"


def test_an_audit_without_profile_has_no_reviews() -> None:
    picked = unanswered_reviews(_raw(None), limit=5)

    assert (picked.reply_data_available, picked.reviews, picked.source) == (False, [], None)
