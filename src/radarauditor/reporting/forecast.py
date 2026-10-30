"""Estimate the range of additional enquiries after optimisation.

Context: Volume per keyword times click-through by position times enquiry rate, always as a low-high
range and labelled as an estimate. Curves live in config/prognose.yaml, enquiry rates keyed by the
industry groups of config/branchen.yaml. A keyword earns clicks from the organic results and from
the local pack, so both click-through rates are added.
Input: Keywords with volumes and positions, the customer's industry.
Output: Forecast model.
"""

import logging
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from radarauditor.db.models import Keyword, Ranking
from radarauditor.errors import ConfigurationError
from radarauditor.industries import INDUSTRIES_FILE, IndustryCatalogue, load_industries
from radarauditor.rendering import german_number

logger = logging.getLogger(__name__)

CitySize = Literal["small_city", "medium_city", "large_city"]
CUSTOMER_SUBJECT = "customer"
DEFAULT_INDUSTRY = "default"
NO_POSITION_KEY = "none"
RESULT_PLACES = 1
PERCENT = 100

Range = tuple[float, float]


class PositionBand(BaseModel):
    """Click-through range for positions first to last, both inclusive."""

    first: int = Field(ge=1)
    last: int = Field(ge=1)
    low: float = Field(ge=0, le=1)
    high: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "PositionBand":
        if self.last < self.first or self.high < self.low:
            raise ValueError(f"band {self.first}-{self.last} is not ordered")
        return self


class AssumptionTexts(BaseModel):
    """German sentences with placeholders, one per assumption kind."""

    click_rates: str
    target_position: str
    enquiry_rate: str
    enquiry_rate_fallback: str
    volume_fallback: str
    unmeasured_keyword: str


class ForecastCurves(BaseModel):
    """Content of config/prognose.yaml."""

    organic_ctr: list[PositionBand]
    local_pack_ctr: list[PositionBand]
    enquiry_rate_by_industry: dict[str, Range]
    volume_fallback_when_unknown: dict[CitySize, tuple[int, int]]
    report_target_position: int = Field(ge=1)
    report_city_size: CitySize
    city_size_labels: dict[CitySize, str]
    assumption_texts: AssumptionTexts

    @field_validator("organic_ctr", "local_pack_ctr", mode="before")
    @classmethod
    def _bands(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return [_band(key, rates) for key, rates in value.items() if key != NO_POSITION_KEY]

    @model_validator(mode="after")
    def _has_default_rate(self) -> "ForecastCurves":
        if DEFAULT_INDUSTRY not in self.enquiry_rate_by_industry:
            raise ValueError("enquiry_rate_by_industry needs a 'default' entry")
        return self


def _band(key: object, rates: object) -> dict[str, object]:
    """Turn a YAML entry such as `6-10: [0.02, 0.04]` into band fields."""
    first_text, _, last_text = str(key).partition("-")
    if not isinstance(rates, list) or len(rates) != 2:
        raise ValueError(f"position {key}: expected [low, high]")
    try:
        first = int(first_text)
        last = int(last_text) if last_text else first
    except ValueError as exc:
        raise ValueError(f"position {key!r} is neither a number nor a range") from exc
    return {"first": first, "last": last, "low": rates[0], "high": rates[1]}


class Forecast(BaseModel):
    """Monthly enquiries today and after optimisation, as ranges."""

    enquiries_now_low: float
    enquiries_now_high: float
    enquiries_after_low: float
    enquiries_after_high: float
    assumptions: list[str]
    keywords_counted: int = 0


def load_curves(path: Path) -> ForecastCurves:
    """Read and validate the forecast configuration.

    Args:
        path: Location of prognose.yaml.

    Returns:
        Parsed curves, rates and wording.

    Raises:
        ConfigurationError: If the file cannot be read or is malformed.
    """
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"forecast curves {path} cannot be read: {exc}") from exc
    try:
        return ForecastCurves.model_validate(content)
    except ValidationError as exc:
        raise ConfigurationError(f"forecast curves {path} are malformed: {exc}") from exc


def click_rate(bands: list[PositionBand], position: int | None) -> Range:
    """Return the click-through range of a position; positions outside every band get zero.

    Args:
        bands: Organic or local pack curve.
        position: Position on the results page, None if not listed.

    Returns:
        Low and high click-through rate.
    """
    if position is None:
        return 0.0, 0.0
    for band in bands:
        if band.first <= position <= band.last:
            return band.low, band.high
    return 0.0, 0.0


