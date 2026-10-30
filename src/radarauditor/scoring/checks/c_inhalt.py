"""Checks of area c: inhalt (ids c1..).

Context: One pure function per catalogue check; each returns outcome and measured values.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

import re

from radarauditor.collectors.snapshot import PageSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    NO_PAID_DATA,
    CheckFunction,
    CheckReturn,
    Thresholds,
    at_least_outcome,
    business_city,
    combine,
    content_pages,
    dispatch,
    fold,
    h1_texts,
    listed,
    not_measurable,
    number,
    page_matches_terms,
    page_text,
    service_pages,
    service_terms,
    share_outcome,
    tokens,
    url_path,
)

GAP_MIN_VOLUME = 10
GAP_FAIL_COUNT = 10
QUESTION_MATCH_SHARE = 0.6
MIN_QUESTION_WORD_LENGTH = 5
YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
ABOUT_PATH_PATTERN = re.compile(r"(ueber-uns|uber-uns|about|team)")
QUALIFICATION_PATTERN = re.compile(
    r"(meister|zertifiziert|ausbildung|qualifikation|jahre erfahrung|innung|gepruef|diplom|"
    r"geselle|fachbetrieb|approbation)"
)
CONTACT_PATH_PATTERN = re.compile(r"(kontakt|contact|anfahrt)")
GUIDE_PATH_PATTERN = re.compile(r"/(blog|ratgeber|magazin|news|aktuelles|tipps|wissen)/.+")


def page_per_service(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c1: each tracked search phrase has a page whose address, title or h1 names the service."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    terms = service_terms(raw)
    if not terms:
        return not_measurable("no_search_phrases")
    pages = content_pages(raw.website)
    uncovered = [
        phrase
        for phrase, words in terms.items()
        if not any(page_matches_terms(page, words) for page in pages)
    ]
    return share_outcome(len(terms) - len(uncovered), len(terms)), {
        "services_without_page": listed(uncovered),
        "services_total": len(terms),
    }


def _names_phrase(page: PageSnapshot, phrase: str) -> bool:
    """True if title or main heading carry every word of the phrase (service and city)."""
    focus = set(tokens(" ".join([page.title or "", *h1_texts(page)])))
    return set(tokens(phrase)) <= focus


def paid_demand_pages(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c2: every primary phrase with at least min_volume searches has a page naming it.

    The page's title or main heading must contain the service and the city; passed when all such
    phrases have one, partial from half.
    """
    if raw.paid is None:
        return not_measurable(NO_PAID_DATA)
    if not raw.paid.volumes:
        return not_measurable("no_volumes")
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    minimum = number(thresholds, "min_volume")
    demanded = {
        phrase: volume
        for phrase in raw.paid.primary_phrases
        if (volume := raw.paid.volumes.get(phrase)) is not None and volume >= minimum
    }
    pages = content_pages(raw.website)
    uncovered = [
        phrase for phrase in demanded if not any(_names_phrase(page, phrase) for page in pages)
    ]
    return share_outcome(len(demanded) - len(uncovered), len(demanded)), {
        "phrases_with_demand": demanded,
        "phrases_without_page": listed(uncovered),
        "min_volume": minimum,
    }


