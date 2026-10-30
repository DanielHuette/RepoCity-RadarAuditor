"""Potential points and expected extra enquiries per check result.

Context: Runs at scoring time right after evaluate. potential_points is how many points the total
score gains when this one check passes, computed with the evaluator's own scoring. The forecast
gain (enquiries after optimisation minus today) assumes every open check is fixed, so it is spread
over the open checks in proportion to their potential; the uplifts of an audit add up to the
forecast gain. Passed, skipped and not measurable checks get zero.
Input: CheckResults, Catalogue, forecast gain as a low-high range.
Output: CheckResults with potential_points, uplift_low and uplift_high set.
"""

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

from radarauditor.db.models import CheckOutcome, CheckResult
from radarauditor.scoring.catalog import Catalogue
from radarauditor.scoring.evaluator import total_fraction

OPEN_OUTCOMES = frozenset({CheckOutcome.FAILED, CheckOutcome.PARTIAL})
POINT_PLACES = Decimal("0.01")
UPLIFT_PLACES = Decimal("0.1")
HUNDRED = Decimal(100)
ZERO = Decimal(0)

Uplift = tuple[Decimal, Decimal]


def potential_points(results: list[CheckResult], catalogue: Catalogue) -> dict[str, Decimal]:
    """Points of the 0-100 total score each check adds when it passes.

    Args:
        results: Check results of one audit.
        catalogue: Validated catalogue with the area weights.

    Returns:
        Potential per check id, two decimal places; zero for checks that are not failed or
        partial and for checks in areas of weight 0.
    """
    before = total_fraction(results, catalogue)
    potentials: dict[str, Decimal] = {}
    for index, result in enumerate(results):
        if result.outcome not in OPEN_OUTCOMES or before is None:
            potentials[result.check_id] = ZERO
            continue
        passed = result.model_copy(update={"outcome": CheckOutcome.PASSED})
        after = total_fraction([*results[:index], passed, *results[index + 1 :]], catalogue)
        gain = ZERO if after is None else (after - before) * HUNDRED
        potentials[result.check_id] = max(ZERO, gain).quantize(POINT_PLACES, ROUND_HALF_UP)
    return potentials


def distribute_uplift(
    potentials: Mapping[str, Decimal], gain: tuple[float, float]
) -> dict[str, Uplift]:
    """Spread the forecast gain over the checks in proportion to their potential.

    Args:
        potentials: Potential points per check id.
        gain: Additional enquiries per month after optimisation, low and high, low <= high.

    Returns:
        Low and high extra enquiries per month per check id, one decimal place; zero for every
        check when no check has potential.

    Raises:
        ValueError: If the gain is negative or low exceeds high.
    """
    low, high = gain
    if low < 0 or high < low:
        raise ValueError(f"gain must be an ordered non-negative range, got {gain}")
    total = sum(potentials.values(), ZERO)
    if total == 0:
        return {check_id: (ZERO, ZERO) for check_id in potentials}
    gain_low, gain_high = Decimal(str(low)), Decimal(str(high))
    return {
        check_id: (_share(gain_low, potential, total), _share(gain_high, potential, total))
        for check_id, potential in potentials.items()
    }


def _share(gain: Decimal, potential: Decimal, total: Decimal) -> Decimal:
    return (gain * potential / total).quantize(UPLIFT_PLACES, ROUND_HALF_UP)


def with_potential(
    results: list[CheckResult], catalogue: Catalogue, gain: tuple[float, float]
) -> list[CheckResult]:
    """Return copies of the results with potential points and uplift filled in.

    Args:
        results: Check results of one audit.
        catalogue: Validated catalogue with the area weights.
        gain: Additional enquiries per month after optimisation, low and high.

    Returns:
        The results in the same order with potential_points, uplift_low and uplift_high set.

    Raises:
        ValueError: If the gain is negative or low exceeds high.
    """
    potentials = potential_points(results, catalogue)
    uplifts = distribute_uplift(potentials, gain)
    return [
        result.model_copy(
            update={
                "potential_points": potentials[result.check_id],
                "uplift_low": uplifts[result.check_id][0],
                "uplift_high": uplifts[result.check_id][1],
            }
        )
        for result in results
    ]
