"""Evaluate every applicable check and compute area and total scores.

Context: Checks whose paid or access requirement is not met in the audit context are left out and
their weight is redistributed within the area; a check that runs but finds no data counts as not
measurable. Checks switched off in the app settings are stored as skipped with weight 0 and do not
count. A check whose source reached its monthly cost cap is not measurable with reason
"Monatslimit erreicht"; one whose source ran out of its daily cap with reason "Tageslimit
erreicht"; for assistant checks only the capped assistants' answers are left out. Areas of weight 0
(area m) are scored but do not move the total.
Input: RawAudit, Catalogue.
Output: CheckResults and scores.
"""

import importlib
import logging
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from types import ModuleType
from uuid import UUID, uuid5

from radarauditor.collectors.assistants import provider_source
from radarauditor.collectors.snapshot import RawAudit
from radarauditor.db.app_settings import Source
from radarauditor.db.models import CheckOutcome, CheckResult
from radarauditor.errors import ScoringError
from radarauditor.scoring.catalog import Catalogue, CheckDefinition, checks_for_context
from radarauditor.scoring.checks.common import CheckReturn

logger = logging.getLogger(__name__)

AREA_MODULES = {
    "a": "a_technik",
    "b": "b_struktur",
    "c": "c_inhalt",
    "d": "d_firmendaten",
    "e": "e_strukturierte_daten",
    "f": "f_google_profil",
    "g": "g_bewertungen",
    "h": "h_verzeichnisse",
    "i": "i_links",
    "j": "j_rankings",
    "k": "k_assistenten",
    "l": "l_recht",
    "m": "m_wirkung",
    "n": "n_werkzeuge",
}
POINTS = {
    CheckOutcome.PASSED: Decimal(1),
    CheckOutcome.PARTIAL: Decimal("0.5"),
    CheckOutcome.FAILED: Decimal(0),
}
DAILY_CAP_REASON = "Tageslimit erreicht"
MONTHLY_CAP_REASON = "Monatslimit erreicht"
SKIPPED_REASON = "In den Einstellungen abgeschaltet"
ASSISTANT_SOURCE = "assistants"
CATALOGUE_SOURCE_FEEDS: dict[str, frozenset[Source]] = {
    "places": frozenset({Source.PLACES, Source.GOOGLE_MAPS}),
    "serp": frozenset({Source.GOOGLE_SEARCH}),
    ASSISTANT_SOURCE: frozenset({Source.CHATGPT, Source.CLAUDE, Source.GEMINI, Source.PERPLEXITY}),
    "paid": frozenset({Source.DATAFORSEO}),
}
PAID_SOURCE = "paid"
SERP_SOURCE = "serp"
DATAFORSEO_ROUTE = "dataforseo"
API_ROUTE = "api"
DOMAIN_AGE_CHECK = "i5"
# Serp checks that read Bing or the image search, which only run in the browser.
NON_GOOGLE_WEB_SERP_CHECKS = frozenset({"j7", "j8"})
WEIGHT_PLACES = Decimal("0.001")
SCORE_PLACES = Decimal("0.01")
HUNDRED = Decimal(100)


@cache
def area_module(area_id: str) -> ModuleType:
    """Import the check module of an area.

    Raises:
        ScoringError: If the area has no check module.
    """
    name = AREA_MODULES.get(area_id)
    if name is None:
        raise ScoringError(f"Area {area_id} has no check module")
    return importlib.import_module(f"radarauditor.scoring.checks.{name}")


def run_check(check: CheckDefinition, raw: RawAudit) -> CheckReturn:
    """Measure one check; access checks without collected access data are not measurable.

    Paid checks run their function, which reports not measurable while the RawAudit holds no
    paid data.

    Args:
        check: Catalogue definition.
        raw: Collected audit data.

    Returns:
        Outcome and measured values.

    Raises:
        ScoringError: If no function exists for the check.
    """
    if check.source == "access" and raw.access is None:
        return CheckOutcome.NOT_MEASURABLE, {"reason": "no_access"}
    module = area_module(check.area)
    outcome, measured = module.run(check.id, raw, check.thresholds)
    if not isinstance(outcome, CheckOutcome) or not isinstance(measured, dict):
        raise ScoringError(f"Check {check.id} returned an invalid result")
    return outcome, measured


