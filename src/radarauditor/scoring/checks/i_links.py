"""Checks of area i: links (ids i1..).

Context: One pure function per catalogue check; each returns outcome and measured values.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

from radarauditor.collectors.patterns import host_of
from radarauditor.collectors.snapshot import BacklinkSummary, PaidSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    NO_PAID_DATA,
    CheckFunction,
    CheckReturn,
    Thresholds,
    band_outcome,
    brand_serps,
    business_name,
    ceiling_outcome,
    content_pages,
    customer_host,
    dispatch,
    is_google_profile_link,
    listed,
    not_measurable,
    same_site,
    share_of_best,
)

DOMAINS_PASS_SHARE = 0.8
DOMAINS_PARTIAL_SHARE = 0.4
LOCAL_PASS_SHARE = 0.3
LOCAL_PARTIAL_SHARE = 0.15
SPAM_PASS_SHARE = 0.1
SPAM_PARTIAL_SHARE = 0.25
VISIBILITY_TOLERANCE = 0.2
DAYS_PER_YEAR = 365.25
MIN_DOMAIN_AGE_YEARS = 1.0

SOCIAL_NETWORK_DOMAINS = frozenset(
    {
        "facebook.com",
        "fb.com",
        "instagram.com",
        "linkedin.com",
        "xing.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "youtu.be",
        "tiktok.com",
        "pinterest.com",
        "pinterest.de",
        "threads.net",
        "whatsapp.com",
        "wa.me",
    }
)


def is_social_network(host: str) -> bool:
    """True if the host belongs to a social network."""
    return any(host == domain or host.endswith(f".{domain}") for domain in SOCIAL_NETWORK_DOMAINS)


def _own_links(paid: PaidSnapshot) -> BacklinkSummary | None:
    if paid.customer_domain is None:
        return None
    return paid.backlinks.get(paid.customer_domain)


def referring_domains(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """i1: referring domains reach 80 % of the best competitor; partial from 40 %."""
    if raw.paid is None:
        return not_measurable(NO_PAID_DATA)
    own = _own_links(raw.paid)
    if own is None:
        return not_measurable("no_backlink_data")
    rivals = {
        domain: float(summary.referring_domains)
        for domain, summary in raw.paid.backlinks.items()
        if domain != own.domain
    }
    if not rivals:
        return not_measurable("no_competitor_backlinks")
    share, leader = share_of_best(own.referring_domains, rivals)
    return band_outcome(share, DOMAINS_PASS_SHARE, DOMAINS_PARTIAL_SHARE), {
        "referring_domains": own.referring_domains,
        "competitors": {domain: int(count) for domain, count in rivals.items()},
        "best_competitor": leader,
        "share_of_best": share,
    }


def local_links(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """i2: at least 30 % of the top referring domains are local (.de or city name); 15 % partial."""
    if raw.paid is None:
        return not_measurable(NO_PAID_DATA)
    own = _own_links(raw.paid)
    if own is None or own.referring_domains_local_share is None:
        return not_measurable("no_backlink_data")
    share = own.referring_domains_local_share
    return band_outcome(share, LOCAL_PASS_SHARE, LOCAL_PARTIAL_SHARE), {
        "local_share": share,
        "referring_domains": own.referring_domains,
    }


def harmful_links(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """i3: at most 10 % of the top referring domains look like spam; 25 % partial."""
    if raw.paid is None:
        return not_measurable(NO_PAID_DATA)
    own = _own_links(raw.paid)
    if own is None or own.referring_domains_spam_share is None:
        return not_measurable("no_backlink_data")
    share = own.referring_domains_spam_share
    return ceiling_outcome(share, SPAM_PASS_SHARE, SPAM_PARTIAL_SHARE), {
        "spam_share": share,
        "spam_score": own.spam_score,
        "referring_domains": own.referring_domains,
    }


def domain_age_years(raw: RawAudit) -> float | None:
    """Age of the domain in years at collection time since its first Wayback Machine capture."""
    first = raw.domain_first_capture
    if first is None:
        return None
    return round((raw.collected_at.date() - first).days / DAYS_PER_YEAR, 1)


def visibility_history(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """i5: top-10 keywords now at least as many as twelve months ago; within 20 % below partial.

    The history comes from DataForSEO, the domain age from the first Wayback Machine capture. A
    domain that exists for at least a year without any top-10 keyword in the whole history has had
    no visibility all year and fails; a younger or undated domain without history is not
    measurable.
    """
    years = domain_age_years(raw)
    age = _age_details(raw, years)
    if raw.paid is None:
        return not_measurable(NO_PAID_DATA, **age)
    history = raw.paid.historic_visibility
    if not history and years is not None and years >= MIN_DOMAIN_AGE_YEARS:
        return CheckOutcome.FAILED, {"history": {}, "top10_keywords": 0, **age}
    if len(history) < 2:
        return not_measurable("short_history", **age)
    (first_month, first), (last_month, last) = history[0], history[-1]
    return _visibility_trend(first, last), {
        "first_month": first_month,
        "first_value": first,
        "last_month": last_month,
        "last_value": last,
        "history": dict(history),
        **age,
    }


def _age_details(raw: RawAudit, years: float | None) -> dict[str, object]:
    if raw.domain_first_capture is None:
        return {}
    return {
        "domain_first_capture": raw.domain_first_capture.isoformat(),
        "domain_age_years": years,
    }


def _visibility_trend(first: float, last: float) -> CheckOutcome:
    if last >= first:
        return CheckOutcome.PASSED
    if last >= first * (1 - VISIBILITY_TOLERANCE):
        return CheckOutcome.PARTIAL
    return CheckOutcome.FAILED


def unlinked_mentions(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """i4: foreign pages found for the brand search; whether they link needs the paid provider."""
    if business_name(raw) is None:
        return not_measurable("no_business_name")
    brand_searches = brand_serps(raw)
    if not brand_searches:
        return not_measurable("no_brand_search")
    host = customer_host(raw)
    mentions = sorted(
        {
            found
            for serp in brand_searches
            for _, url in serp.organic
            if (found := host_of(url)) is not None and (host is None or not same_site(url, host))
        }
    )
    return not_measurable("paid_disabled", mentions=mentions[:10], mention_count=len(mentions))


def outbound_partner_links(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """i6: pages link to other sites such as manufacturers or trade bodies.

    Links to social networks, to Google Maps or the Google profile and to the customer's own
    domains do not count.
    """
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    own_hosts = {
        host
        for host in (
            customer_host(raw),
            host_of(raw.place.website) if raw.place is not None else None,
        )
        if host is not None
    }
    profile_url = (
        str(raw.place.google_maps_uri)
        if raw.place is not None and raw.place.google_maps_uri is not None
        else None
    )
    partners = sorted(
        {
            host
            for page in content_pages(raw.website)
            for link in page.external_links
            if (host := host_of(link)) is not None
            and not any(same_site(str(link), own) for own in own_hosts)
            and not is_social_network(host)
            and not is_google_profile_link(str(link), profile_url)
        }
    )
    outcome = CheckOutcome.PASSED if partners else CheckOutcome.FAILED
    return outcome, {"partner_hosts": listed(partners), "partner_host_count": len(partners)}


CHECKS: dict[str, CheckFunction] = {
    "i1": referring_domains,
    "i2": local_links,
    "i3": harmful_links,
    "i4": unlinked_mentions,
    "i5": visibility_history,
    "i6": outbound_partner_links,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
