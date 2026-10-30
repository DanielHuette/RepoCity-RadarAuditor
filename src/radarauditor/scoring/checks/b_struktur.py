"""Checks of area b: struktur (ids b1..).

Context: One pure function per catalogue check; each returns outcome and measured values.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

import re
from collections import Counter
from itertools import combinations
from urllib.parse import urlsplit

from radarauditor.collectors.snapshot import PageSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    CheckFunction,
    CheckReturn,
    Thresholds,
    business_city,
    click_depths,
    content_pages,
    dispatch,
    fold,
    h1_texts,
    listed,
    not_measurable,
    number,
    page_schema_types,
    share_outcome,
    start_page,
    tokens,
    url_key,
)

ID_SEGMENT_PATTERN = re.compile(r"^(\d+|[0-9a-f]{8,}|.*\.(php|aspx?|jsp|cfm))$")
BREADCRUMB_TEXT_PATTERN = re.compile(r"(startseite|home)\s*[>›»/|]")
OPEN_GRAPH_REQUIRED = ("title", "description", "image")


def single_h1_with_city(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """b1: every page has exactly one main heading; the start page's names the city."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    wrong = [str(page.url) for page in pages if len(h1_texts(page)) != 1]
    outcome = share_outcome(len(pages) - len(wrong), len(pages))
    city = business_city(raw)
    start = start_page(raw.website)
    start_h1 = " ".join(h1_texts(start)) if start is not None else ""
    start_has_city = city is not None and fold(city) in fold(start_h1)
    if city is not None and not start_has_city and outcome is CheckOutcome.PASSED:
        outcome = CheckOutcome.PARTIAL
    return outcome, {
        "pages_without_single_h1": listed(wrong),
        "pages_without_single_h1_count": len(wrong),
        "pages_total": len(pages),
        "city": city,
        "start_page_h1": start_h1 or None,
        "start_page_h1_has_city": start_has_city,
    }


def _length_problems(
    pages: list[PageSnapshot], values: dict[str, str | None], low: float, high: float
) -> dict[str, list[str]]:
    """Group page URLs by missing, too short, too long and duplicate text."""
    present = {url: text.strip() for url, text in values.items() if text and text.strip()}
    counts = Counter(fold(text) for text in present.values())
    problems: dict[str, list[str]] = {
        "missing": [],
        "too_short": [],
        "too_long": [],
        "duplicate": [],
    }
    for page in pages:
        url = str(page.url)
        text = present.get(url)
        if text is None:
            problems["missing"].append(url)
        elif len(text) < low:
            problems["too_short"].append(url)
        elif len(text) > high:
            problems["too_long"].append(url)
        elif counts[fold(text)] > 1:
            problems["duplicate"].append(url)
    return problems


def _text_length_check(
    pages: list[PageSnapshot], values: dict[str, str | None], thresholds: Thresholds
) -> CheckReturn:
    """Shared rule of b2 and b3: length within bounds and unique on every page."""
    low, high = number(thresholds, "min_chars"), number(thresholds, "max_chars")
    problems = _length_problems(pages, values, low, high)
    bad = {url for urls in problems.values() for url in urls}
    measured: dict[str, object] = {f"pages_{key}": listed(urls) for key, urls in problems.items()}
    measured.update(
        {
            "pages_ok": len(pages) - len(bad),
            "pages_total": len(pages),
            "min_chars": low,
            "max_chars": high,
        }
    )
    return share_outcome(len(pages) - len(bad), len(pages)), measured