def evaluate(
    audit_id: UUID,
    raw: RawAudit,
    catalogue: Catalogue,
    paid_enabled: bool = False,
    with_access: bool = False,
    disabled_checks: frozenset[str] = frozenset(),
) -> list[CheckResult]:
    """Run each check function by id and collect outcomes.

    Args:
        audit_id: Audit the results belong to; result ids derive from it, so reruns are idempotent.
        raw: Collected audit data.
        catalogue: Validated catalogue.
        paid_enabled: Whether checks that need the paid provider count, i.e. whether the dataforseo
            source is switched on in the app settings.
        with_access: Whether checks that need customer access count.
        disabled_checks: Ids of checks switched off for this audit.

    Returns:
        One result per check that counts in this context, in catalogue order; disabled checks are
        skipped with weight 0.

    Raises:
        ScoringError: If a check has no function or returns an invalid result.
    """
    checks = checks_for_context(catalogue, paid_enabled, with_access)
    weights = _check_weights(catalogue, [c for c in checks if c.id not in disabled_checks])
    results: list[CheckResult] = []
    for check in checks:
        skipped: CheckReturn = (CheckOutcome.SKIPPED, {"reason": SKIPPED_REASON})
        outcome, measured = skipped if check.id in disabled_checks else measure_check(check, raw)
        results.append(
            CheckResult(
                id=uuid5(audit_id, check.id),
                audit_id=audit_id,
                check_id=check.id,
                area=check.area,
                outcome=outcome,
                measured=measured,
                weight=weights.get(check.id, Decimal(0)),
            )
        )
    scored = sum(1 for result in results if result.outcome in POINTS)
    logger.info("Evaluated %d checks for audit %s, %d scored", len(results), audit_id, scored)
    return results


def _check_weights(catalogue: Catalogue, counted: list[CheckDefinition]) -> dict[str, Decimal]:
    """Share the area weight equally among the counted checks of each area."""
    per_area: dict[str, int] = defaultdict(int)
    for check in counted:
        per_area[check.area] += 1
    area_weights = {area.id: area.weight for area in catalogue.areas}
    return {
        check.id: (Decimal(area_weights[check.area]) / per_area[check.area]).quantize(
            WEIGHT_PLACES, rounding=ROUND_HALF_UP
        )
        for check in counted
    }


def measure_check(check: CheckDefinition, raw: RawAudit) -> CheckReturn:
    """Run a check unless a daily or monthly cost cap of its source ran out during collection.

    Args:
        check: Catalogue definition.
        raw: Collected audit data.

    Returns:
        Outcome and measured values; not measurable with reason "Monatslimit erreicht" or
        "Tageslimit erreicht" when the data is incomplete because of a cap.

    Raises:
        ScoringError: If no function exists for the check.
    """
    feeds = CATALOGUE_SOURCE_FEEDS.get(check.source, frozenset())
    cost_capped = sorted(feeds.intersection(raw.cost_capped_sources))
    if cost_capped:
        names = [source.value for source in cost_capped]
        return CheckOutcome.NOT_MEASURABLE, {"reason": MONTHLY_CAP_REASON, "sources": names}
    capped = sorted(feeds.intersection(raw.capped_sources))
    if not capped:
        outcome, measured = run_check(check, raw)
        return outcome, {**measured, **data_routes(check, raw, outcome)}
    capped_names = [source.value for source in capped]
    capped_measured: dict[str, object] = {"reason": DAILY_CAP_REASON, "sources": capped_names}
    if check.source != ASSISTANT_SOURCE:
        return CheckOutcome.NOT_MEASURABLE, capped_measured
    outcome, measured = run_check(check, _without_answers_of(raw, frozenset(capped)))
    if outcome is CheckOutcome.NOT_MEASURABLE:
        return outcome, capped_measured
    return outcome, {**measured, "capped_sources": capped_names}


