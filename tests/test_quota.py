"""Tests for the source gate.

Context: Unit tests with an in-memory quota counter. | Input: SourcesSettings. | Output: Assertions.
"""

from decimal import Decimal

import pytest

from radarauditor.collectors.quota import SourceGate
from radarauditor.db.app_settings import Source, SourcesSettings
from radarauditor.errors import (
    CollectionError,
    DailyCapReachedError,
    MonthlyCostCapReachedError,
    SourceDisabledError,
    StorageError,
)


class Counter:
    """Allows a fixed number of uses per source and records every call."""

    def __init__(self, allowed: int) -> None:
        """Start with the number of allowed uses."""
        self.allowed = allowed
        self.calls: list[tuple[str, int]] = []

    def consume(self, source: str, cap: int) -> bool:
        """Count one use."""
        self.calls.append((source, cap))
        return len(self.calls) <= self.allowed


def test_acquire_counts_with_the_configured_cap() -> None:
    counter = Counter(allowed=5)
    sources = SourcesSettings.model_validate({"chatgpt": {"daily_cap": 3}})
    gate = SourceGate(sources, counter.consume)

    gate.acquire(Source.CHATGPT)

    assert counter.calls == [("chatgpt", 3)]
    assert gate.exhausted == frozenset()


PLACES_OFF = SourcesSettings.model_validate({"places": {"enabled": False}})


def test_disabled_source_is_refused_without_counting() -> None:
    counter = Counter(allowed=5)
    gate = SourceGate(PLACES_OFF, counter.consume)

    with pytest.raises(SourceDisabledError, match="places: switched off") as raised:
        gate.acquire(Source.PLACES)

    assert raised.value.source == "places"
    assert isinstance(raised.value, CollectionError)
    assert counter.calls == []
    assert gate.try_acquire(Source.PLACES) is False
    assert gate.is_enabled(Source.PLACES) is False


def test_exhausted_source_is_refused_without_asking_again() -> None:
    counter = Counter(allowed=1)
    gate = SourceGate(SourcesSettings(), counter.consume)

    assert gate.try_acquire(Source.GEMINI) is True
    with pytest.raises(DailyCapReachedError, match="daily cap of 200 reached"):
        gate.acquire(Source.GEMINI)
    assert gate.try_acquire(Source.GEMINI) is False

    assert len(counter.calls) == 2
    assert gate.exhausted == {Source.GEMINI}


def test_acquire_up_to_grants_until_the_cap() -> None:
    gate = SourceGate(SourcesSettings(), Counter(allowed=2).consume)

    assert gate.acquire_up_to(Source.GOOGLE_SEARCH, 5) == 2
    assert gate.acquire_up_to(Source.GOOGLE_SEARCH, 5) == 0
    assert gate.exhausted == {Source.GOOGLE_SEARCH}


def test_acquire_up_to_raises_for_a_disabled_source() -> None:
    gate = SourceGate(PLACES_OFF, Counter(allowed=2).consume)

    with pytest.raises(SourceDisabledError):
        gate.acquire_up_to(Source.PLACES, 2)


def test_counter_errors_propagate() -> None:
    def broken(source: str, cap: int) -> bool:
        raise StorageError("consume quota of claude failed: offline")

    gate = SourceGate(SourcesSettings(), broken)

    with pytest.raises(StorageError, match="offline"):
        gate.try_acquire(Source.CLAUDE)


class Ledger:
    """Monthly spend in memory that counts how often it was read."""

    def __init__(self, spent: Decimal) -> None:
        """Start with the spend of the month so far."""
        self.spent = spent
        self.reads = 0
        self.added: list[tuple[str, Decimal]] = []

    def month_source_cost(self, source: str) -> Decimal:
        """Return the month's spend."""
        self.reads += 1
        return self.spent

    def add_source_cost(self, source: str, cost: Decimal) -> Decimal:
        """Add one request's cost and return the month's spend."""
        self.added.append((source, cost))
        self.spent += cost
        return self.spent


def _capped_sources(cap: str) -> SourcesSettings:
    return SourcesSettings.model_validate({"dataforseo": {"monthly_cost_cap_eur": cap}})


def test_cost_cap_reads_the_spend_once_and_follows_the_recorded_costs() -> None:
    ledger = Ledger(Decimal("0.90"))
    counter = Counter(allowed=10)
    gate = SourceGate(_capped_sources("1"), counter.consume, ledger)

    gate.acquire(Source.DATAFORSEO)
    gate.record_cost(Source.DATAFORSEO, Decimal("0.05"))
    gate.acquire(Source.DATAFORSEO)
    gate.record_cost(Source.DATAFORSEO, Decimal("0.05"))

    with pytest.raises(MonthlyCostCapReachedError, match="monthly cost cap of 1 EUR reached"):
        gate.acquire(Source.DATAFORSEO)
    assert ledger.reads == 1
    assert ledger.added == [("dataforseo", Decimal("0.05")), ("dataforseo", Decimal("0.05"))]
    assert len(counter.calls) == 2
    assert gate.cost_capped == {Source.DATAFORSEO}
    assert gate.exhausted == frozenset()
    assert gate.try_acquire(Source.DATAFORSEO) is False


def test_spent_month_is_refused_before_counting() -> None:
    counter = Counter(allowed=10)
    gate = SourceGate(_capped_sources("50"), counter.consume, Ledger(Decimal(50)))

    assert gate.acquire_up_to(Source.DATAFORSEO, 3) == 0
    assert counter.calls == []
    assert gate.cost_capped == {Source.DATAFORSEO}


def test_sources_without_cost_cap_or_ledger_ignore_costs() -> None:
    ledger = Ledger(Decimal(1000))
    gate = SourceGate(SourcesSettings(), Counter(allowed=10).consume, ledger)

    gate.acquire(Source.GOOGLE_SEARCH)
    gate.record_cost(Source.DATAFORSEO, Decimal(0))
    without_ledger = SourceGate(_capped_sources("0"), Counter(allowed=10).consume)
    without_ledger.acquire(Source.DATAFORSEO)
    without_ledger.record_cost(Source.DATAFORSEO, Decimal(5))

    assert ledger.reads == 0
    assert ledger.added == []
    assert without_ledger.cost_capped == frozenset()
