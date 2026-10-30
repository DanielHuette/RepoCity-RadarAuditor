"""Measure loading speed and Core Web Vitals through the PageSpeed Insights API.

Context: Free Google API; one call per strategy (mobile, desktop).
Input: URL and API key.
Output: PageSpeedSnapshot.
"""

import asyncio
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, HttpUrl, SecretStr, ValidationError
from pydantic.alias_generators import to_camel

from radarauditor.collectors.snapshot import PageSpeedSnapshot
from radarauditor.collectors.transport import send
from radarauditor.errors import CollectionError

PAGESPEED_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
REQUEST_TIMEOUT_SECONDS = 120
CATEGORIES = ("performance", "accessibility")
FIELD_LCP = "LARGEST_CONTENTFUL_PAINT_MS"
FIELD_CLS = "CUMULATIVE_LAYOUT_SHIFT_SCORE"
FIELD_INP = "INTERACTION_TO_NEXT_PAINT"
LAB_LCP = "largest-contentful-paint"
LAB_CLS = "cumulative-layout-shift"
LAB_INP = "interaction-to-next-paint"
FIELD_CLS_SCALE = 100

Strategy = Literal["mobile", "desktop"]


class _ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class _Category(_ApiModel):
    score: float | None = None


class _LabAudit(_ApiModel):
    numeric_value: float | None = None


class _Lighthouse(_ApiModel):
    categories: dict[str, _Category]
    audits: dict[str, _LabAudit] = {}


class _FieldMetric(_ApiModel):
    percentile: float | None = None


class _LoadingExperience(_ApiModel):
    metrics: dict[str, _FieldMetric] = {}


class _PageSpeedResponse(_ApiModel):
    lighthouse_result: _Lighthouse
    loading_experience: _LoadingExperience | None = None


async def measure(url: HttpUrl, api_key: SecretStr) -> PageSpeedSnapshot:
    """Call PageSpeed Insights for mobile and desktop and merge the results.

    Core Web Vitals and accessibility come from the mobile run because Google indexes the mobile
    version; field data from real visitors is preferred over the lab measurement per metric.

    Args:
        url: Page to measure.
        api_key: Google Cloud key with the PageSpeed Insights API enabled.

    Returns:
        Scores and Core Web Vitals.

    Raises:
        TransientCollectionError: On rate limits or 5xx responses.
        CollectionError: On invalid URL or key.
    """
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        mobile, desktop = await asyncio.gather(
            _run(client, url, api_key, "mobile"),
            _run(client, url, api_key, "desktop"),
        )
    return PageSpeedSnapshot(
        url=url,
        mobile_performance=_category_score(mobile, "performance"),
        desktop_performance=_category_score(desktop, "performance"),
        lcp_ms=round(_required(_lcp_ms(mobile), "largest contentful paint")),
        cls=round(_required(_cls(mobile), "cumulative layout shift"), 3),
        inp_ms=_rounded(_inp_ms(mobile)),
        accessibility=_category_score(mobile, "accessibility"),
    )


async def _run(
    client: httpx.AsyncClient, url: HttpUrl, api_key: SecretStr, strategy: Strategy
) -> _PageSpeedResponse:
    params = [("url", str(url)), ("strategy", strategy)]
    params.extend(("category", category) for category in CATEGORIES)
    response = await send(
        client,
        "GET",
        PAGESPEED_ENDPOINT,
        source=f"PageSpeed Insights ({strategy})",
        params=params,
        headers={"X-Goog-Api-Key": api_key.get_secret_value()},
    )
    try:
        return _PageSpeedResponse.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise CollectionError(f"PageSpeed Insights ({strategy}) answer is malformed") from error


def _category_score(result: _PageSpeedResponse, category: str) -> int:
    entry = result.lighthouse_result.categories.get(category)
    if entry is None or entry.score is None:
        raise CollectionError(f"PageSpeed Insights returned no {category} score")
    return round(entry.score * 100)


def _field(result: _PageSpeedResponse, metric: str) -> float | None:
    if result.loading_experience is None:
        return None
    entry = result.loading_experience.metrics.get(metric)
    return entry.percentile if entry is not None else None


def _lab(result: _PageSpeedResponse, audit: str) -> float | None:
    entry = result.lighthouse_result.audits.get(audit)
    return entry.numeric_value if entry is not None else None


def _lcp_ms(result: _PageSpeedResponse) -> float | None:
    field_value = _field(result, FIELD_LCP)
    return field_value if field_value is not None else _lab(result, LAB_LCP)


def _cls(result: _PageSpeedResponse) -> float | None:
    field_value = _field(result, FIELD_CLS)
    if field_value is not None:
        return field_value / FIELD_CLS_SCALE
    return _lab(result, LAB_CLS)


def _inp_ms(result: _PageSpeedResponse) -> float | None:
    field_value = _field(result, FIELD_INP)
    return field_value if field_value is not None else _lab(result, LAB_INP)


def _required(value: float | None, metric: str) -> float:
    if value is None:
        raise CollectionError(f"PageSpeed Insights returned no {metric}")
    return value


def _rounded(value: float | None) -> int | None:
    return round(value) if value is not None else None
