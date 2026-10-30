"""Tests for catalog.

Context: Unit tests with mocked external calls. | Input: Fixtures. | Output: Assertions.
"""

from pathlib import Path

import pytest
import yaml

from radarauditor.errors import ScoringError
from radarauditor.scoring.catalog import (
    Catalogue,
    checks_for_context,
    find_check,
    load_catalogue,
)


def _check(check_id: str, source: str = "crawler", **overrides: object) -> dict[str, object]:
    check: dict[str, object] = {
        "id": check_id,
        "name": f"Check {check_id}",
        "why": "Weil.",
        "source": source,
        "requires_paid": source == "paid",
        "requires_access": source == "access",
        "thresholds": {"limit": 3},
        "customer_question": "Frage?",
        "customer_impact": "Wirkung.",
    }
    check.update(overrides)
    return check


def _document() -> dict[str, object]:
    return {
        "areas": [
            {"id": "a", "name": "A", "weight": 60, "checks": [_check("a1"), _check("a2", "paid")]},
            {"id": "b", "name": "B", "weight": 40, "checks": [_check("b1", "manual")]},
            {"id": "m", "name": "M", "weight": 0, "checks": [_check("m1", "access")]},
        ]
    }


def _write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "katalog.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def test_real_catalogue_loads(catalogue: Catalogue) -> None:
    assert sum(area.weight for area in catalogue.areas) == 100
    assert find_check(catalogue, "a2").thresholds["lcp_ms_max"] == 2500
    assert find_check(catalogue, "m1").area == "m"
    assert all(check.area == area.id for area in catalogue.areas for check in area.checks)


def test_minimal_catalogue_loads_with_area_ids(tmp_path: Path) -> None:
    catalogue = load_catalogue(_write(tmp_path, _document()))
    assert [area.id for area in catalogue.areas] == ["a", "b", "m"]
    assert find_check(catalogue, "b1").area == "b"


def test_weights_not_100_raise(tmp_path: Path) -> None:
    document = _document()
    document["areas"][0]["weight"] = 50  # type: ignore[index]
    with pytest.raises(ScoringError, match="sum to 90"):
        load_catalogue(_write(tmp_path, document))


def test_duplicate_id_raises(tmp_path: Path) -> None:
    document = _document()
    document["areas"][0]["checks"].append(_check("a1"))  # type: ignore[index]
    with pytest.raises(ScoringError, match="twice"):
        load_catalogue(_write(tmp_path, document))


@pytest.mark.parametrize("bad_id", ["x1", "a", "a1b", "A1"])
def test_malformed_id_raises(tmp_path: Path, bad_id: str) -> None:
    document = _document()
    document["areas"][0]["checks"][0]["id"] = bad_id  # type: ignore[index]
    with pytest.raises(ScoringError):
        load_catalogue(_write(tmp_path, document))


def test_check_in_wrong_area_raises(tmp_path: Path) -> None:
    document = _document()
    document["areas"][1]["checks"][0]["id"] = "c1"  # type: ignore[index]
    with pytest.raises(ScoringError, match="filed under area b"):
        load_catalogue(_write(tmp_path, document))


def test_unknown_source_raises(tmp_path: Path) -> None:
    document = _document()
    document["areas"][0]["checks"][0]["source"] = "guesswork"  # type: ignore[index]
    with pytest.raises(ScoringError, match="unknown source"):
        load_catalogue(_write(tmp_path, document))


def test_paid_flag_disagreeing_with_source_raises(tmp_path: Path) -> None:
    document = _document()
    document["areas"][0]["checks"][1]["requires_paid"] = False  # type: ignore[index]
    with pytest.raises(ScoringError, match="requires_paid"):
        load_catalogue(_write(tmp_path, document))


def test_negative_weight_raises(tmp_path: Path) -> None:
    document = _document()
    document["areas"][2]["weight"] = -10  # type: ignore[index]
    document["areas"][1]["weight"] = 50  # type: ignore[index]
    with pytest.raises(ScoringError, match="negative"):
        load_catalogue(_write(tmp_path, document))


def test_missing_field_raises(tmp_path: Path) -> None:
    document = _document()
    del document["areas"][0]["checks"][0]["customer_question"]  # type: ignore[index]
    with pytest.raises(ScoringError, match="malformed"):
        load_catalogue(_write(tmp_path, document))


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "katalog.yaml"
    path.write_text('areas:\n  - id: a\n    name: "Zitat" danach\n', encoding="utf-8")
    with pytest.raises(ScoringError, match="not valid YAML"):
        load_catalogue(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ScoringError, match="Cannot read"):
        load_catalogue(tmp_path / "fehlt.yaml")


def test_document_without_areas_raises(tmp_path: Path) -> None:
    with pytest.raises(ScoringError, match="no list of areas"):
        load_catalogue(_write(tmp_path, {"bereiche": []}))


def test_find_check_unknown_raises(tmp_path: Path) -> None:
    catalogue = load_catalogue(_write(tmp_path, _document()))
    with pytest.raises(ScoringError, match="z9"):
        find_check(catalogue, "z9")


@pytest.mark.parametrize(
    ("paid", "access", "expected"),
    [
        (False, False, ["a1", "b1"]),
        (True, False, ["a1", "a2", "b1"]),
        (False, True, ["a1", "b1", "m1"]),
        (True, True, ["a1", "a2", "b1", "m1"]),
    ],
)
def test_checks_for_context(tmp_path: Path, paid: bool, access: bool, expected: list[str]) -> None:
    catalogue = load_catalogue(_write(tmp_path, _document()))
    assert [check.id for check in checks_for_context(catalogue, paid, access)] == expected
