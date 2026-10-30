"""Tests for the PageSpeed Insights collector.

Context: Unit tests with mocked external calls. | Input: Fixtures. | Output: Assertions.
"""

import httpx
import pytest
import respx
from pydantic import HttpUrl, SecretStr

from radarauditor.collectors import pagespeed
from radarauditor.errors import CollectionError, TransientCollectionError

URL = HttpUrl("https://www.example.de/")
KEY = SecretStr("google-key")

pytestmark = pytest.mark.usefixtures("fast_retries")


def _lighthouse(performance: float, accessibility: float = 0.9) -> dict[str, object]:
    return {
        "categories": {
            "performance": {"score": performance},
            "accessibility": {"score": accessibility},
        },
        "audits": {
            "largest-contentful-paint": {"numericValue": 3120.4},
            "cumulative-layout-shift": {"numericValue": 0.1234},
        },
    }


def _respond(request: httpx.Request) -> httpx.Response:
    strategy = request.url.params["strategy"]
    if strategy == "mobile":
        body = {
            "lighthouseResult": _lighthouse(0.42, 0.87),
            "loadingExperience": {
                "metrics": {
                    "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2900},
                    "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 7},
                    "INTERACTION_TO_NEXT_PAINT": {"percentile": 210},
                }
            },
        }
    else:
        body = {"lighthouseResult": _lighthouse(0.91)}
    return httpx.Response(200, json=body)


@respx.mock
async def test_measure_merges_mobile_and_desktop_with_field_data() -> None:
    """`measure` merges mobile and desktop with field data."""
    route = respx.get(pagespeed.PAGESPEED_ENDPOINT).mock(side_effect=_respond)

    snapshot = await pagespeed.measure(URL, KEY)

    assert snapshot.mobile_performance == 42
    assert snapshot.desktop_performance == 91
    assert snapshot.accessibility == 87
    assert snapshot.lcp_ms == 2900
    assert snapshot.cls == 0.07
    assert snapshot.inp_ms == 210
    request = route.calls[0].request
    assert request.url.params.get_list("category") == ["performance", "accessibility"]
    assert request.headers["X-Goog-Api-Key"] == "google-key"
    assert "key=" not in str(request.url)


@respx.mock
async def test_measure_falls_back_to_lab_values() -> None:
    """`measure` falls back to lab values."""
    respx.get(pagespeed.PAGESPEED_ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"lighthouseResult": _lighthouse(0.5), "loadingExperience": {}}
        )
    )

    snapshot = await pagespeed.measure(URL, KEY)

    assert snapshot.lcp_ms == 3120
    assert snapshot.cls == 0.123
    assert snapshot.inp_ms is None


@respx.mock
async def test_measure_retries_rate_limits_then_raises_transient() -> None:
    """`measure` retries rate limits then raises transient."""
    route = respx.get(pagespeed.PAGESPEED_ENDPOINT).mock(return_value=httpx.Response(429))

    with pytest.raises(TransientCollectionError):
        await pagespeed.measure(URL, KEY)

    assert route.call_count == 6


@respx.mock
async def test_measure_recovers_after_server_error() -> None:
    """`measure` recovers after server error."""
    responses = iter([httpx.Response(503), httpx.Response(503)])

    def flaky(request: httpx.Request) -> httpx.Response:
        return next(responses, None) or _respond(request)

    respx.get(pagespeed.PAGESPEED_ENDPOINT).mock(side_effect=flaky)

    snapshot = await pagespeed.measure(URL, KEY)

    assert snapshot.desktop_performance == 91


@pytest.mark.parametrize("status", [400, 403])
@respx.mock
async def test_measure_raises_collection_error_on_rejected_request(status: int) -> None:
    """`measure` raises collection error on rejected request."""
    respx.get(pagespeed.PAGESPEED_ENDPOINT).mock(
        return_value=httpx.Response(status, json={"error": {"message": "API key not valid"}})
    )

    with pytest.raises(CollectionError, match="API key not valid") as raised:
        await pagespeed.measure(URL, KEY)

    assert not isinstance(raised.value, TransientCollectionError)


@respx.mock
async def test_measure_raises_on_missing_score() -> None:
    """`measure` raises on missing score."""
    body = {"lighthouseResult": {"categories": {"performance": {"score": None}}}}
    respx.get(pagespeed.PAGESPEED_ENDPOINT).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(CollectionError, match="performance"):
        await pagespeed.measure(URL, KEY)


@respx.mock
async def test_measure_raises_on_malformed_answer() -> None:
    """`measure` raises on malformed answer."""
    respx.get(pagespeed.PAGESPEED_ENDPOINT).mock(return_value=httpx.Response(200, text="<html>"))

    with pytest.raises(CollectionError, match="malformed"):
        await pagespeed.measure(URL, KEY)
