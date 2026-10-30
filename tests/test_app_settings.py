"""Tests for the settings rows shared by the app and the worker.

Context: Pure model tests. | Input: JSON values as the app writes them. | Output: Assertions.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from radarauditor.db.app_settings import (
    DEFAULT_SOURCES,
    WORKER_SETTINGS_KEYS,
    Source,
    SourcesSettings,
    WorkerCapabilities,
    WorkerHeartbeat,
    parse_worker_settings,
)
from radarauditor.errors import ConfigurationError


def test_missing_rows_give_the_defaults() -> None:
    settings = parse_worker_settings({})

    assert all(settings.sources.of(source).enabled for source in Source)
    assert settings.sources.places.daily_cap == 500
    assert settings.sources.dataforseo.monthly_cost_cap_eur == Decimal(50)
    assert settings.sources.chatgpt.monthly_cost_cap_eur is None


def test_null_rows_give_the_defaults() -> None:
    assert parse_worker_settings({"sources": None}).sources == SourcesSettings()


def test_partial_source_keeps_the_other_defaults() -> None:
    settings = parse_worker_settings(
        {"sources": {"places": {"enabled": True}, "gemini": {"daily_cap": 5}, "x": {}}}
    )

    assert settings.sources.places.enabled is True
    assert settings.sources.places.daily_cap == DEFAULT_SOURCES[Source.PLACES].daily_cap
    assert settings.sources.gemini.enabled is True
    assert settings.sources.gemini.daily_cap == 5


def test_only_the_sources_row_is_read() -> None:
    assert WORKER_SETTINGS_KEYS == ("sources",)
    assert parse_worker_settings({"disabled_checks": "a2"}) == parse_worker_settings({})


def test_every_source_takes_a_monthly_cost_cap() -> None:
    settings = parse_worker_settings({"sources": {"claude": {"monthly_cost_cap_eur": 12.5}}})

    assert settings.sources.claude.monthly_cost_cap_eur == Decimal("12.5")


@pytest.mark.parametrize(
    "values",
    [
        {"sources": []},
        {"sources": {"places": {"daily_cap": -1}}},
        {"sources": {"places": True}},
        {"sources": {"dataforseo": {"monthly_cost_cap_eur": -5}}},
    ],
)
def test_malformed_rows_raise(values: dict[str, object]) -> None:
    with pytest.raises(ConfigurationError, match="malformed"):
        parse_worker_settings(values)


def test_heartbeat_round_trips_as_json() -> None:
    heartbeat = WorkerHeartbeat(
        at=datetime(2026, 10, 23, 8, 0, tzinfo=UTC),
        version="0.1.0",
        capabilities=WorkerCapabilities(
            chromium=True,
            claude_cli=True,
            claude_logged_in=False,
            browser_profile=True,
            analysis_available=False,
            analysis_reason="Claude Code ist nicht angemeldet",
            mail_reading_available=False,
            mail_reading_reason="Claude Code ist nicht angemeldet",
        ),
    )

    dumped = heartbeat.model_dump(mode="json")

    assert dumped == {
        "at": "2026-10-23T08:00:00Z",
        "version": "0.1.0",
        "capabilities": {
            "chromium": True,
            "claude_cli": True,
            "claude_logged_in": False,
            "browser_profile": True,
            "analysis_available": False,
            "analysis_reason": "Claude Code ist nicht angemeldet",
            "mail_reading_available": False,
            "mail_reading_reason": "Claude Code ist nicht angemeldet",
        },
    }
    assert WorkerHeartbeat.model_validate(dumped) == heartbeat
