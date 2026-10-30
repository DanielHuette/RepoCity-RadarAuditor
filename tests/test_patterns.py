"""Tests for the shared text and address patterns of the collectors.

Context: Pure function tests. | Input: Literal strings and URLs. | Output: Assertions.
"""

import pytest
from pydantic import HttpUrl

from radarauditor.collectors.patterns import host_of


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.Dachdecker-Hollenbeck.de/impressum", "dachdecker-hollenbeck.de"),
        (HttpUrl("https://www.elektro-brinkmann.de/"), "elektro-brinkmann.de"),
        ("overmann.de", "overmann.de"),
        ("//www.overmann.de/kontakt", "overmann.de"),
        ("localhost", None),
        ("", None),
        (None, None),
    ],
)
def test_host_of_gives_the_host_without_www(
    url: str | HttpUrl | None, expected: str | None
) -> None:
    assert host_of(url) == expected