def best_positions(rankings: list[Ranking]) -> dict[UUID, tuple[int | None, int | None]]:
    """Best organic and local pack position of the customer per keyword.

    Measurements at the location itself (no grid point) win over grid measurements.

    Args:
        rankings: Rankings of one audit for every subject.

    Returns:
        Organic and local pack position per keyword id; None where not listed.
    """
    own = [ranking for ranking in rankings if ranking.subject == CUSTOMER_SUBJECT]
    by_keyword: dict[UUID, list[Ranking]] = defaultdict(list)
    for ranking in own:
        by_keyword[ranking.keyword_id].append(ranking)
    positions: dict[UUID, tuple[int | None, int | None]] = {}
    for keyword_id, rows in by_keyword.items():
        central = [row for row in rows if row.grid_point is None] or rows
        positions[keyword_id] = (
            _best(row.organic_position for row in central),
            _best(row.local_pack_position for row in central),
        )
    return positions


def _best(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _improved(position: int | None, target: int) -> int:
    return target if position is None else min(position, target)


def _clicks(curves: ForecastCurves, organic: int | None, local: int | None) -> Range:
    organic_low, organic_high = click_rate(curves.organic_ctr, organic)
    local_low, local_high = click_rate(curves.local_pack_ctr, local)
    return organic_low + local_low, organic_high + local_high


def check_rate_keys(curves: ForecastCurves, industries: IndustryCatalogue) -> None:
    """Every enquiry rate belongs to an industry group or is the default.

    Args:
        curves: Parsed prognose.yaml.
        industries: Parsed branchen.yaml.

    Raises:
        ConfigurationError: If a rate is keyed by something other than a group id or "default".
    """
    known = industries.ids | {DEFAULT_INDUSTRY}
    unknown = sorted(key for key in curves.enquiry_rate_by_industry if key not in known)
    if unknown:
        raise ConfigurationError(f"enquiry rates for unknown industry groups: {', '.join(unknown)}")


def _enquiry_rate(
    curves: ForecastCurves, industries: IndustryCatalogue, industry: str
) -> tuple[Range, str]:
    texts = curves.assumption_texts
    rates = curves.enquiry_rate_by_industry
    group = industries.resolve(industry)
    label = industries.label(industry)
    if group is not None and group.id in rates:
        low, high = rates[group.id]
        return (low, high), texts.enquiry_rate.format(**_percent(low, high), industry=label)
    low, high = rates[DEFAULT_INDUSTRY]
    note = texts.enquiry_rate_fallback.format(**_percent(low, high), industry=label)
    return (low, high), note


def _load(curves: Path, industries: Path | None) -> tuple[ForecastCurves, IndustryCatalogue]:
    config = load_curves(curves)
    groups = load_industries(industries or curves.with_name(INDUSTRIES_FILE))
    check_rate_keys(config, groups)
    return config, groups


def _percent(low: float, high: float) -> dict[str, str]:
    return {"low": _plain(low * PERCENT), "high": _plain(high * PERCENT)}


def _plain(value: float) -> str:
    places = 0 if math.isclose(value, round(value)) else 1
    return german_number(value, places)


def estimate(
    keywords: list[Keyword],
    rankings: list[Ranking],
    target_position: int,
    curves: Path,
    *,
    industry: str = DEFAULT_INDUSTRY,
    city_size: CitySize = "medium_city",
    industries: Path | None = None,
) -> Forecast:
    """Compute the forecast; keywords without volume use the city-size fallback range.

    Args:
        keywords: Tracked keywords of the customer.
        rankings: Rankings of the audit; only subject "customer" counts.
        target_position: Position assumed after optimisation; better positions are kept.
        curves: Location of prognose.yaml.
        industry: Customer industry as stored: a group id, a group label or an older value
            such as "handwerk"; values outside every group get the default rate.
        city_size: Size class for the volume fallback.
        industries: Location of branchen.yaml; defaults to the file next to the curves.

    Returns:
        Enquiries per month now and after optimisation, with every assumption in German.

    Raises:
        ValueError: If target_position is smaller than 1.
        ConfigurationError: If the curves or the industry groups are missing or malformed.
    """
    if target_position < 1:
        raise ValueError(f"target_position must be at least 1, got {target_position}")
    config, groups = _load(curves, industries)
    texts = config.assumption_texts
    rate, rate_note = _enquiry_rate(config, groups, industry)
    assumptions = [texts.click_rates, texts.target_position.format(target=target_position)]
    assumptions.append(rate_note)
    positions = best_positions(rankings)
    totals = [0.0, 0.0, 0.0, 0.0]
    counted = 0
    for keyword in keywords:
        if keyword.id not in positions:
            assumptions.append(texts.unmeasured_keyword.format(phrase=keyword.phrase))
            continue
        volume = _volume(keyword, config, city_size, assumptions)
        enquiries = _enquiries(config, volume, positions[keyword.id], rate, target_position)
        totals = [total + part for total, part in zip(totals, enquiries, strict=True)]
        counted += 1
    logger.info("Forecast over %d of %d keywords", counted, len(keywords))
    return Forecast(
        enquiries_now_low=round(totals[0], RESULT_PLACES),
        enquiries_now_high=round(totals[1], RESULT_PLACES),
        enquiries_after_low=round(totals[2], RESULT_PLACES),
        enquiries_after_high=round(totals[3], RESULT_PLACES),
        assumptions=assumptions,
        keywords_counted=counted,
    )


def _enquiries(
    config: ForecastCurves,
    volume: tuple[int, int],
    position: tuple[int | None, int | None],
    rate: Range,
    target_position: int,
) -> tuple[float, float, float, float]:
    """Enquiries of one keyword: now low and high, after optimisation low and high."""
    volume_low, volume_high = volume
    rate_low, rate_high = rate
    organic, local = position
    now = _clicks(config, organic, local)
    after = _clicks(config, _improved(organic, target_position), _improved(local, target_position))
    return (
        volume_low * now[0] * rate_low,
        volume_high * now[1] * rate_high,
        volume_low * after[0] * rate_low,
        volume_high * after[1] * rate_high,
    )


def _volume(
    keyword: Keyword, config: ForecastCurves, city_size: CitySize, assumptions: list[str]
) -> tuple[int, int]:
    if keyword.monthly_volume is not None:
        return keyword.monthly_volume, keyword.monthly_volume
    low, high = config.volume_fallback_when_unknown[city_size]
    assumptions.append(
        config.assumption_texts.volume_fallback.format(
            phrase=keyword.phrase,
            low=german_number(low, 0),
            high=german_number(high, 0),
            city_size=config.city_size_labels[city_size],
        )
    )
    return low, high


def value_of_forecast(
    forecast: Forecast, order_value_eur: float, close_rate: float
) -> tuple[float, float]:
    """Translate the enquiry range into a monthly revenue range for the sales talk.

    The additional enquiries are the gains of the low and the high scenario (after minus now,
    never negative); each is multiplied by the share of enquiries that become orders and by the
    average order value.

    Args:
        forecast: Result of estimate.
        order_value_eur: Average net order value in euro.
        close_rate: Share of enquiries that become orders, between 0 and 1.

    Returns:
        Low and high additional revenue per month in euro, rounded to cents.

    Raises:
        ValueError: If the order value is negative or the close rate is outside 0 to 1.
    """
    if order_value_eur < 0:
        raise ValueError(f"order value must not be negative, got {order_value_eur}")
    if not 0 <= close_rate <= 1:
        raise ValueError(f"close rate must be between 0 and 1, got {close_rate}")
    low, high = enquiry_gain(forecast)
    factor = order_value_eur * close_rate
    return round(low * factor, 2), round(high * factor, 2)


def enquiry_gain(forecast: Forecast) -> Range:
    """Additional enquiries per month an optimisation brings, as an ordered range.

    The gains of the low and the high scenario (after minus now) are never negative and are
    sorted, so low never exceeds high.

    Args:
        forecast: Result of estimate.

    Returns:
        Low and high additional enquiries per month.
    """
    gain_low = max(0.0, forecast.enquiries_after_low - forecast.enquiries_now_low)
    gain_high = max(0.0, forecast.enquiries_after_high - forecast.enquiries_now_high)
    low, high = sorted((gain_low, gain_high))
    return low, high


def report_forecast(
    industry: str,
    keywords: list[Keyword],
    rankings: list[Ranking],
    curves: Path,
    industries: Path | None = None,
) -> Forecast:
    """Run estimate with the target position and city size the report uses.

    Args:
        industry: Customer industry.
        keywords: Tracked keywords of the customer.
        rankings: Rankings of the audit.
        curves: Location of prognose.yaml.
        industries: Location of branchen.yaml; defaults to the file next to the curves.

    Returns:
        The forecast as printed in the sales report.

    Raises:
        ConfigurationError: If the curves or the industry groups are missing or malformed.
    """
    config = load_curves(curves)
    return estimate(
        keywords,
        rankings,
        config.report_target_position,
        curves,
        industry=industry,
        city_size=config.report_city_size,
        industries=industries,
    )
