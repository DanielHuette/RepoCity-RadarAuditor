"""Checks of area l: recht (ids l1..).

Context: One pure function per catalogue check; each returns outcome and measured values.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

import re

from radarauditor.collectors.snapshot import PageSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    CheckFunction,
    CheckReturn,
    Thresholds,
    at_least_outcome,
    click_depths,
    content_pages,
    dispatch,
    find_page,
    fold,
    listed,
    nap_value,
    not_measurable,
    number,
    page_text,
    postal_code,
    url_key,
)

PHONE_PATTERN = re.compile(r"(?:\+49|\b0)[\d\s/()-]{6,}\d")
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
KNOWN_SERVICES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "Google Maps": (("maps.googleapis.com", "maps.google.", "maps.gstatic.com"), ("google maps",)),
    "Google Fonts": (("fonts.googleapis.com", "fonts.gstatic.com"), ("google fonts",)),
    "Google Analytics": (
        ("google-analytics.com", "googletagmanager.com"),
        ("google analytics", "tag manager"),
    ),
    "Facebook": (("facebook.com", "facebook.net", "fbcdn.net"), ("facebook",)),
    "YouTube": (("youtube.com", "youtube-nocookie.com", "ytimg.com"), ("youtube",)),
}
FONT_HOSTS = frozenset({"fonts.googleapis.com", "fonts.gstatic.com"})
TRACKING_HOST_MARKERS = ("googletagmanager.com", "google-analytics.com")
CONSENT_MARKERS = ("cookie", "einwilligung")
RESPONSIBLE_PATTERN = re.compile(
    r"(\bgmbh\b|\bug\b|\bkg\b|\bohg\b|\bgbr\b|\bag\b|e\.\s?k\.|inhaber|geschaeftsfuehr|"
    r"vertreten durch|verantwortlich)"
)


def legal_notice_elements(page: PageSnapshot, company: str | None) -> dict[str, bool]:
    """Which mandatory elements the legal notice text shows."""
    text = page_text(page)
    raw_text = " ".join([page.text_excerpt, *(heading for _, heading in page.headings)])
    return {
        "postal_code": postal_code(raw_text) is not None,
        "phone_or_email": bool(
            page.tel_links
            or page.mailto_links
            or PHONE_PATTERN.search(raw_text)
            or EMAIL_PATTERN.search(raw_text)
        ),
        "responsible": bool(
            RESPONSIBLE_PATTERN.search(text) or (company is not None and fold(company) in text)
        ),
    }


def legal_notice(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """l1: the Impressum exists within max_clicks and shows postal code, contact and responsible."""
    if raw.website is None:
        return not_measurable("no_website")
    website = raw.website
    if website.legal_notice_url is None:
        return CheckOutcome.FAILED, {"legal_notice_url": None}
    page = find_page(website, website.legal_notice_url)
    if page is None:
        return not_measurable(
            "legal_notice_not_crawled", legal_notice_url=str(website.legal_notice_url)
        )
    limit = number(thresholds, "max_clicks")
    depth = click_depths(website).get(url_key(page.url))
    elements = legal_notice_elements(page, nap_value(website.footer_nap, "name"))
    complete = all(elements.values()) and depth is not None and depth <= limit
    return (CheckOutcome.PASSED if complete else CheckOutcome.PARTIAL), {
        "legal_notice_url": str(page.url),
        "click_depth": depth,
        "max_clicks": limit,
        "missing_elements": [name for name, present in elements.items() if not present],
    }


def third_party_hosts(raw: RawAudit) -> set[str]:
    """Hosts of third-party resources loaded by any successful page."""
    if raw.website is None:
        return set()
    return {
        host.casefold() for page in content_pages(raw.website) for host in page.third_party_hosts
    }


def services_in_use(hosts: set[str]) -> list[str]:
    """Known services whose hosts appear among the third-party hosts, in catalogue order."""
    return [
        service
        for service, (host_markers, _) in KNOWN_SERVICES.items()
        if any(marker in host for host in hosts for marker in host_markers)
    ]


def privacy_policy(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """l2: the privacy policy names every known third-party service the site loads.

    Some services missing is partial, all missing fails; a site without known services passes
    once the policy exists.
    """
    if raw.website is None:
        return not_measurable("no_website")
    website = raw.website
    if website.privacy_url is None:
        return CheckOutcome.FAILED, {"privacy_url": None}
    page = find_page(website, website.privacy_url)
    if page is None:
        return not_measurable("privacy_not_crawled", privacy_url=str(website.privacy_url))
    in_use = services_in_use(third_party_hosts(raw))
    text = fold(page.text_excerpt)
    missing = [
        service
        for service in in_use
        if not any(fold(name) in text for name in KNOWN_SERVICES[service][1])
    ]
    if not missing:
        outcome = CheckOutcome.PASSED
    elif len(missing) == len(in_use):
        outcome = CheckOutcome.FAILED
    else:
        outcome = CheckOutcome.PARTIAL
    return outcome, {
        "privacy_url": str(page.url),
        "services_in_use": in_use,
        "services_not_named": missing,
    }


def has_consent_hint(raw: RawAudit) -> bool:
    """True if any page mentions cookies or consent in its text or headings."""
    if raw.website is None:
        return False
    return any(
        marker in fold(" ".join([page.text_excerpt, *(text for _, text in page.headings)]))
        for page in content_pages(raw.website)
        for marker in CONSENT_MARKERS
    )


def consent_and_fonts(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """l3: no Google Fonts from Google's servers, and tracking only with a visible consent hint.

    Fonts loaded from fonts.googleapis.com or fonts.gstatic.com fail; Google Analytics or Tag
    Manager without any page mentioning cookies or consent is partial.
    """
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    hosts = third_party_hosts(raw)
    font_hosts = sorted(hosts & FONT_HOSTS)
    tracking_hosts = sorted(
        host for host in hosts if any(marker in host for marker in TRACKING_HOST_MARKERS)
    )
    consent_hint = has_consent_hint(raw)
    if font_hosts:
        outcome = CheckOutcome.FAILED
    elif tracking_hosts and not consent_hint:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.PASSED
    return outcome, {
        "font_hosts": font_hosts,
        "tracking_hosts": listed(tracking_hosts),
        "consent_hint": consent_hint,
        "third_party_hosts": listed(sorted(hosts)),
    }


def accessibility(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """l4: the Lighthouse accessibility score reaches min_score."""
    if raw.pagespeed is None:
        return not_measurable("no_pagespeed")
    minimum = number(thresholds, "min_score")
    return at_least_outcome(raw.pagespeed.accessibility, minimum), {
        "accessibility_score": raw.pagespeed.accessibility,
        "min_score": minimum,
    }


CHECKS: dict[str, CheckFunction] = {
    "l1": legal_notice,
    "l2": privacy_policy,
    "l3": consent_and_fonts,
    "l4": accessibility,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
