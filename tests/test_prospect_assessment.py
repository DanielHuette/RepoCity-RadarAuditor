"""Tests for ranking the businesses of a customer search by signs of a neglected profile.

Context: Pure functions over config/kundensuche.yaml and listed places. | Input: Listed places.
| Output: Assertions.
"""

from pathlib import Path

import pytest

from radarauditor.collectors.places import ProspectPlace
from radarauditor.errors import ConfigurationError
from radarauditor.prospects.assessment import assess, reason_sentence
from radarauditor.prospects.criteria import ProspectCriteria, load_criteria

CRITERIA_PATH = Path(__file__).resolve().parents[1] / "config" / "kundensuche.yaml"


@pytest.fixture(scope="module")
def criteria() -> ProspectCriteria:
    return load_criteria(CRITERIA_PATH)


def _place(name: str, **changes: object) -> ProspectPlace:
    values: dict[str, object] = {
        "place_id": f"ChIJ-{name}",
        "name": name,
        "formatted_address": "Hiltruper Str. 210, 48165 Münster, Deutschland",
        "phone": "0251 123456",
        "website": f"https://{name.lower()}.example/",
        "maps_url": f"https://maps.google.com/?cid={name}",
        "rating": 4.6,
        "review_count": 40,
        "photo_count": 10,
        "has_opening_hours": True,
        "business_status": "OPERATIONAL",
    }
    values.update(changes)
    return ProspectPlace(**values)  # type: ignore[arg-type]


def test_a_profile_with_every_gap_ranks_first_and_names_each_gap(
    criteria: ProspectCriteria,
) -> None:
    kept = _place("Gepflegt")
    neglected = _place(
        "Verwaist",
        website=None,
        has_opening_hours=False,
        phone=None,
        photo_count=0,
        review_count=0,
        rating=None,
    )

    ranked = assess([kept, neglected, _place("Zweiter")], criteria)

    assert [item.name for item in ranked] == ["Verwaist", "Gepflegt", "Zweiter"]
    first = ranked[0]
    assert first.rank == 1
    assert first.signals == [
        "no_website",
        "no_opening_hours",
        "no_phone",
        "no_photos",
        "no_reviews",
    ]
    assert first.score == 3 + 2 + 1 + 2 + 2
    assert first.reasons == (
        "Keine Webseite im Profil, keine Öffnungszeiten, keine Telefonnummer, keine Fotos und "
        "noch keine Bewertung."
    )


def test_few_reviews_are_measured_against_the_median_of_the_list(
    criteria: ProspectCriteria,
) -> None:
    listed = [_place(f"B{index}", review_count=count) for index, count in enumerate([3, 30, 40])]

    ranked = assess(listed, criteria)

    few = next(item for item in ranked if item.review_count == 3)
    assert few.signals == ["few_reviews"]
    assert (
        few.reasons == "3 Bewertungen, wo die Hälfte der Betriebe in der Liste mindestens 30 hat."
    )
    assert ranked[0] is few


def test_reviews_are_not_compared_when_the_list_has_too_few(criteria: ProspectCriteria) -> None:
    listed = [_place(f"B{index}", review_count=count) for index, count in enumerate([1, 3, 3])]

    assert all(item.signals == [] for item in assess(listed, criteria))


def test_few_photos_name_the_number(criteria: ProspectCriteria) -> None:
    ranked = assess([_place("Wenig", photo_count=2)], criteria)

    assert ranked[0].signals == ["few_photos"]
    assert ranked[0].reasons == "Nur 2 Fotos."


def test_closed_businesses_are_left_out(criteria: ProspectCriteria) -> None:
    listed = [
        _place("Offen"),
        _place("Zu", business_status="CLOSED_PERMANENTLY"),
        _place("Pause", business_status="CLOSED_TEMPORARILY"),
    ]

    assert [item.name for item in assess(listed, criteria)] == ["Offen"]


def test_equal_scores_put_fewer_reviews_first(criteria: ProspectCriteria) -> None:
    listed = [_place("Viele", website=None, review_count=50), _place("Wenige", website=None)]

    assert [item.name for item in assess(listed, criteria)] == ["Wenige", "Viele"]


def test_a_kept_profile_gets_the_sentence_without_gaps(criteria: ProspectCriteria) -> None:
    ranked = assess([_place("Gepflegt")], criteria)

    assert ranked[0].score == 0
    assert ranked[0].reasons == criteria.no_signal_text


def test_the_address_is_split_into_street_postal_code_and_city(criteria: ProspectCriteria) -> None:
    item = assess([_place("Adresse")], criteria)[0]

    assert (item.street, item.postal_code, item.city) == ("Hiltruper Str. 210", "48165", "Münster")


def test_contact_ways_follow_the_data_and_never_offer_a_cold_mail(
    criteria: ProspectCriteria,
) -> None:
    with_phone = assess([_place("Tel")], criteria)[0]
    without = assess([_place("Ohne", phone=None, formatted_address="48165 Münster")], criteria)[0]

    assert [way.channel for way in with_phone.contact_ways] == ["letter", "visit", "phone", "email"]
    assert [(way.channel, way.status) for way in without.contact_ways] == [("email", "forbidden")]
    phone = with_phone.contact_ways[2]
    assert phone.status == "uncertain"
    assert phone.label == "Anruf"


def test_reason_sentence_joins_with_und_and_capitalises() -> None:
    assert reason_sentence(["keine Fotos"], "leer") == "Keine Fotos."
    assert reason_sentence(["a", "b", "c"], "leer") == "A, b und c."
    assert reason_sentence([], "Nichts gefunden.") == "Nichts gefunden."


def test_a_criteria_file_without_a_known_signal_is_rejected(tmp_path: Path) -> None:
    broken = CRITERIA_PATH.read_text(encoding="utf-8").replace("no_phone:", "no_fax:")
    path = tmp_path / "kundensuche.yaml"
    path.write_text(broken, encoding="utf-8")

    with pytest.raises(ConfigurationError, match="kundensuche.yaml"):
        load_criteria(path)


def test_a_missing_criteria_file_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_criteria(tmp_path / "fehlt.yaml")
