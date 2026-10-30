"""Tests for potential points and uplift per check.

Context: Pure calculations on a small hand-built catalogue and the real one. | Input: Fixtures. |
Output: Assertions.
"""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from radarauditor.db.models import CheckOutcome, CheckResult
from radarauditor.scoring.catalog import AreaDefinition, Catalogue, CheckDefinition
from radarauditor.scoring.evaluator import total_score
from radarauditor.scoring.potential import distribute_uplift, potential_points, with_potential

AUDIT_ID = UUID("00000000-0000-4000-8000-000000000001")


def _check(check_id: str) -> CheckDefinition:
    return CheckDefinition(
        id=check_id,
        area=check_id[0],
        name=check_id,
        why="Weil.",
        source="crawler",
        requires_paid=False,
        requires_access=False,
        thresholds={},
        customer_question="?",
        customer_impact=".",
    )


def _area(area_id: str, weight: int, check_ids: list[str]) -> AreaDefinition:
    return AreaDefinition(
        id=area_id, name=area_id, weight=weight, checks=[_check(i) for i in check_ids]
    )


@pytest.fixture
def small_catalogue() -> Catalogue:
    """Area a weighs 60, b 40, m 0 (scored but not counted)."""
    return Catalogue(
        areas=[
            _area("a", 60, ["a1", "a2", "a3"]),
            _area("b", 40, ["b1"]),
            _area("m", 0, ["m1"]),
        ]
    )


def _result(check_id: str, outcome: CheckOutcome) -> CheckResult:
    return CheckResult(
        id=uuid4(),
        audit_id=AUDIT_ID,
        check_id=check_id,
        area=check_id[0],
        outcome=outcome,
        measured={},
        weight=Decimal(1),
    )


@pytest.fixture
def mixed_results() -> list[CheckResult]:
    """a1 failed, a2 passed, a3 not measurable, b1 partial, m1 failed; total score 50."""
    return [
        _result("a1", CheckOutcome.FAILED),
        _result("a2", CheckOutcome.PASSED),
        _result("a3", CheckOutcome.NOT_MEASURABLE),
        _result("b1", CheckOutcome.PARTIAL),
        _result("m1", CheckOutcome.FAILED),
    ]


def test_potential_is_the_total_score_gain(
    small_catalogue: Catalogue, mixed_results: list[CheckResult]
) -> None:
    """a1 lifts area a from 50 to 100 (+30 total), b1 lifts b from 50 to 100 (+20 total)."""
    potentials = potential_points(mixed_results, small_catalogue)

    assert potentials == {
        "a1": Decimal("30.00"),
        "a2": Decimal(0),
        "a3": Decimal(0),
        "b1": Decimal("20.00"),
        "m1": Decimal(0),
    }


def test_potential_matches_the_rescored_total(
    small_catalogue: Catalogue, mixed_results: list[CheckResult]
) -> None:
    """Passing a1 raises total_score by exactly its potential."""
    passed = [
        result.model_copy(update={"outcome": CheckOutcome.PASSED})
        if result.check_id == "a1"
        else result
        for result in mixed_results
    ]

    gain = total_score(passed, small_catalogue) - total_score(mixed_results, small_catalogue)

    assert gain == potential_points(mixed_results, small_catalogue)["a1"]


def test_skipped_checks_have_no_potential(small_catalogue: Catalogue) -> None:
    """Skipped checks count neither in the score nor in the potential."""
    results = [_result("a1", CheckOutcome.SKIPPED), _result("b1", CheckOutcome.FAILED)]

    assert potential_points(results, small_catalogue) == {"a1": Decimal(0), "b1": Decimal(100)}


def test_no_weighted_measurable_area_means_no_potential(small_catalogue: Catalogue) -> None:
    """Without any scored weighted area there is nothing to gain."""
    results = [_result("a1", CheckOutcome.NOT_MEASURABLE), _result("m1", CheckOutcome.FAILED)]

    assert potential_points(results, small_catalogue) == {"a1": Decimal(0), "m1": Decimal(0)}


def test_uplift_is_spread_by_potential() -> None:
    """Shares follow the potential and add up to the gain."""
    uplift = distribute_uplift(
        {"a1": Decimal(30), "b1": Decimal(20), "a2": Decimal(0)}, (5.0, 10.0)
    )

    assert uplift == {
        "a1": (Decimal("3.0"), Decimal("6.0")),
        "b1": (Decimal("2.0"), Decimal("4.0")),
        "a2": (Decimal("0.0"), Decimal("0.0")),
    }


def test_uplift_without_potential_is_zero() -> None:
    """No potential anywhere leaves every uplift at zero."""
    assert distribute_uplift({"a1": Decimal(0)}, (3.0, 6.0)) == {"a1": (Decimal(0), Decimal(0))}


@pytest.mark.parametrize("gain", [(-1.0, 2.0), (3.0, 2.0)])
def test_uplift_rejects_unordered_gain(gain: tuple[float, float]) -> None:
    """The gain must be a non-negative ordered range."""
    with pytest.raises(ValueError, match="ordered non-negative"):
        distribute_uplift({"a1": Decimal(1)}, gain)


def test_with_potential_fills_every_result(
    small_catalogue: Catalogue, mixed_results: list[CheckResult]
) -> None:
    """Every result gets potential and uplift; closed checks stay at zero."""
    enriched = with_potential(mixed_results, small_catalogue, (5.0, 10.0))

    by_id = {result.check_id: result for result in enriched}
    assert [result.check_id for result in enriched] == [r.check_id for r in mixed_results]
    assert (by_id["a1"].uplift_low, by_id["a1"].uplift_high) == (Decimal("3.0"), Decimal("6.0"))
    for check_id in ("a2", "a3", "m1"):
        assert by_id[check_id].potential_points == 0
        assert by_id[check_id].uplift_high == 0


def test_real_catalogue_uplift_adds_up_to_the_gain(
    report_check_results: list[CheckResult], catalogue: Catalogue
) -> None:
    """Over the real catalogue the shares sum to the forecast gain within rounding."""
    enriched = with_potential(report_check_results, catalogue, (4.2, 11.7))

    open_count = sum(1 for result in enriched if result.potential_points)
    assert open_count > 0
    low = sum(result.uplift_low or Decimal(0) for result in enriched)
    high = sum(result.uplift_high or Decimal(0) for result in enriched)
    tolerance = Decimal("0.05") * open_count
    assert abs(low - Decimal("4.2")) <= tolerance
    assert abs(high - Decimal("11.7")) <= tolerance
    assert all((r.uplift_low or 0) <= (r.uplift_high or 0) for r in enriched)
