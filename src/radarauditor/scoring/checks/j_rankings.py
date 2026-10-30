"""Checks of area j: rankings (ids j1..).

Context: One pure function per catalogue check; each returns outcome and measured values.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

from radarauditor.collectors.patterns import host_of
from radarauditor.collectors.snapshot import RawAudit, SerpResult
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    NO_PAID_DATA,
    CheckFunction,
    CheckReturn,
    Thresholds,
    band_outcome,
    brand_serps,
    business_name,
    combine,
    customer_host,
    dispatch,
    fold,
    google_web_serps,
    listed,
    names_match,
    not_measurable,
    number,
    route_entry,
    same_site,
    share_of_best,
)

GRID_TOP = 3
GRID_PASS_SHARE = 0.7
GRID_PARTIAL_SHARE = 0.4
RANKED_PASS_SHARE = 0.8
RANKED_PARTIAL_SHARE = 0.4


def organic_position(serp: SerpResult, host: str) -> int | None:
    """Best organic position of any URL on the customer's host."""
    positions = [position for position, url in serp.organic if same_site(url, host)]
    return min(positions) if positions else None


def local_pack_position(serp: SerpResult, name: str) -> int | None:
    """Best local pack position of an entry whose name matches the business."""
    positions = [position for position, entry in serp.local_pack if names_match(entry, name)]
    return min(positions) if positions else None


def _non_brand_serps(raw: RawAudit) -> list[SerpResult]:
    """Google web searches for services, excluding searches for the business name itself."""
    name = business_name(raw)
    return [
        serp for serp in google_web_serps(raw) if name is None or not names_match(serp.phrase, name)
    ]


def _ranked_everywhere(positions: dict[str, int | None], limit: float) -> CheckOutcome:
    """PASSED if every phrase ranks within limit, FAILED if none does, PARTIAL otherwise."""
    return combine(
        CheckOutcome.PASSED if position is not None and position <= limit else CheckOutcome.FAILED
        for position in positions.values()
    )


