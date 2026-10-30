"""Tests for the Wayback Machine first-capture collector.

Context: Unit tests with mocked HTTP. | Input: CDX answers. | Output: Assertions.
"""

from datetime import date

import httpx
import pytest
import respx

from radarauditor.collectors import wayback
from radarauditor.errors import CollectionError, TransientCollectionError

pytestmark = pytest.mark.usefixtures("fast_retries")


@respx.mock
async def test_first_capture_asks_for_the_oldest_capture() -> None:
    route = respx.get(wayback.CDX_ENDPOINT).mock(
        return_value=httpx.Response(200, text='[["timestamp"],["20110402093011"]]')
    )

    assert await wayback.first_capture("dachdecker-hollenbeck.de") == date(2011, 4, 2)
    params = route.calls[0].request.url.params
    assert params["url"] == "dachdecker-hollenbeck.de"
    assert params["limit"] == "1"
    assert params["fl"] == "timestamp"
    assert params["output"] == "json"


@pytest.mark.parametrize("body", ["", "[]", '[["timestamp"]]'])
def test_no_capture_is_none(body: str) -> None:
    assert wayback.parse_first_capture(body) is None


@pytest.mark.parametrize("body", ["<html>", '{"a": 1}', '[["timestamp"],[5]]', '[[""],["x"]]'])
def test_malformed_answers_are_collection_errors(body: str) -> None:
    with pytest.raises(CollectionError):
        wayback.parse_first_capture(body)


@respx.mock
async def test_overloaded_archive_is_transient() -> None:
    respx.get(wayback.CDX_ENDPOINT).mock(return_value=httpx.Response(503))

    with pytest.raises(TransientCollectionError, match="Wayback Machine answered HTTP 503"):
        await wayback.first_capture("example.de")
