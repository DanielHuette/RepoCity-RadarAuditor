"""Tests for evaluator.

Context: Unit tests with mocked external calls. | Input: Fixtures. | Output: Assertions.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

import pytest

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.db.app_settings import Source
from radarauditor.db.models import CheckOutcome, CheckResult
from radarauditor.errors import ScoringError
from radarauditor.scoring.catalog import AreaDefinition, Catalogue, CheckDefinition, find_check
from radarauditor.scoring.evaluator import (
    DAILY_CAP_REASON,
    MONTHLY_CAP_REASON,
    SKIPPED_REASON,
    area_module,
    area_scores,
    evaluate,
    measure_check,
    run_check,
    total_fraction,
    total_score,
)

AUDIT_ID = UUID("00000000-0000-4000-8000-000000000001")
ALLOWED_NOT_MEASURABLE = {
    "manual",
    "needs_history",
    "paid_disabled",
    "no_access",
    "no_grid",
    "reply_data_unavailable",
}


def _definition(check_id: str, source: str = "crawler") -> CheckDefinition:
    return CheckDefinition(
        id=check_id,
        area=check_id[0],
        name=check_id,
        why="Weil.",
        source=source,
        requires_paid=source == "paid",
        requires_access=source == "access",
        thresholds={},
        customer_question="?",
        customer_impact=".",
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


def test_full_audit_scores_every_measurable_area_at_100(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    results = evaluate(AUDIT_ID, raw_audit_full, catalogue)
    scores = area_scores(results, catalogue)
    assert set(scores) == set("abcdefghijkln")
    assert all(score == Decimal("100.00") for score in scores.values())
    assert total_score(results, catalogue) == Decimal("100.00")


def test_full_audit_measures_every_collectable_check(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    results = evaluate(AUDIT_ID, raw_audit_full, catalogue)
    assert total_score(results, catalogue) >= Decimal(90)
    in_scope = [result for result in results if result.area in "abcdefghijkln"]
    assert len(in_scope) == len(results)
    for result in in_scope:
        if result.outcome is CheckOutcome.NOT_MEASURABLE:
            assert result.measured["reason"] in ALLOWED_NOT_MEASURABLE, result.check_id
        else:
            assert result.outcome is CheckOutcome.PASSED, (result.check_id, result.measured)


def test_results_are_ordered_idempotent_and_skip_paid_and_access(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    results = evaluate(AUDIT_ID, raw_audit_full, catalogue)
    ids = [result.check_id for result in results]
    assert ids[:3] == ["a1", "a2", "a3"]
    assert not {"c2", "c12", "i1", "j5", "m1"} & set(ids)
    assert "j3" in ids
    assert results[0].id == uuid5(AUDIT_ID, "a1")
    assert [result.id for result in evaluate(AUDIT_ID, raw_audit_full, catalogue)] == [
        result.id for result in results
    ]


def test_weight_is_redistributed_when_paid_disabled(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    without_paid = {r.check_id: r.weight for r in evaluate(AUDIT_ID, raw_audit_full, catalogue)}
    with_paid = {
        r.check_id: r.weight
        for r in evaluate(AUDIT_ID, raw_audit_full, catalogue, paid_enabled=True)
    }
    assert without_paid["a1"] == Decimal("0.900")
    assert without_paid["c1"] == Decimal("1.200")
    assert with_paid["c1"] == Decimal("1.000")
    assert without_paid["i4"] == Decimal("3.000")
    assert with_paid["i4"] == Decimal("1.000")
    assert with_paid["c2"] == Decimal("1.000")


def test_paid_and_access_checks_are_measured_when_enabled(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    results = evaluate(AUDIT_ID, raw_audit_full, catalogue, paid_enabled=True, with_access=True)
    by_id = {r.check_id: r for r in results}
    for check_id in ("c2", "c12", "i1", "i2", "i3", "i5", "j5", "m1", "m2", "m3", "m4"):
        assert by_id[check_id].outcome is CheckOutcome.PASSED, (check_id, by_id[check_id])
    assert by_id["j3"].measured == {"reason": "no_grid"}
    assert by_id["m1"].weight == Decimal("0.000")
    scores = area_scores(results, catalogue)
    assert scores["m"] == Decimal("100.00")
    assert total_score(results, catalogue) == Decimal("100.00")


def test_paid_and_access_checks_without_data_are_not_measurable(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    raw = raw_audit_full.model_copy(update={"paid": None, "access": None})
    results = {
        r.check_id: r
        for r in evaluate(AUDIT_ID, raw, catalogue, paid_enabled=True, with_access=True)
    }
    assert results["c2"].measured == {"reason": "no_paid_data"}
    assert results["m1"].measured == {"reason": "no_access"}
    assert "m" not in area_scores(list(results.values()), catalogue)
    assert total_score(list(results.values()), catalogue) == Decimal("100.00")


def test_failed_impact_area_does_not_move_the_total(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    assert raw_audit_full.access is not None
    access = raw_audit_full.access.model_copy(
        update={"analytics": None, "owner_reviews": [], "search_queries": []}
    )
    raw = raw_audit_full.model_copy(update={"access": access})
    results = evaluate(AUDIT_ID, raw, catalogue, with_access=True)
    scores = area_scores(results, catalogue)
    assert scores["m"] == Decimal("50.00")
    assert total_score(results, catalogue) == Decimal("100.00")


def test_minimal_audit_scores_only_the_missing_profile(
    raw_audit_minimal: RawAudit, catalogue: Catalogue
) -> None:
    results = evaluate(AUDIT_ID, raw_audit_minimal, catalogue)
    measurable = [r for r in results if r.outcome is not CheckOutcome.NOT_MEASURABLE]
    assert [r.check_id for r in measurable] == ["f1"]
    assert area_scores(results, catalogue) == {"f": Decimal("0.00")}
    assert total_score(results, catalogue) == Decimal("0.00")


def test_area_and_total_arithmetic(catalogue: Catalogue) -> None:
    results = [
        _result("a1", CheckOutcome.PASSED),
        _result("a2", CheckOutcome.PARTIAL),
        _result("a3", CheckOutcome.FAILED),
        _result("a4", CheckOutcome.NOT_MEASURABLE),
        _result("c1", CheckOutcome.PASSED),
        _result("c3", CheckOutcome.FAILED),
        _result("c4", CheckOutcome.FAILED),
        _result("i4", CheckOutcome.NOT_MEASURABLE),
    ]
    assert area_scores(results, catalogue) == {"a": Decimal("50.00"), "c": Decimal("33.33")}
    # (9 x 0.5 + 12 x 1/3) / (9 + 12): areas a and c with their catalogue weights.
    assert total_score(results, catalogue) == Decimal("40.48")


def test_skipped_checks_do_not_count(catalogue: Catalogue) -> None:
    results = [
        _result("a1", CheckOutcome.PASSED),
        _result("a2", CheckOutcome.SKIPPED),
        _result("c1", CheckOutcome.SKIPPED),
    ]
    assert area_scores(results, catalogue) == {"a": Decimal("100.00")}


def test_not_measurable_only_raises(catalogue: Catalogue) -> None:
    results = [_result("a1", CheckOutcome.NOT_MEASURABLE), _result("m1", CheckOutcome.PASSED)]
    assert area_scores(results, catalogue) == {"m": Decimal("100.00")}
    with pytest.raises(ScoringError, match="No weighted area"):
        total_score(results, catalogue)
    assert total_fraction(results, catalogue) is None


def test_total_fraction_is_the_unrounded_total(catalogue: Catalogue) -> None:
    """total_score is the fraction times 100, rounded to two places."""
    results = [_result("a1", CheckOutcome.PASSED), _result("c1", CheckOutcome.FAILED)]

    fraction = total_fraction(results, catalogue)

    assert fraction is not None
    assert total_score(results, catalogue) == (fraction * 100).quantize(Decimal("0.01"))
    assert Decimal(0) < fraction < Decimal(1)


def test_run_check_handles_access_and_paid_sources(raw_audit_full: RawAudit) -> None:
    raw = raw_audit_full.model_copy(update={"paid": None, "access": None})
    assert run_check(_definition("m1", "access"), raw)[1] == {"reason": "no_access"}
    assert run_check(_definition("i1", "paid"), raw)[1] == {"reason": "no_paid_data"}
    assert run_check(_definition("m1", "access"), raw_audit_full)[0] is CheckOutcome.PASSED
    assert run_check(_definition("i1", "paid"), raw_audit_full)[0] is CheckOutcome.PASSED


def test_run_check_unknown_check_raises(raw_audit_full: RawAudit) -> None:
    with pytest.raises(ScoringError, match="a99"):
        run_check(_definition("a99"), raw_audit_full)


def test_area_without_module_raises() -> None:
    with pytest.raises(ScoringError, match="no check module"):
        area_module("z")


def test_invalid_module_result_raises(
    raw_audit_full: RawAudit, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = area_module("a")
    monkeypatch.setattr(module, "run", lambda *_: ("passed", {}))
    with pytest.raises(ScoringError, match="invalid result"):
        run_check(_definition("a1"), raw_audit_full)


def test_evaluate_with_custom_catalogue(raw_audit_minimal: RawAudit) -> None:
    catalogue = Catalogue(
        areas=[
            AreaDefinition(id="a", name="A", weight=70, checks=[_definition("a1")]),
            AreaDefinition(id="f", name="F", weight=30, checks=[_definition("f1", "places")]),
        ]
    )
    results = evaluate(AUDIT_ID, raw_audit_minimal, catalogue)
    assert [r.weight for r in results] == [Decimal("70.000"), Decimal("30.000")]
    assert total_score(results, catalogue) == Decimal("0.00")


def test_disabled_checks_are_skipped_without_weight(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    results = evaluate(AUDIT_ID, raw_audit_full, catalogue, disabled_checks=frozenset({"a1"}))
    by_id = {r.check_id: r for r in results}

    assert by_id["a1"].outcome is CheckOutcome.SKIPPED
    assert by_id["a1"].measured == {"reason": SKIPPED_REASON}
    assert by_id["a1"].weight == Decimal(0)
    full = {r.check_id: r.weight for r in evaluate(AUDIT_ID, raw_audit_full, catalogue)}
    assert by_id["a2"].weight > full["a2"]
    assert total_score(results, catalogue) == Decimal("100.00")


def test_capped_source_makes_its_checks_not_measurable(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    raw = raw_audit_full.model_copy(update={"capped_sources": [Source.GOOGLE_SEARCH]})
    by_id = {r.check_id: r for r in evaluate(AUDIT_ID, raw, catalogue)}

    assert by_id["j1"].outcome is CheckOutcome.NOT_MEASURABLE
    assert by_id["j1"].measured == {"reason": DAILY_CAP_REASON, "sources": ["google_search"]}
    assert by_id["a1"].outcome is CheckOutcome.PASSED


def test_monthly_cost_cap_makes_every_dataforseo_check_not_measurable(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    raw = raw_audit_full.model_copy(
        update={
            "cost_capped_sources": [Source.DATAFORSEO],
            "capped_sources": [Source.DATAFORSEO],
        }
    )
    by_id = {r.check_id: r for r in evaluate(AUDIT_ID, raw, catalogue, paid_enabled=True)}

    for check_id in ("c2", "c12", "i1", "i2", "i3", "i5", "j5"):
        assert by_id[check_id].outcome is CheckOutcome.NOT_MEASURABLE, check_id
        assert by_id[check_id].measured == {
            "reason": MONTHLY_CAP_REASON,
            "sources": ["dataforseo"],
        }
    assert by_id["j1"].outcome is CheckOutcome.PASSED


def test_paid_checks_name_the_dataforseo_route(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    raw = raw_audit_full.model_copy(update={"domain_first_capture": date(2012, 1, 1)})
    by_id = {r.check_id: r for r in evaluate(AUDIT_ID, raw, catalogue, paid_enabled=True)}

    for check_id in ("c2", "c12", "i1", "i2", "i3", "j5"):
        assert by_id[check_id].measured["route"] == "dataforseo", check_id
    assert by_id["i5"].measured["routes"] == {"dataforseo": "dataforseo", "wayback": "api"}
    assert "route" not in by_id["j1"].measured
    assert "routes" not in by_id["j1"].measured


def test_search_checks_list_the_route_per_phrase_after_a_fallback(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    google = [s for s in raw_audit_full.serps if s.engine == "google" and s.vertical == "web"]
    first = google[0]
    serps = [
        s.model_copy(update={"route": "dataforseo"}) if s is first else s
        for s in raw_audit_full.serps
    ]
    raw = raw_audit_full.model_copy(update={"serps": serps})
    by_id = {r.check_id: r for r in evaluate(AUDIT_ID, raw, catalogue)}

    routes = by_id["j1"].measured["routes"]
    assert isinstance(routes, dict)
    assert routes[first.phrase] == "dataforseo"
    assert set(routes.values()) <= {"browser", "dataforseo"}
    assert "routes" not in by_id["j7"].measured


def test_capped_assistant_only_drops_its_own_answers(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    raw = raw_audit_full.model_copy(update={"capped_sources": [Source.CHATGPT]})
    k1 = find_check(catalogue, "k1")

    outcome, measured = measure_check(k1, raw)

    assert outcome is not CheckOutcome.NOT_MEASURABLE
    assert measured == {
        **run_check(k1, _only_gemini(raw_audit_full))[1],
        "capped_sources": ["chatgpt"],
    }


def test_every_assistant_capped_is_not_measurable(
    raw_audit_full: RawAudit, catalogue: Catalogue
) -> None:
    raw = raw_audit_full.model_copy(update={"capped_sources": [Source.CHATGPT, Source.GEMINI]})

    outcome, measured = measure_check(find_check(catalogue, "k1"), raw)

    assert outcome is CheckOutcome.NOT_MEASURABLE
    assert measured == {"reason": DAILY_CAP_REASON, "sources": ["chatgpt", "gemini"]}


def _only_gemini(raw: RawAudit) -> RawAudit:
    answers = [answer for answer in raw.assistant_answers if answer.provider == "gemini"]
    return raw.model_copy(update={"assistant_answers": answers})