def service_page_words(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c3: every service page has at least min_words words; partial if most do or all are close."""
    pages = service_pages(raw)
    if not pages:
        return not_measurable("no_service_pages")
    minimum = number(thresholds, "min_words")
    short = {str(page.url): page.word_count for page in pages if page.word_count < minimum}
    outcome = share_outcome(len(pages) - len(short), len(pages))
    lowest = min(page.word_count for page in pages)
    if outcome is CheckOutcome.FAILED and at_least_outcome(lowest, minimum) is CheckOutcome.PARTIAL:
        outcome = CheckOutcome.PARTIAL
    return outcome, {
        "pages_below_min_words": dict(list(short.items())[:10]),
        "service_pages_total": len(pages),
        "lowest_word_count": lowest,
        "min_words": minimum,
    }


def _term_density(page: PageSnapshot, words: list[str], limit: float) -> float | None:
    """Share of the excerpt's words that are service words; None for excerpts too short to judge.

    A single mention in fewer than 1 / limit words already exceeds the limit, so such excerpts
    say nothing about stuffing.
    """
    excerpt = tokens(page.text_excerpt)
    if len(excerpt) < 1 / limit:
        return None
    return sum(1 for word in excerpt if word in words) / len(excerpt)


def terms_in_text(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c4: each service word appears in the text and no page exceeds max_density."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    terms = service_terms(raw)
    if not terms:
        return not_measurable("no_search_phrases")
    pages = content_pages(raw.website)
    limit = number(thresholds, "max_density")
    site_words = set(tokens(" ".join(page_text(page) for page in pages)))
    missing = [phrase for phrase, words in terms.items() if not set(words) <= site_words]
    stuffed = {
        str(page.url): round(density, 3)
        for page in pages
        for words in terms.values()
        if (density := _term_density(page, words, limit)) is not None and density > limit
    }
    outcomes = [
        share_outcome(len(terms) - len(missing), len(terms)),
        CheckOutcome.FAILED if stuffed else CheckOutcome.PASSED,
    ]
    return combine(outcomes), {
        "phrases_missing_in_text": listed(missing),
        "pages_over_density": dict(list(stuffed.items())[:10]),
        "phrases_total": len(terms),
        "max_density": limit,
    }


def _answered(question: str, site_words: set[str]) -> bool:
    """True if most significant words of the question appear on the site."""
    significant = [word for word in tokens(question) if len(word) >= MIN_QUESTION_WORD_LENGTH]
    if not significant:
        return False
    hits = sum(1 for word in significant if word in site_words)
    return hits / len(significant) >= QUESTION_MATCH_SHARE


def questions_answered(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c5: the questions Google shows under People also ask are covered by the site's text."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    questions = sorted({question for serp in raw.serps for question in serp.people_also_ask})
    if not questions:
        return not_measurable("no_questions")
    site_words = set(tokens(" ".join(page_text(page) for page in content_pages(raw.website))))
    open_questions = [question for question in questions if not _answered(question, site_words)]
    return share_outcome(len(questions) - len(open_questions), len(questions)), {
        "unanswered_questions": listed(open_questions),
        "questions_total": len(questions),
    }


def local_references(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c6: service pages mention the city in their text."""
    city = business_city(raw)
    if city is None:
        return not_measurable("no_city")
    pages = service_pages(raw)
    if not pages:
        return not_measurable("no_service_pages")
    without = [str(page.url) for page in pages if fold(city) not in page_text(page)]
    return share_outcome(len(pages) - len(without), len(pages)), {
        "pages_without_city": listed(without),
        "service_pages_total": len(pages),
        "city": city,
    }


def visible_currency(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c7: the newest year shown on the site is this or last year; two years back is partial."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    current = raw.collected_at.year
    years = [
        int(year)
        for page in content_pages(raw.website)
        for year in YEAR_PATTERN.findall(page.text_excerpt)
        if int(year) <= current
    ]
    latest = max(years) if years else None
    if latest is not None and latest >= current - 1:
        outcome = CheckOutcome.PASSED
    elif latest is not None and latest == current - 2:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {"latest_year_on_site": latest, "audit_year": current}


def about_page(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c8: an about page exists and names qualifications."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    candidates = [
        page for page in content_pages(raw.website) if ABOUT_PATH_PATTERN.search(url_path(page.url))
    ]
    qualified = [page for page in candidates if QUALIFICATION_PATTERN.search(page_text(page))]
    if qualified:
        outcome = CheckOutcome.PASSED
    elif candidates:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {
        "about_page": str(candidates[0].url) if candidates else None,
        "mentions_qualification": bool(qualified),
    }


def contact_page(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c9: a contact page exists and carries a tappable phone link."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    candidates = [
        page
        for page in content_pages(raw.website)
        if CONTACT_PATH_PATTERN.search(url_path(page.url))
    ]
    with_phone = [page for page in candidates if page.tel_links]
    parts = [bool(candidates), bool(with_phone)]
    outcome = combine(CheckOutcome.PASSED if part else CheckOutcome.FAILED for part in parts)
    return outcome, {
        "contact_page": str(candidates[0].url) if candidates else None,
        "has_tel_link": bool(with_phone),
    }


def local_guides(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c10: at least min_posts guide or blog articles exist."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    minimum = number(thresholds, "min_posts")
    posts = [
        page
        for page in content_pages(raw.website)
        if GUIDE_PATH_PATTERN.search(url_path(page.url).rstrip("/") + "/")
    ]
    city = business_city(raw)
    local = [page for page in posts if city is not None and fold(city) in page_text(page)]
    return at_least_outcome(len(posts), minimum), {
        "guide_posts": len(posts),
        "guide_posts_with_city": len(local),
        "min_posts": minimum,
    }


def thin_pages(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c11: no content page except the legal pages has fewer than min_words words."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    website = raw.website
    minimum = number(thresholds, "min_words")
    legal = {str(url) for url in (website.legal_notice_url, website.privacy_url) if url is not None}
    pages = [page for page in content_pages(website) if str(page.url) not in legal]
    thin = {str(page.url): page.word_count for page in pages if page.word_count < minimum}
    outcome = share_outcome(len(pages) - len(thin), len(pages))
    if thin and outcome is CheckOutcome.PASSED:
        outcome = CheckOutcome.PARTIAL
    return outcome, {
        "thin_pages": dict(list(thin.items())[:10]),
        "thin_page_count": len(thin),
        "pages_total": len(pages),
        "min_words": minimum,
    }


def paid_keyword_gap(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """c12: search phrases competitors rank for and the customer does not.

    Counts gap phrases with at least GAP_MIN_VOLUME searches: none passes, up to nine is partial,
    ten or more fails.
    """
    if raw.paid is None:
        return not_measurable(NO_PAID_DATA)
    if raw.paid.keyword_gap is None:
        return not_measurable("no_keyword_gap")
    relevant = [gap for gap in raw.paid.keyword_gap if gap.volume >= GAP_MIN_VOLUME]
    if not relevant:
        outcome = CheckOutcome.PASSED
    elif len(relevant) < GAP_FAIL_COUNT:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {
        "gap_keywords": len(relevant),
        "top_gap_keywords": [gap.model_dump() for gap in relevant[:10]],
        "min_volume": GAP_MIN_VOLUME,
    }


CHECKS: dict[str, CheckFunction] = {
    "c1": page_per_service,
    "c2": paid_demand_pages,
    "c3": service_page_words,
    "c4": terms_in_text,
    "c5": questions_answered,
    "c6": local_references,
    "c7": visible_currency,
    "c8": about_page,
    "c9": contact_page,
    "c10": local_guides,
    "c11": thin_pages,
    "c12": paid_keyword_gap,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