def organic_rankings(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """j1: all phrases rank within good; any phrase within visible is partial."""
    serps = _non_brand_serps(raw)
    if not serps:
        return not_measurable("no_serps")
    host = customer_host(raw)
    if host is None:
        return not_measurable("no_website")
    top, good, visible = (number(thresholds, key) for key in ("top", "good", "visible"))
    positions = {serp.phrase: organic_position(serp, host) for serp in serps}
    ranked = [position for position in positions.values() if position is not None]
    in_good = sum(1 for position in ranked if position <= good)
    in_visible = sum(1 for position in ranked if position <= visible)
    if in_good == len(positions):
        outcome = CheckOutcome.PASSED
    elif in_visible:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {
        "positions": positions,
        "in_top": sum(1 for position in ranked if position <= top),
        "in_good": in_good,
        "in_visible": in_visible,
        "phrases_total": len(positions),
        "top": top,
        "good": good,
        "visible": visible,
    }


def map_pack(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """j2: the business is in the top map results for every phrase that shows a map block.

    A phrase whose Google search shows no map block, or was not searched, takes the position in
    the Google Maps result list instead; maps_phrases lists those phrases.
    """
    name = business_name(raw)
    if name is None:
        return not_measurable("no_business_name")
    serps = [serp for serp in _non_brand_serps(raw) if serp.local_pack]
    positions = {serp.phrase: local_pack_position(serp, name) for serp in serps}
    from_maps = [ranking for ranking in raw.map_rankings if ranking.phrase not in positions]
    positions.update({ranking.phrase: ranking.position for ranking in from_maps})
    if not positions:
        return not_measurable("no_local_pack")
    top = number(thresholds, "top")
    in_top = sum(1 for position in positions.values() if position is not None and position <= top)
    return _ranked_everywhere(positions, top), {
        "positions": positions,
        "in_top": in_top,
        "phrases_with_map": len(positions),
        "maps_phrases": [ranking.phrase for ranking in from_maps],
        "top": top,
        "business_name": name,
        **route_entry(ranking.route for ranking in from_maps),
    }


def _optional_number(thresholds: Thresholds, key: str, default: float) -> float:
    return number(thresholds, key) if key in thresholds else default


def grid_visibility(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """j3: share of grid points where the business is in the top map results.

    Passed from GRID_PASS_SHARE of the points, partial from GRID_PARTIAL_SHARE; the catalogue may
    override them with the thresholds top, pass_share and partial_share.
    """
    if not raw.grid:
        return not_measurable("no_grid")
    top = _optional_number(thresholds, "top", GRID_TOP)
    pass_share = _optional_number(thresholds, "pass_share", GRID_PASS_SHARE)
    partial_share = _optional_number(thresholds, "partial_share", GRID_PARTIAL_SHARE)
    in_top = [p.key for p in raw.grid if p.position is not None and p.position <= top]
    share = len(in_top) / len(raw.grid)
    if share >= pass_share:
        outcome = CheckOutcome.PASSED
    elif share >= partial_share:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {
        "points_total": len(raw.grid),
        "points_in_top": len(in_top),
        "share_in_top": round(share, 2),
        "positions": {point.key: point.position for point in raw.grid},
        "top": top,
        **route_entry(point.route for point in raw.grid),
    }


def brand_search(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """j4: the own website is first for the brand search with a knowledge panel beside it.

    First place without a panel is partial; any lower place fails.
    """
    host = customer_host(raw)
    if business_name(raw) is None or host is None:
        return not_measurable("no_business_name")
    serps = brand_serps(raw)
    if not serps:
        return not_measurable("no_brand_search")
    positions = [organic_position(serp, host) for serp in serps]
    best = min((position for position in positions if position is not None), default=None)
    panel = any(serp.knowledge_panel for serp in serps)
    if best == 1:
        outcome = CheckOutcome.PASSED if panel else CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {"brand_phrase": serps[0].phrase, "position": best, "knowledge_panel": panel}


def ai_overviews(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """j6: every Google AI overview shown for a phrase names the business or its website."""
    overviews = [serp for serp in google_web_serps(raw) if serp.ai_overview_present]
    if not overviews:
        return not_measurable("no_ai_overview")
    name = business_name(raw)
    host = customer_host(raw)

    def mentions_business(serp: SerpResult) -> bool:
        return any(
            (name is not None and names_match(mention, name))
            or (host is not None and host in fold(mention))
            for mention in serp.ai_overview_mentions
        )

    mentioned = [serp.phrase for serp in overviews if mentions_business(serp)]
    outcome = combine(
        CheckOutcome.PASSED if serp.phrase in mentioned else CheckOutcome.FAILED
        for serp in overviews
    )
    return outcome, {
        "phrases_with_overview": len(overviews),
        "phrases_mentioning_business": mentioned,
    }


def bing_rankings(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """j7: the website ranks within good on Bing for every phrase searched there."""
    serps = [serp for serp in raw.serps if serp.engine == "bing"]
    if not serps:
        return not_measurable("no_bing_serp")
    host = customer_host(raw)
    if host is None:
        return not_measurable("no_website")
    good = number(thresholds, "good")
    positions = {serp.phrase: organic_position(serp, host) for serp in serps}
    return _ranked_everywhere(positions, good), {
        "positions": positions,
        "phrases_total": len(positions),
        "good": good,
    }


def image_search(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """j8: the website is a source among the first image results for every phrase."""
    serps = [serp for serp in raw.serps if serp.vertical == "images"]
    if not serps:
        return not_measurable("no_image_serp")
    host = customer_host(raw)
    if host is None:
        return not_measurable("no_website")
    positions = {serp.phrase: organic_position(serp, host) for serp in serps}
    outcome = combine(
        CheckOutcome.FAILED if position is None else CheckOutcome.PASSED
        for position in positions.values()
    )
    return outcome, {
        "source_positions": positions,
        "phrases_total": len(positions),
        "sources_seen": {serp.phrase: len(serp.organic) for serp in serps},
    }


def is_own_ad(advertiser: str, host: str | None, name: str | None) -> bool:
    """True if an ad entry is the customer's domain or, for a name, the business name."""
    if host_of(advertiser) is not None:
        return host is not None and same_site(advertiser, host)
    return names_match(advertiser, name)


def competitor_ads(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """j10: no ad shown on the brand search belongs to anyone but the customer."""
    name = business_name(raw)
    if name is None:
        return not_measurable("no_business_name")
    serps = brand_serps(raw)
    if not serps:
        return not_measurable("no_brand_search")
    host = customer_host(raw)
    ads = [advertiser for serp in serps for advertiser in serp.ads]
    foreign = sorted({advertiser for advertiser in ads if not is_own_ad(advertiser, host, name)})
    outcome = CheckOutcome.FAILED if foreign else CheckOutcome.PASSED
    return outcome, {"foreign_ads": listed(foreign), "ads_seen": len(ads)}


def ranked_keywords(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """j5: keywords the site ranks for reach 80 % of the best competitor; partial from 40 %."""
    if raw.paid is None:
        return not_measurable(NO_PAID_DATA)
    customer = raw.paid.customer_domain
    counts = raw.paid.ranked_keywords
    if customer is None or customer not in counts:
        return not_measurable("no_ranked_keywords")
    rivals = {domain: float(count) for domain, count in counts.items() if domain != customer}
    if not rivals:
        return not_measurable("no_competitor_keywords")
    share, leader = share_of_best(counts[customer], rivals)
    return band_outcome(share, RANKED_PASS_SHARE, RANKED_PARTIAL_SHARE), {
        "ranked_keywords": counts[customer],
        "competitors": {domain: int(count) for domain, count in rivals.items()},
        "best_competitor": leader,
        "share_of_best": share,
    }


def _not_collected(reason: str) -> CheckFunction:
    """Check function for data the search collector does not record."""

    def check(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
        return not_measurable(reason)

    return check


CHECKS: dict[str, CheckFunction] = {
    "j1": organic_rankings,
    "j2": map_pack,
    "j3": grid_visibility,
    "j4": brand_search,
    "j5": ranked_keywords,
    "j6": ai_overviews,
    "j7": bing_rankings,
    "j8": image_search,
    "j9": _not_collected("needs_history"),
    "j10": competitor_ads,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