def title_length(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """b2: every page title is 30 to 60 characters long and unique."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    return _text_length_check(pages, {str(page.url): page.title for page in pages}, thresholds)


def meta_description_length(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """b3: every meta description is 70 to 155 characters long and unique."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    values = {str(page.url): page.meta_description for page in pages}
    return _text_length_check(pages, values, thresholds)


def _has_heading_jump(page: PageSnapshot) -> bool:
    """True if a heading is more than one level deeper than the one before it."""
    previous = 0
    for level, _ in page.headings:
        if previous and level > previous + 1:
            return True
        previous = level
    return False


def heading_hierarchy(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """b4: no page skips a heading level, for example from h2 straight to h4."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    jumping = [str(page.url) for page in pages if _has_heading_jump(page)]
    return share_outcome(len(pages) - len(jumping), len(pages)), {
        "pages_with_jumps": listed(jumping),
        "pages_total": len(pages),
    }


def click_depth(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """b5: every page is reachable from the start page within max_depth clicks."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    limit = number(thresholds, "max_depth")
    depths = click_depths(raw.website)
    deep = [str(page.url) for page in pages if depths.get(url_key(page.url), limit + 1) > limit]
    reached = [depths[url_key(page.url)] for page in pages if url_key(page.url) in depths]
    return share_outcome(len(pages) - len(deep), len(pages)), {
        "pages_too_deep": listed(deep),
        "pages_too_deep_count": len(deep),
        "deepest_click_depth": max(reached) if reached else None,
        "pages_total": len(pages),
        "max_depth": limit,
    }


def _is_speaking(url: str) -> bool:
    """True if the URL has no query and no id-like path segment."""
    parts = urlsplit(url)
    if parts.query:
        return False
    segments = [segment for segment in parts.path.split("/") if segment]
    return not any(ID_SEGMENT_PATTERN.match(fold(segment)) for segment in segments)


def speaking_urls(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """b6: addresses consist of words, not ids, file extensions or query strings."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    cryptic = [str(page.url) for page in pages if not _is_speaking(str(page.url))]
    return share_outcome(len(pages) - len(cryptic), len(pages)), {
        "cryptic_urls": listed(cryptic),
        "pages_total": len(pages),
    }


def open_graph(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """b7: every page has an OpenGraph title, description and image."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    incomplete: dict[str, list[str]] = {}
    for page in pages:
        keys = {
            key.casefold().removeprefix("og:") for key, value in page.open_graph.items() if value
        }
        missing = [key for key in OPEN_GRAPH_REQUIRED if key not in keys]
        if missing:
            incomplete[str(page.url)] = missing
    return share_outcome(len(pages) - len(incomplete), len(pages)), {
        "pages_incomplete": dict(list(incomplete.items())[:10]),
        "pages_incomplete_count": len(incomplete),
        "pages_total": len(pages),
    }


def _similarity(first: set[str], second: set[str]) -> float:
    """Jaccard similarity of two word sets."""
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def duplicate_content(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """b8: no two pages share more than max_similarity of their words."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = [page for page in content_pages(raw.website) if page.text_excerpt.strip()]
    if len(pages) < 2:
        return not_measurable("too_few_pages", pages_total=len(pages))
    limit = number(thresholds, "max_similarity")
    words = {str(page.url): set(tokens(page.text_excerpt)) for page in pages}
    pairs: list[list[object]] = []
    involved: set[str] = set()
    for first, second in combinations(words, 2):
        score = _similarity(words[first], words[second])
        if score > limit:
            pairs.append([first, second, round(score, 3)])
            involved.update((first, second))
    return share_outcome(len(pages) - len(involved), len(pages)), {
        "duplicate_pairs": pairs[:10],
        "pages_involved": len(involved),
        "pages_total": len(pages),
        "max_similarity": limit,
    }


def breadcrumb_navigation(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """b9: subpages show a breadcrumb trail, as markup or as visible text."""
    if raw.website is None:
        return not_measurable("no_website")
    start = start_page(raw.website)
    subpages = [
        page for page in content_pages(raw.website) if start is None or page.url != start.url
    ]
    if not subpages:
        return not_measurable("no_subpages")
    without = [
        str(page.url)
        for page in subpages
        if "BreadcrumbList" not in page_schema_types(page)
        and not BREADCRUMB_TEXT_PATTERN.search(fold(page.text_excerpt))
    ]
    return share_outcome(len(subpages) - len(without), len(subpages)), {
        "pages_without_breadcrumb": listed(without),
        "subpages_total": len(subpages),
    }


def error_page(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """b10: a probed, unlinked address answers 404 with a page that links back into the site."""
    if raw.website is None:
        return not_measurable("no_website")
    linked = {url_key(link) for page in raw.website.pages for link in page.internal_links}
    probes = [
        page
        for page in raw.website.pages
        if page.status_code == 404 and url_key(page.url) not in linked
    ]
    if not probes:
        return not_measurable("needs_error_page")
    probe = probes[0]
    outcome = CheckOutcome.PASSED if probe.internal_links else CheckOutcome.FAILED
    return outcome, {
        "error_page_url": str(probe.url),
        "links_on_error_page": len(probe.internal_links),
    }


CHECKS: dict[str, CheckFunction] = {
    "b1": single_h1_with_city,
    "b2": title_length,
    "b3": meta_description_length,
    "b4": heading_hierarchy,
    "b5": click_depth,
    "b6": speaking_urls,
    "b7": open_graph,
    "b8": duplicate_content,
    "b9": breadcrumb_navigation,
    "b10": error_page,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
