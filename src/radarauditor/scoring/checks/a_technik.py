"""Checks of area a: technik (ids a1..).

Context: One pure function per catalogue check; each returns outcome and measured values.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

import re
from urllib.robotparser import RobotFileParser

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    CheckFunction,
    CheckReturn,
    Thresholds,
    at_least_outcome,
    at_most_outcome,
    combine,
    content_pages,
    dispatch,
    listed,
    not_measurable,
    number,
    share_outcome,
    url_key,
    url_path,
)

MOBILE_USABLE_SCORE = 50
ADMIN_PATH_PATTERN = re.compile(
    r"/(wp-admin|wp-login\.php|admin|administrator|backend|phpmyadmin)\b"
)


def https_single_host(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """a1: the site uses HTTPS and every host variant ends on one address."""
    if raw.website is None:
        return not_measurable("no_website")
    website = raw.website
    parts = [website.uses_https, website.redirects_to_single_host]
    outcome = combine(CheckOutcome.PASSED if part else CheckOutcome.FAILED for part in parts)
    return outcome, {
        "uses_https": website.uses_https,
        "redirects_to_single_host": website.redirects_to_single_host,
    }


def loading_speed(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """a2: LCP, CLS, INP and the mobile score meet their limits; a mix of results is partial."""
    if raw.pagespeed is None:
        return not_measurable("no_pagespeed")
    speed = raw.pagespeed
    limits = {
        "lcp_ms_max": number(thresholds, "lcp_ms_max"),
        "cls_max": number(thresholds, "cls_max"),
        "inp_ms_max": number(thresholds, "inp_ms_max"),
        "mobile_score_min": number(thresholds, "mobile_score_min"),
    }
    outcomes = [
        at_most_outcome(speed.lcp_ms, limits["lcp_ms_max"]),
        at_most_outcome(speed.cls, limits["cls_max"]),
        at_least_outcome(speed.mobile_performance, limits["mobile_score_min"]),
    ]
    if speed.inp_ms is not None:
        outcomes.append(at_most_outcome(speed.inp_ms, limits["inp_ms_max"]))
    return combine(outcomes), {
        "lcp_ms": speed.lcp_ms,
        "cls": speed.cls,
        "inp_ms": speed.inp_ms,
        "mobile_performance": speed.mobile_performance,
        "desktop_performance": speed.desktop_performance,
        **limits,
    }


def mobile_display(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """a3: every page sets a viewport and the mobile score is not poor; a mix is partial.

    The catalogue may set mobile_score_min; without it Lighthouse's lower bound of the
    "needs improvement" band applies.
    """
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    without_viewport = [str(page.url) for page in pages if not page.has_viewport_meta]
    outcomes = [share_outcome(len(pages) - len(without_viewport), len(pages))]
    minimum = (
        number(thresholds, "mobile_score_min")
        if "mobile_score_min" in thresholds
        else MOBILE_USABLE_SCORE
    )
    mobile = raw.pagespeed.mobile_performance if raw.pagespeed is not None else None
    if mobile is not None:
        outcomes.append(at_least_outcome(mobile, minimum))
    return combine(outcomes), {
        "pages_without_viewport": listed(without_viewport),
        "pages_without_viewport_count": len(without_viewport),
        "pages_total": len(pages),
        "mobile_performance": mobile,
        "mobile_score_min": minimum,
    }


def robots_and_sitemap(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """a4: robots.txt exists without blocking everything, and a sitemap is declared."""
    if raw.website is None:
        return not_measurable("no_website")
    website = raw.website
    blocks_all = False
    if website.robots_txt is not None:
        parser = RobotFileParser()
        parser.parse(website.robots_txt.splitlines())
        blocks_all = not parser.can_fetch("Googlebot", str(website.start_url))
    if blocks_all:
        outcome = CheckOutcome.FAILED
    else:
        parts = [website.robots_txt is not None, bool(website.sitemap_urls)]
        outcome = combine(CheckOutcome.PASSED if part else CheckOutcome.FAILED for part in parts)
    return outcome, {
        "has_robots_txt": website.robots_txt is not None,
        "sitemap_count": len(website.sitemap_urls),
        "blocks_google": blocks_all,
    }


def broken_links(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """a5: linked pages that answer with an error, plus crawl errors, stay within max_broken."""
    if raw.website is None or not raw.website.pages:
        return not_measurable("no_website")
    website = raw.website
    linked = {url_key(link) for page in website.pages for link in page.internal_links}
    broken = [
        str(page.url)
        for page in website.pages
        if page.status_code >= 400 and url_key(page.url) in linked
    ]
    bad = len(broken) + len(website.crawl_errors)
    limit = number(thresholds, "max_broken")
    total = len(website.pages) + len(website.crawl_errors)
    if bad <= limit:
        outcome = CheckOutcome.PASSED
    else:
        outcome = share_outcome(total - bad, total)
        if outcome is CheckOutcome.PASSED:
            outcome = CheckOutcome.PARTIAL
    return outcome, {
        "broken_urls": listed(broken),
        "broken_count": len(broken),
        "crawl_errors": listed(website.crawl_errors),
        "pages_total": len(website.pages),
        "max_broken": limit,
    }


def canonical_per_page(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """a6: every successful page declares a canonical URL."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    missing = [str(page.url) for page in pages if page.canonical is None]
    return share_outcome(len(pages) - len(missing), len(pages)), {
        "pages_missing_canonical": listed(missing),
        "pages_missing_count": len(missing),
        "pages_total": len(pages),
    }


