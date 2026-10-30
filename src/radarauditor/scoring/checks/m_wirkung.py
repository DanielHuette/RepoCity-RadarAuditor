"""Checks of area m: wirkung (ids m1..), measured with the customer's own Google data.

Context: One pure function per catalogue check; each returns outcome and measured values. The
evaluator reports these checks as not measurable while the RawAudit holds no access data; the
functions handle access data in which a single source is missing.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

from statistics import mean

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    TOLERANCE,
    CheckFunction,
    CheckReturn,
    Thresholds,
    dispatch,
    listed,
    not_measurable,
    service_terms,
    tokens,
)
from radarauditor.scoring.checks.g_bewertungen import owner_reply, reply_delay_days

ACTION_METRICS = ("CALL_CLICKS", "BUSINESS_DIRECTION_REQUESTS", "WEBSITE_CLICKS")
REPLY_DAYS = 7
REPLY_PASS_SHARE = 0.8
REPLY_PARTIAL_SHARE = 0.5
PERCENT = 100


def change_percent(now: int, before: int) -> float | None:
    """Change from before to now in percent, one decimal; None when before is 0."""
    if before == 0:
        return None
    return round((now - before) / before * PERCENT, 1)


def profile_actions(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """m1: calls, direction requests and website clicks from the profile hold or grow.

    Passed if the last 28 days bring at least as many actions as the 28 days before, partial
    within 20 % below, failed beyond.
    """
    access = raw.access
    if access is None:
        return not_measurable("no_access")
    if not access.profile_metrics:
        return not_measurable("no_profile_metrics")
    now = sum(access.profile_metrics.get(name, 0) for name in ACTION_METRICS)
    before = sum(access.profile_metrics_previous.get(name, 0) for name in ACTION_METRICS)
    if now >= before:
        outcome = CheckOutcome.PASSED
    elif now >= before * (1 - TOLERANCE):
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {
        "metrics": access.profile_metrics,
        "metrics_previous": access.profile_metrics_previous,
        "change_percent": {
            name: change_percent(value, access.profile_metrics_previous.get(name, 0))
            for name, value in access.profile_metrics.items()
        },
        "actions": now,
        "actions_previous": before,
        "actions_change_percent": change_percent(now, before),
    }


def _matches(query: str, phrases: dict[str, list[str]]) -> list[str]:
    words = set(tokens(query))
    return [phrase for phrase, terms in phrases.items() if set(terms) <= words]


def search_console_clicks(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """m2: at least one query with clicks contains a tracked service phrase.

    Partial when Search Console shows queries but none with clicks matches; failed without any
    query.
    """
    access = raw.access
    if access is None:
        return not_measurable("no_access")
    if not access.search_totals:
        return not_measurable("no_search_console")
    phrases = service_terms(raw)
    if not phrases:
        return not_measurable("no_search_phrases")
    matching = [
        query
        for query in access.search_queries
        if query.clicks > 0 and _matches(query.query, phrases)
    ]
    if matching:
        outcome = CheckOutcome.PASSED
    elif access.search_queries:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {
        "matching_queries": {query.query: query.clicks for query in matching[:10]},
        "top_queries": {query.query: query.clicks for query in access.search_queries[:10]},
        "queries_total": len(access.search_queries),
        "totals": access.search_totals,
        "tracked_phrases": listed(phrases),
    }


def website_enquiries(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """m3: the website records key events (enquiries); visits without them are partial."""
    access = raw.access
    if access is None:
        return not_measurable("no_access")
    analytics = access.analytics
    if analytics is None:
        return not_measurable("no_analytics")
    if analytics.key_events > 0:
        outcome = CheckOutcome.PASSED
    elif analytics.sessions > 0:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {
        "sessions": analytics.sessions,
        "engaged_sessions": analytics.engaged_sessions,
        "key_events": analytics.key_events,
    }


def owner_reply_rate(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """m4: share of reviews the owner answered within seven days, from the profile itself.

    Passed from 80 %, partial from 50 %.
    """
    access = raw.access
    if access is None:
        return not_measurable("no_access")
    reviews = access.owner_reviews
    if not reviews:
        return not_measurable("no_owner_reviews")
    delays = [reply_delay_days(review) for review in reviews]
    in_time = sum(1 for delay in delays if delay is not None and delay <= REPLY_DAYS)
    replied = sum(1 for review in reviews if owner_reply(review) is not None)
    share = in_time / len(reviews)
    if share >= REPLY_PASS_SHARE:
        outcome = CheckOutcome.PASSED
    elif share >= REPLY_PARTIAL_SHARE:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    known = [delay for delay in delays if delay is not None]
    return outcome, {
        "reviews": len(reviews),
        "replied": replied,
        "replied_within_days": in_time,
        "reply_share_in_time": round(share, 3),
        "average_reply_days": round(mean(known), 1) if known else None,
        "reply_days": REPLY_DAYS,
    }


CHECKS: dict[str, CheckFunction] = {
    "m1": profile_actions,
    "m2": search_console_clicks,
    "m3": website_enquiries,
    "m4": owner_reply_rate,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
