"""Tests for forecast.

Context: Pure calculations against the real config/prognose.yaml and config/branchen.yaml. |
Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from radarauditor.db.models import Keyword, Ranking
from radarauditor.errors import ConfigurationError
from radarauditor.industries import load_industries
from radarauditor.reporting.forecast import (
    Forecast,
    best_positions,
    click_rate,
    enquiry_gain,
    estimate,
    load_curves,
    report_forecast,
    value_of_forecast,
)


def test_curves_turn_ranges_into_bands(report_forecast_path: Path) -> None:
    """Keys like 6-10 become bands; "none" is dropped."""
    curves = load_curves(report_forecast_path)

    assert click_rate(curves.organic_ctr, 1) == (0.25, 0.35)
    assert click_rate(curves.organic_ctr, 7) == (0.02, 0.04)
    assert click_rate(curves.organic_ctr, 20) == (0.005, 0.015)
    assert click_rate(curves.organic_ctr, 21) == (0.0, 0.0)
    assert click_rate(curves.local_pack_ctr, 4) == (0.0, 0.0)
    assert click_rate(curves.organic_ctr, None) == (0.0, 0.0)


def test_estimate_adds_organic_and_local_clicks(
    report_keywords: list[Keyword], report_rankings: list[Ranking], report_forecast_path: Path
) -> None:
    """Hand-calculated ranges for one measured keyword with volume and one without.

    Primary: 880 searches, organic 2 (12-18 %) plus local 1 (15-25 %), default rate 3-8 %:
    now 7.128 to 30.272, unchanged after because both positions beat target 3.
    Second: fallback 80-300 searches, organic 5 (4-6 %): now 0.096 to 1.44; after organic 3
    (8-12 %) plus local 3 (7-10 %): 0.36 to 5.28.
    """
    forecast = estimate(
        report_keywords, report_rankings, 3, report_forecast_path, industry="Dachdecker"
    )

    assert forecast.enquiries_now_low == pytest.approx(7.2)
    assert forecast.enquiries_now_high == pytest.approx(31.7)
    assert forecast.enquiries_after_low == pytest.approx(7.5)
    assert forecast.enquiries_after_high == pytest.approx(35.6)
    assert forecast.keywords_counted == 2


def test_estimate_names_every_fallback(
    report_keywords: list[Keyword], report_rankings: list[Ranking], report_forecast_path: Path
) -> None:
    """Unknown industry, unknown volume and unmeasured keyword each get a sentence."""
    forecast = estimate(
        report_keywords, report_rankings, 3, report_forecast_path, industry="Dachdecker"
    )
    text = "\n".join(forecast.assumptions)

    assert "„Dachdecker“ liegt keine eigene Anfragequote vor" in text
    assert "3 bis 8 Prozent" in text
    assert "Für „Dachsanierung Münster“ ist kein Suchvolumen bekannt" in text
    assert "80 bis 300 Suchen" in text
    assert "mittelgroße Stadt" in text
    assert "„Dachfenster Münster“ wurde in diesem Audit nicht gemessen" in text
    assert "Platz 3" in text


def test_known_industry_uses_its_rate(
    report_keywords: list[Keyword], report_rankings: list[Ranking], report_forecast_path: Path
) -> None:
    """A group id gets its own rate and is named by its label."""
    forecast = estimate(
        report_keywords, report_rankings, 3, report_forecast_path, industry="bauhandwerk"
    )
    text = "\n".join(forecast.assumptions)

    assert "keine eigene Anfragequote" not in text
    assert "Von 100 Besuchern stellen 5 bis 10 eine Anfrage (Branche: Bauhandwerk)" in text
    assert forecast.enquiries_now_low == pytest.approx(
        round(880 * 0.27 * 0.05 + 80 * 0.04 * 0.05, 1)
    )


@pytest.mark.parametrize(
    ("stored", "label", "rate"),
    [
        (" Handwerk ", "Bauhandwerk", "5 bis 10"),
        ("medizin", "Arzt und Zahnarzt", "8 bis 15"),
        ("Recht und Steuern", "Recht, Steuern, Beratung", "4 bis 8"),
        ("recht_steuern", "Recht, Steuern, Beratung", "4 bis 8"),
        ("Immobilien", "Handel und Dienstleistung", "3 bis 8"),
        ("Gastronomie", "Gastronomie", "3 bis 6"),
        ("kfz", "Kfz", "4 bis 8"),
        ("Haustechnik", "Haustechnik", "6 bis 10"),
    ],
)
def test_older_values_and_labels_map_to_groups(
    report_keywords: list[Keyword],
    report_rankings: list[Ranking],
    report_forecast_path: Path,
    stored: str,
    label: str,
    rate: str,
) -> None:
    """Industries saved before the groups existed, and group labels, get the group's rate."""
    forecast = estimate(report_keywords, report_rankings, 3, report_forecast_path, industry=stored)

    assert f"stellen {rate} eine Anfrage (Branche: {label})" in "\n".join(forecast.assumptions)


def test_every_group_has_its_own_rate(report_forecast_path: Path) -> None:
    """prognose.yaml names a rate for each group of branchen.yaml."""
    curves = load_curves(report_forecast_path)
    groups = load_industries(report_forecast_path.with_name("branchen.yaml"))

    assert set(curves.enquiry_rate_by_industry) == groups.ids | {"default"}


def test_rate_for_unknown_group_is_rejected(tmp_path: Path, report_forecast_path: Path) -> None:
    """A rate keyed by a retired industry stops the forecast instead of being ignored."""
    content = yaml.safe_load(report_forecast_path.read_text(encoding="utf-8"))
    content["enquiry_rate_by_industry"]["handwerk"] = [0.05, 0.1]
    curves = tmp_path / "prognose.yaml"
    curves.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")
    industries = report_forecast_path.with_name("branchen.yaml")

    with pytest.raises(ConfigurationError, match="unknown industry groups: handwerk"):
        estimate([], [], 3, curves, industries=industries)


