"""Tests for industries.

Context: The real config/branchen.yaml and broken copies of it. | Input: Fixtures. |
Output: Assertions.
"""

from pathlib import Path

import pytest
import yaml

from radarauditor.errors import ConfigurationError
from radarauditor.industries import IndustryCatalogue, load_industries

INDUSTRIES_FILE = Path(__file__).resolve().parents[1] / "config" / "branchen.yaml"
GROUP_IDS = [
    "bauhandwerk",
    "haustechnik",
    "holz_innenausbau",
    "kfz",
    "friseur_kosmetik",
    "gastronomie",
    "baeckerei_lebensmittel",
    "arzt_zahnarzt",
    "therapie_pflege",
    "recht_steuern_beratung",
    "fitness_sport",
    "handel_dienstleistung",
]


@pytest.fixture
def industries() -> IndustryCatalogue:
    """The real industry groups."""
    return load_industries(INDUSTRIES_FILE)


def test_twelve_groups_in_order(industries: IndustryCatalogue) -> None:
    """The app lists the groups in the order of the file."""
    assert [group.id for group in industries.groups] == GROUP_IDS


@pytest.mark.parametrize(
    ("stored", "group_id"),
    [
        ("haustechnik", "haustechnik"),
        (" Haustechnik ", "haustechnik"),
        ("Recht, Steuern, Beratung", "recht_steuern_beratung"),
        ("handwerk", "bauhandwerk"),
        ("Handwerk", "bauhandwerk"),
        ("medizin", "arzt_zahnarzt"),
        ("recht_steuern", "recht_steuern_beratung"),
        ("Recht und Steuern", "recht_steuern_beratung"),
        ("immobilien", "handel_dienstleistung"),
        ("gastronomie", "gastronomie"),
        ("Kfz", "kfz"),
    ],
)
def test_resolve_ids_labels_and_older_values(
    industries: IndustryCatalogue, stored: str, group_id: str
) -> None:
    """Ids, labels and values saved before the groups existed all find their group."""
    group = industries.resolve(stored)

    assert group is not None
    assert group.id == group_id


def test_values_outside_every_group_stay_as_stored(industries: IndustryCatalogue) -> None:
    """An unknown industry has no group and is shown and asked about as stored."""
    assert industries.resolve("Sonstiges") is None
    assert industries.label(" Sonstiges ") == "Sonstiges"
    assert industries.question_term("Dachdecker") == "Dachdecker"


def test_label_and_question_term_of_a_group(industries: IndustryCatalogue) -> None:
    """Older values are labelled and asked about like their group."""
    assert industries.label("medizin") == "Arzt und Zahnarzt"
    assert industries.label("holz_innenausbau") == "Holz und Innenausbau"
    assert industries.question_term("haustechnik") == "Haustechnikbetrieb"
    assert industries.question_term("Handwerk") == "Handwerksbetrieb"


def test_missing_file(tmp_path: Path) -> None:
    """A missing file is a configuration error."""
    with pytest.raises(ConfigurationError, match="cannot be read"):
        load_industries(tmp_path / "fehlt.yaml")


def _broken(tmp_path: Path, change: dict[str, object]) -> Path:
    content = yaml.safe_load(INDUSTRIES_FILE.read_text(encoding="utf-8"))
    content.update(change)
    path = tmp_path / "branchen.yaml"
    path.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"legacy": {"handwerk": "zimmerei"}}, "unknown groups: zimmerei"),
        ({"legacy": {"Handwerk": "bauhandwerk"}}, "lower case: Handwerk"),
        ({"groups": []}, "groups"),
    ],
)
def test_malformed_groups(tmp_path: Path, change: dict[str, object], reason: str) -> None:
    """Legacy values pointing nowhere, upper-case legacy keys and no groups are rejected."""
    with pytest.raises(ConfigurationError, match=reason):
        load_industries(_broken(tmp_path, change))


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    """Two groups with the same id would make stored values ambiguous."""
    content = yaml.safe_load(INDUSTRIES_FILE.read_text(encoding="utf-8"))
    groups = content["groups"]
    path = _broken(tmp_path, {"groups": [*groups, groups[0]]})

    with pytest.raises(ConfigurationError, match="unique"):
        load_industries(path)


