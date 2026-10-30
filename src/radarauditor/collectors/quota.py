"""Gate every external request through the source switches, daily caps and monthly cost caps.

Context: One gate per job; collectors ask it before each request. The count lives in table
`source_usage` and is increased atomically by the database function `consume_source_quota`, so
parallel jobs cannot overrun a cap. Paid sources with a monthly_cost_cap_eur also report what each
request cost; the gate adds it to `source_usage` through `add_source_cost` and refuses further
requests once the month's spend reaches the cap. Requests already running when the cap is reached
still finish, so the spend can exceed the cap by at most their cost.
Input: SourcesSettings, a function that consumes one unit of a source's daily quota and, for cost
caps, a CostLedger.
Output: Permission per request; the sources whose daily or monthly cap ran out during the job.
"""

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

from radarauditor.db.app_settings import Source, SourcesSettings
from radarauditor.errors import (
    DailyCapReachedError,
    MonthlyCostCapReachedError,
    SourceDisabledError,
)

logger = logging.getLogger(__name__)

QuotaConsumer = Callable[[str, int], bool]


class CostLedger(Protocol):
    """Spend per source and calendar month (Europe/Berlin), e.g. Repositories."""

    def month_source_cost(self, source: str) -> Decimal:
        """Return what the source cost so far this month, in euros."""
        ...

    def add_source_cost(self, source: str, cost: Decimal) -> Decimal:
        """Add the cost of one request and return the month's spend after it, in euros."""
        ...


class SourceGate:
    """Switches and caps of every source for one job."""

    def __init__(
        self, sources: SourcesSettings, consume: QuotaConsumer, ledger: CostLedger | None = None
    ) -> None:
        """Bind the settings to the quota counter and the cost ledger.

        Args:
            sources: Source switches and caps read at the start of the job.
            consume: Counts one use of a source against a cap and answers whether it was allowed,
                e.g. Repositories.consume_source_quota.
            ledger: Records request costs; without it monthly cost caps are not enforced.
        """
        self._sources = sources
        self._consume = consume
        self._ledger = ledger
        self._exhausted: set[Source] = set()
        self._cost_capped: set[Source] = set()
        self._month_cost: dict[Source, Decimal] = {}

    @property
    def exhausted(self) -> frozenset[Source]:
        """Sources whose daily cap ran out during this job."""
        return frozenset(self._exhausted)

    @property
    def cost_capped(self) -> frozenset[Source]:
        """Sources whose monthly cost cap was reached during this job."""
        return frozenset(self._cost_capped)

    def is_enabled(self, source: Source) -> bool:
        """Return whether the source is switched on."""
        return self._sources.of(source).enabled

    def acquire(self, source: Source) -> None:
        """Count one request of a source; call it right before the request.

        Raises:
            SourceDisabledError: If the source is switched off.
            MonthlyCostCapReachedError: If the month's spend reached the monthly cost cap.
            DailyCapReachedError: If the daily cap is used up.
            StorageError: If the counter or the spend cannot be read or updated.
        """
        setting = self._sources.of(source)
        if not setting.enabled:
            raise SourceDisabledError(source.value, "switched off in the app settings")
        self._check_cost_cap(source)
        if source in self._exhausted or not self._consume(source.value, setting.daily_cap):
            self._mark_exhausted(source, setting.daily_cap)
            raise DailyCapReachedError(source.value, f"daily cap of {setting.daily_cap} reached")

    def try_acquire(self, source: Source) -> bool:
        """Count one request like acquire, answering False instead of raising.

        Raises:
            StorageError: If the counter cannot be updated.
        """
        try:
            self.acquire(source)
        except (SourceDisabledError, DailyCapReachedError, MonthlyCostCapReachedError):
            return False
        return True

    def acquire_up_to(self, source: Source, count: int) -> int:
        """Count up to count requests of a source and return how many were granted.

        Raises:
            SourceDisabledError: If the source is switched off.
            StorageError: If the counter cannot be updated.
        """
        for granted in range(count):
            try:
                self.acquire(source)
            except (DailyCapReachedError, MonthlyCostCapReachedError):
                return granted
        return count

    def record_cost(self, source: Source, cost: Decimal) -> None:
        """Add what one request cost to the month's spend of the source.

        Args:
            source: Source that was asked.
            cost: Cost of the request in euros; zero is not recorded.

        Raises:
            StorageError: If the spend cannot be updated.
        """
        if self._ledger is None or cost <= 0:
            return
        total = self._ledger.add_source_cost(source.value, cost)
        self._month_cost[source] = total
        cap = self._sources.of(source).monthly_cost_cap_eur
        if cap is not None and total >= cap:
            self._mark_cost_capped(source, cap)

    def _check_cost_cap(self, source: Source) -> None:
        cap = self._sources.of(source).monthly_cost_cap_eur
        if cap is None or self._ledger is None:
            return
        if source not in self._month_cost:
            self._month_cost[source] = self._ledger.month_source_cost(source.value)
        if source in self._cost_capped or self._month_cost[source] >= cap:
            self._mark_cost_capped(source, cap)
            raise MonthlyCostCapReachedError(source.value, f"monthly cost cap of {cap} EUR reached")

    def _mark_exhausted(self, source: Source, cap: int) -> None:
        if source not in self._exhausted:
            logger.warning("Daily cap of %d for %s reached", cap, source.value)
        self._exhausted.add(source)

    def _mark_cost_capped(self, source: Source, cap: Decimal) -> None:
        if source not in self._cost_capped:
            logger.warning("Monthly cost cap of %s EUR for %s reached", cap, source.value)
        self._cost_capped.add(source)