def test_city_size_changes_the_volume_fallback(
    report_keywords: list[Keyword], report_rankings: list[Ranking], report_forecast_path: Path
) -> None:
    """A large city assumes more searches for keywords without volume."""
    medium = estimate(report_keywords, report_rankings, 3, report_forecast_path)
    large = estimate(
        report_keywords, report_rankings, 3, report_forecast_path, city_size="large_city"
    )

    assert large.enquiries_after_high > medium.enquiries_after_high
    assert any("300 bis 1.200 Suchen" in line and "Großstadt" in line for line in large.assumptions)


def test_competitor_rankings_do_not_count(
    report_keywords: list[Keyword],
    report_make_ranking: Callable[..., Ranking],
    report_forecast_path: Path,
) -> None:
    """Only subject "customer" is the customer's own position."""
    rows = [report_make_ranking(report_keywords[0].id, "Dach Schmidt", 1, 1)]

    forecast = estimate(report_keywords, rows, 3, report_forecast_path)

    assert forecast.keywords_counted == 0
    assert forecast.enquiries_after_high == 0


def test_not_listed_today_reaches_target_after(
    report_keywords: list[Keyword],
    report_make_ranking: Callable[..., Ranking],
    report_forecast_path: Path,
) -> None:
    """A keyword measured but not listed gives zero now and the target rate afterwards."""
    rows = [report_make_ranking(report_keywords[0].id, "customer", None, None)]

    forecast = estimate(report_keywords[:1], rows, 1, report_forecast_path)

    assert forecast.enquiries_now_high == 0
    assert forecast.enquiries_after_low == pytest.approx(round(880 * 0.40 * 0.03, 1))


def test_best_positions_prefer_central_measurement(
    report_rankings: list[Ranking], report_keywords: list[Keyword]
) -> None:
    """Grid points do not override the measurement at the location."""
    positions = best_positions(report_rankings)

    assert positions[report_keywords[0].id] == (2, 1)
    assert positions[report_keywords[1].id] == (5, None)


def test_target_position_must_be_positive(report_forecast_path: Path) -> None:
    """Position 0 does not exist."""
    with pytest.raises(ValueError, match="target_position"):
        estimate([], [], 0, report_forecast_path)


def test_missing_curves_file(tmp_path: Path) -> None:
    """A missing file is a configuration error."""
    with pytest.raises(ConfigurationError, match="cannot be read"):
        load_curves(tmp_path / "fehlt.yaml")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("organic_ctr", {"1": [0.3]}),
        ("organic_ctr", {"eins": [0.1, 0.2]}),
        ("organic_ctr", {"3-1": [0.1, 0.2]}),
        ("enquiry_rate_by_industry", {"bauhandwerk": [0.05, 0.1]}),
    ],
)
def test_malformed_curves(
    tmp_path: Path, report_forecast_path: Path, key: str, value: object
) -> None:
    """Bad bands and a missing default rate are rejected."""
    content = yaml.safe_load(report_forecast_path.read_text(encoding="utf-8"))
    content[key] = value
    broken = tmp_path / "prognose.yaml"
    broken.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="malformed"):
        load_curves(broken)


def forecast_of(now: tuple[float, float], after: tuple[float, float]) -> Forecast:
    """A forecast with the given ranges."""
    return Forecast(
        enquiries_now_low=now[0],
        enquiries_now_high=now[1],
        enquiries_after_low=after[0],
        enquiries_after_high=after[1],
        assumptions=[],
    )


def test_value_of_forecast() -> None:
    """Gains 2 and 6 enquiries, 25 % close rate, 2,000 euro order value."""
    forecast = forecast_of((1.0, 4.0), (3.0, 10.0))

    assert value_of_forecast(forecast, 2000.0, 0.25) == (1000.0, 3000.0)


def test_value_of_forecast_never_negative() -> None:
    """A forecast below today yields no revenue gain."""
    assert value_of_forecast(forecast_of((5.0, 9.0), (4.0, 8.0)), 1000.0, 0.5) == (0.0, 0.0)


@pytest.mark.parametrize(("order_value", "close_rate"), [(-1.0, 0.5), (100.0, 1.5), (100.0, -0.1)])
def test_value_of_forecast_rejects_bad_inputs(order_value: float, close_rate: float) -> None:
    """Negative order values and close rates outside 0 to 1 are errors."""
    with pytest.raises(ValueError):
        value_of_forecast(forecast_of((0, 0), (0, 0)), order_value, close_rate)


def test_enquiry_gain_is_after_minus_now() -> None:
    """Gains of the low and the high scenario."""
    assert enquiry_gain(forecast_of((1.0, 4.0), (3.0, 10.0))) == (2.0, 6.0)


def test_enquiry_gain_is_ordered_and_never_negative() -> None:
    """A loss counts as zero and the range is sorted."""
    assert enquiry_gain(forecast_of((5.0, 1.0), (4.0, 4.0))) == (0.0, 3.0)


def test_report_forecast_uses_report_target_and_city_size(
    report_keywords: list[Keyword], report_rankings: list[Ranking], report_forecast_path: Path
) -> None:
    """Same result as estimate with the report's target position and city size."""
    curves = load_curves(report_forecast_path)
    expected = estimate(
        report_keywords,
        report_rankings,
        curves.report_target_position,
        report_forecast_path,
        industry="haustechnik",
        city_size=curves.report_city_size,
    )

    forecast = report_forecast(
        "haustechnik", report_keywords, report_rankings, report_forecast_path
    )

    assert forecast == expected