# Real names of the place types of the Places API (New), tables A and B of
# https://developers.google.com/maps/documentation/places/web-service/place-types, as far as
# config/branchen.yaml uses them.
DOCUMENTED_GOOGLE_TYPES = frozenset(
    {
        "roofing_contractor", "painter", "electrician", "plumber", "furniture_store",
        "car_repair", "car_dealer", "tire_shop", "car_wash", "auto_parts_store", "truck_dealer",
        "hair_salon", "hair_care", "barber_shop", "beauty_salon", "beautician", "nail_salon",
        "makeup_artist", "tanning_studio", "restaurant", "cafe", "coffee_shop", "bar", "pub",
        "bistro", "beer_garden", "wine_bar", "cocktail_bar", "brewpub", "gastropub",
        "fast_food_restaurant", "pizza_restaurant", "ice_cream_shop", "meal_takeaway",
        "catering_service", "bakery", "butcher_shop", "cake_shop", "pastry_shop",
        "confectionery", "deli", "farmers_market", "food_store", "health_food_store",
        "grocery_store", "supermarket", "dentist", "dental_clinic", "doctor", "medical_clinic",
        "medical_center", "physiotherapist", "chiropractor", "massage", "foot_care", "lawyer",
        "accounting", "insurance_agency", "consultant", "gym", "fitness_center", "yoga_studio",
        "sports_club", "sports_school", "sports_coaching", "real_estate_agency", "laundry",
        "moving_company", "locksmith", "florist", "travel_agency", "tailor", "courier_service",
        "clothing_store", "shoe_store", "jewelry_store", "book_store", "gift_shop",
        "electronics_store", "cell_phone_store", "hardware_store", "pet_store", "bicycle_store",
    }
)  # fmt: skip


def test_every_group_names_documented_google_types(industries: IndustryCatalogue) -> None:
    """Each group has Google categories, all of them real names and none in two groups."""
    used = [google_type for group in industries.groups for google_type in group.google_types]

    assert all(group.google_types for group in industries.groups)
    assert set(used) <= DOCUMENTED_GOOGLE_TYPES
    assert len(used) == len(set(used))


@pytest.mark.parametrize(
    ("types", "group_id"),
    [
        (["roofing_contractor", "point_of_interest"], "bauhandwerk"),
        (["point_of_interest", "plumber"], "haustechnik"),
        (["car_repair"], "kfz"),
        (["barber_shop"], "friseur_kosmetik"),
        (["cafe", "bakery"], "gastronomie"),
        (["bakery", "cafe"], "baeckerei_lebensmittel"),
        (["dentist"], "arzt_zahnarzt"),
        (["physiotherapist"], "therapie_pflege"),
        (["lawyer"], "recht_steuern_beratung"),
        (["yoga_studio"], "fitness_sport"),
        (["real_estate_agency"], "handel_dienstleistung"),
        (["furniture_store"], "holz_innenausbau"),
    ],
)
def test_google_types_find_the_group(
    industries: IndustryCatalogue, types: list[str], group_id: str
) -> None:
    """The first category that belongs to a group decides, the primary one comes first."""
    group = industries.by_google_types(types)

    assert group is not None and group.id == group_id


def test_google_types_outside_every_group(industries: IndustryCatalogue) -> None:
    """Generic categories name no group."""
    assert industries.by_google_types(["point_of_interest", "establishment"]) is None
    assert industries.by_google_types([]) is None


@pytest.mark.parametrize(
    ("texts", "group_id"),
    [
        (["Dachdeckerei Hollenbeck GmbH"], "bauhandwerk"),
        (["Elektro Brinkmann"], "haustechnik"),
        (["Barbier Ali"], "friseur_kosmetik"),
        (["Bar Celona"], "gastronomie"),
        (["Zahnarztpraxis Dr. Meier"], "arzt_zahnarzt"),
        (["Kanzlei am Markt"], "recht_steuern_beratung"),
        (["Betrieb Müller", "Autohaus"], "kfz"),
    ],
)
def test_words_of_name_and_category_find_the_group(
    industries: IndustryCatalogue, texts: list[str], group_id: str
) -> None:
    """Examples, services and label parts at the start of a word name the group."""
    group = industries.by_words(texts)

    assert group is not None and group.id == group_id


@pytest.mark.parametrize("texts", [["Barbara Moden"], ["Müller & Söhne"], [""], []])
def test_words_name_no_group(industries: IndustryCatalogue, texts: list[str]) -> None:
    """Short words count only whole ("Barbara" is no "Bar"); unknown names give no group."""
    assert industries.by_words(texts) is None


def test_words_tied_between_groups_give_no_group(industries: IndustryCatalogue) -> None:
    """A name with equally long words of two groups is not guessed."""
    assert industries.by_words(["Holz Café"]) is None


def test_google_type_in_two_groups_is_rejected(tmp_path: Path) -> None:
    """A category may decide for one group only."""
    content = yaml.safe_load(INDUSTRIES_FILE.read_text(encoding="utf-8"))
    content["groups"][1]["google_types"] = [*content["groups"][1]["google_types"], "painter"]

    with pytest.raises(ConfigurationError, match="more than one group: painter"):
        load_industries(_broken(tmp_path, {"groups": content["groups"]}))