def images_optimised(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """a7: images have alt text and none exceeds the size limit used by the crawler."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    max_missing_alt = number(thresholds, "max_missing_alt")
    missing_alt = sum(page.images_without_alt for page in pages)
    oversized = sum(page.oversized_images for page in pages)
    pages_with_issues = [
        str(page.url) for page in pages if page.images_without_alt or page.oversized_images
    ]
    if missing_alt <= max_missing_alt and oversized == 0:
        outcome = CheckOutcome.PASSED
    else:
        outcome = share_outcome(len(pages) - len(pages_with_issues), len(pages))
        if outcome is CheckOutcome.PASSED:
            outcome = CheckOutcome.PARTIAL
    return outcome, {
        "images_without_alt": missing_alt,
        "oversized_images": oversized,
        "pages_with_issues": listed(pages_with_issues),
        "pages_total": len(pages),
        "max_image_kb": number(thresholds, "max_image_kb"),
        "max_missing_alt": max_missing_alt,
    }


def readable_without_javascript(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """a8: pages delivered as HTML carry words and headings without running scripts."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    empty = [str(page.url) for page in pages if page.word_count == 0 or not page.headings]
    return share_outcome(len(pages) - len(empty), len(pages)), {
        "pages_without_text": listed(empty),
        "pages_without_text_count": len(empty),
        "pages_total": len(pages),
    }


def language_marked(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """a9: every page declares German as its language."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    unmarked = [
        str(page.url) for page in pages if not (page.lang or "").casefold().startswith("de")
    ]
    return share_outcome(len(pages) - len(unmarked), len(pages)), {
        "pages_without_german_lang": listed(unmarked),
        "pages_total": len(pages),
    }


def secure_content(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """a10: no open admin pages were reachable and an HTTPS site links no page over plain HTTP."""
    if raw.website is None or not raw.website.pages:
        return not_measurable("no_website")
    website = raw.website
    open_admin = [
        str(page.url)
        for page in content_pages(website)
        if ADMIN_PATH_PATTERN.search(url_path(page.url))
    ]
    insecure_links = sorted(
        {
            str(link)
            for page in website.pages
            for link in page.internal_links
            if website.uses_https and str(link).startswith("http://")
        }
    )
    if open_admin:
        outcome = CheckOutcome.FAILED
    elif insecure_links:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.PASSED
    return outcome, {
        "open_admin_pages": listed(open_admin),
        "insecure_links": listed(insecure_links),
        "insecure_link_count": len(insecure_links),
    }


CHECKS: dict[str, CheckFunction] = {
    "a1": https_single_host,
    "a2": loading_speed,
    "a3": mobile_display,
    "a4": robots_and_sitemap,
    "a5": broken_links,
    "a6": canonical_per_page,
    "a7": images_optimised,
    "a8": readable_without_javascript,
    "a9": language_marked,
    "a10": secure_content,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