def data_routes(check: CheckDefinition, raw: RawAudit, outcome: CheckOutcome) -> dict[str, object]:
    """Which route delivered the data of a measured check, for the route note in the app.

    Checks of source paid read DataForSEO ("route"); i5 adds the Wayback Machine as a free API
    ("routes" per source). Google web search checks list the route per searched phrase
    ("routes") as soon as one phrase came through DataForSEO because Google blocked the browser;
    with every phrase read in the browser nothing is added. Unmeasured checks get no route.

    Args:
        check: Catalogue definition.
        raw: Collected audit data.
        outcome: Outcome of the check.

    Returns:
        {"route": id}, {"routes": {key: id}} or {}.
    """
    if outcome in (CheckOutcome.NOT_MEASURABLE, CheckOutcome.SKIPPED):
        return {}
    if check.source == PAID_SOURCE:
        if check.id == DOMAIN_AGE_CHECK and raw.domain_first_capture is not None:
            return {"routes": {Source.DATAFORSEO.value: DATAFORSEO_ROUTE, "wayback": API_ROUTE}}
        return {"route": DATAFORSEO_ROUTE}
    if check.source != SERP_SOURCE or check.id in NON_GOOGLE_WEB_SERP_CHECKS:
        return {}
    routes = {
        serp.phrase: serp.route
        for serp in raw.serps
        if serp.engine == "google" and serp.vertical == "web"
    }
    if DATAFORSEO_ROUTE not in routes.values():
        return {}
    return {"routes": routes}


def _without_answers_of(raw: RawAudit, capped: frozenset[Source]) -> RawAudit:
    """Drop the answers of capped assistants, whose question set is incomplete."""
    kept = [
        answer for answer in raw.assistant_answers if provider_source(answer.provider) not in capped
    ]
    return raw.model_copy(update={"assistant_answers": kept})


def _area_fractions(results: list[CheckResult]) -> dict[str, Decimal]:
    """Unrounded share of points per area over its measurable results."""
    points: dict[str, list[Decimal]] = defaultdict(list)
    for result in results:
        if result.outcome in POINTS:
            points[result.area].append(POINTS[result.outcome])
    return {area: sum(values, Decimal(0)) / len(values) for area, values in points.items()}


def area_scores(results: list[CheckResult], catalogue: Catalogue) -> dict[str, Decimal]:
    """Score 0-100 per area from passed=1, partial=0.5, failed=0.

    Args:
        results: Check results of one audit.
        catalogue: Validated catalogue, which fixes the order of the areas.

    Returns:
        Score with two decimal places for every area with at least one measurable result.
    """
    fractions = _area_fractions(results)
    return {
        area.id: (fractions[area.id] * HUNDRED).quantize(SCORE_PLACES, rounding=ROUND_HALF_UP)
        for area in catalogue.areas
        if area.id in fractions
    }


def total_score(results: list[CheckResult], catalogue: Catalogue) -> Decimal:
    """Weighted total 0-100 over measurable areas.

    Args:
        results: Check results of one audit.
        catalogue: Validated catalogue with the area weights.

    Returns:
        Sum of area weight times area score divided by the summed weight of the scored areas,
        two decimal places.

    Raises:
        ScoringError: If no weighted area has a measurable result.
    """
    fraction = total_fraction(results, catalogue)
    if fraction is None:
        raise ScoringError("No weighted area has a measurable result")
    return (fraction * HUNDRED).quantize(SCORE_PLACES, rounding=ROUND_HALF_UP)


def total_fraction(results: list[CheckResult], catalogue: Catalogue) -> Decimal | None:
    """Unrounded weighted share of points 0-1 over measurable areas.

    Args:
        results: Check results of one audit.
        catalogue: Validated catalogue with the area weights.

    Returns:
        The share behind total_score, or None when no weighted area has a measurable result.
    """
    fractions = _area_fractions(results)
    weights = {area.id: Decimal(area.weight) for area in catalogue.areas if area.id in fractions}
    weight_sum = sum(weights.values(), Decimal(0))
    if weight_sum == 0:
        return None
    weighted = sum((weights[area] * fractions[area] for area in weights), Decimal(0))
    return weighted / weight_sum
