"""Checks of area g: bewertungen (ids g1..).

Context: One pure function per catalogue check; each returns outcome and measured values. Reviews
read on Google Maps in the browser are the newest ones, each with the owner's reply or an empty
reply field, so g3, g4 and g5 measure them directly. The Places API (New), the fallback when Maps
blocks the browser, returns no owner replies, so a review sample without any reply field cannot
show whether the owner answers; g4 and g5 then give PARTIAL at most and record
reply_data_available false instead of counting every review as unanswered. In an audit with
customer access, g4 and g5 read the profile's own reviews with the real replies instead. g1
compares with every business of the Google Maps result list for the primary keyword; audits
collected before that list was recorded compare with the stored competitor profiles.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean

from dateutil.parser import isoparse

from radarauditor.collectors.snapshot import ListedBusiness, MapResultList, PlaceSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    MOST_SHARE,
    CheckFunction,
    CheckReturn,
    Thresholds,
    at_least_outcome,
    business_city,
    dispatch,
    fold,
    listed,
    names_match,
    not_measurable,
    number,
    place_routes,
    route_entry,
    service_terms,
    share_outcome,
    tokens,
)

STRONGEST_COMPETITORS = 3
DAYS_PER_MONTH = 30.44
NEGATIVE_RATING_MAX = 2
REPLY_KEY_MARKERS = ("reply", "response")
REPLY_TIME_KEYS = ("publishTime", "updateTime", "createTime", "time")
REVIEW_PLATFORMS = frozenset(
    fold(name)
    for name in (
        "ProvenExpert",
        "Trustpilot",
        "kununu",
        "Jameda",
        "Doctolib",
        "TripAdvisor",
        "Werkenntdenbesten",
        "MyHammer",
        "Check24 Profis",
        "Anwalt.de",
    )
)
UNANSWERED_EXAMPLES = 3
EXAMPLE_TEXT_CHARS = 160
SUSPICIOUS_REVIEW_COUNT = 20
SUSPICIOUS_EMPTY_FIVE_STAR_SHARE = 0.8


def parse_time(value: object) -> datetime | None:
    """Timezone-aware datetime from an ISO string or datetime; None if unreadable."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = isoparse(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def review_text(review: Mapping[str, object]) -> str:
    """Review text, whether stored as a string or as a Places object with a text field."""
    for key in ("text", "originalText"):
        value = review.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict) and isinstance(value.get("text"), str):
            return str(value["text"])
    return ""


def review_rating(review: Mapping[str, object]) -> float | None:
    """Star rating of a review."""
    value = review.get("rating")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def has_reply_field(review: Mapping[str, object]) -> bool:
    """True if the review carries a reply field at all, answered or not."""
    return any(marker in key.casefold() for key in review for marker in REPLY_KEY_MARKERS)


def reply_data_available(reviews: list[dict[str, object]]) -> bool:
    """True if the source reports owner replies for this review sample."""
    return any(has_reply_field(review) for review in reviews)


def owner_reply(review: Mapping[str, object]) -> object | None:
    """The owner's reply under any reply-like key, or None."""
    for key, value in review.items():
        if value and any(marker in key.casefold() for marker in REPLY_KEY_MARKERS):
            return value
    return None


def reply_delay_days(review: Mapping[str, object]) -> float | None:
    """Days between review and reply, if both timestamps are known."""
    reply = owner_reply(review)
    posted = parse_time(review.get("publishTime"))
    if not isinstance(reply, dict) or posted is None:
        return None
    for key in REPLY_TIME_KEYS:
        replied = parse_time(reply.get(key))
        if replied is not None:
            return max((replied - posted).total_seconds() / 86400, 0.0)
    return None


def judged_reviews(raw: RawAudit) -> tuple[list[dict[str, object]], str] | None:
    """Reviews for the reply checks and their source.

    The profile's own reviews ("profile") when the audit read them with customer access, else the
    public sample with the route that read it ("browser" or "places_api"); None without either.
    """
    if raw.access is not None and raw.access.owner_reviews:
        return raw.access.owner_reviews, "profile"
    if raw.place is None:
        return None
    return raw.place.recent_reviews, sample_source(raw)


def sample_source(raw: RawAudit) -> str:
    """Route of the public review sample: "browser" (newest reviews) or "places_api"."""
    route = raw.place.route_of("recent_reviews") if raw.place is not None else None
    return route or "places_api"


def _is_customer_listing(business: ListedBusiness, place: PlaceSnapshot) -> bool:
    """True if a listed business is the customer, by its mark, its place id or its name."""
    return (
        business.is_customer
        or (business.place_id is not None and business.place_id == place.place_id)
        or names_match(business.name, place.name)
    )


def _unique_names(pairs: list[tuple[str, int]]) -> dict[str, int]:
    """Name to count; a name listed twice gets a running number, so no business is lost."""
    counts: dict[str, int] = {}
    for name, count in pairs:
        key, running_number = name, 2
        while key in counts:
            key = f"{name} ({running_number})"
            running_number += 1
        counts[key] = count
    return counts


@dataclass(frozen=True)
class _ReviewComparison:
    """Businesses the customer's review count is compared with, and where they come from."""

    pairs: list[tuple[str, int]]
    uncounted: list[str]
    source: str


def _listed_comparison(
    results: MapResultList, place: PlaceSnapshot
) -> _ReviewComparison | CheckReturn:
    others = [
        business for business in results.businesses if not _is_customer_listing(business, place)
    ]
    if not others:
        return not_measurable("no_listed_businesses", phrase=results.phrase)
    pairs = [
        (business.name, business.review_count)
        for business in others
        if business.review_count is not None
    ]
    uncounted = [business.name for business in others if business.review_count is None]
    if not pairs:
        return not_measurable("no_review_counts", without_review_count=listed(uncounted))
    return _ReviewComparison(pairs, uncounted, "maps_result_list")


def _profile_comparison(competitors: list[PlaceSnapshot]) -> _ReviewComparison | CheckReturn:
    pairs = [(place.name, place.review_count) for place in competitors[:STRONGEST_COMPETITORS]]
    if not pairs:
        return not_measurable("no_competitors")
    return _ReviewComparison(pairs, [], "competitor_profiles")


def review_count_vs_competitors(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """g1: review count reaches the average of every other business on the Maps result list.

    The list is the one Google Maps shows for the primary keyword, the customer left out;
    businesses whose count the list does not show are named but not averaged. Within 20 % below
    the average is partial, as for every lower limit. Without the list (audits collected before
    it was recorded) the three strongest stored competitor profiles are the comparison.
    """
    if raw.place is None:
        return not_measurable("no_place")
    comparison = (
        _listed_comparison(raw.map_results, raw.place)
        if raw.map_results is not None
        else _profile_comparison(raw.competitor_places)
    )
    if not isinstance(comparison, _ReviewComparison):
        return comparison
    counts = _unique_names(comparison.pairs)
    average = mean(counts.values())
    measured: dict[str, object] = {
        "customer_reviews": raw.place.review_count,
        "competitors": counts,
        "competitor_average": round(average, 1),
        "compared_businesses": len(counts),
        "comparison_source": comparison.source,
    }
    if raw.map_results is not None:
        measured["phrase"] = raw.map_results.phrase
    if comparison.uncounted:
        measured["without_review_count"] = listed(comparison.uncounted)
    return at_least_outcome(raw.place.review_count, average), measured


def average_rating(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """g2: the average rating reaches min_rating; no rating at all fails."""
    if raw.place is None:
        return not_measurable("no_place")
    minimum = number(thresholds, "min_rating")
    rating = raw.place.rating
    outcome = CheckOutcome.FAILED if rating is None else at_least_outcome(rating, minimum)
    return outcome, {
        "rating": rating,
        "review_count": raw.place.review_count,
        "min_rating": minimum,
    }


def review_frequency(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """g3: reviews per month over the span of the sampled reviews reach min_per_month.

    The browser sample holds the newest reviews, so the rate is the real recent pace; the Places
    sample is Google's choice of relevant reviews and only an estimate.
    """
    if raw.place is None:
        return not_measurable("no_place")
    minimum = number(thresholds, "min_per_month")
    if raw.place.review_count == 0:
        return CheckOutcome.FAILED, {"reviews_per_month": 0.0, "min_per_month": minimum}
    dates = sorted(
        moment
        for review in raw.place.recent_reviews
        if (moment := parse_time(review.get("publishTime"))) is not None
    )
    if not dates:
        return not_measurable("no_review_dates")
    collected = parse_time(raw.collected_at) or dates[-1]
    months = max((collected - dates[0]).days / DAYS_PER_MONTH, 1.0)
    rate = len(dates) / months
    return at_least_outcome(rate, minimum), {
        "reviews_in_sample": len(dates),
        "oldest_review": dates[0].date().isoformat(),
        "newest_review": dates[-1].date().isoformat(),
        "reviews_per_month": round(rate, 2),
        "min_per_month": minimum,
        "review_source": sample_source(raw),
    }


def unanswered_examples(reviews: list[dict[str, object]]) -> list[dict[str, object]]:
    """Up to three of the most recent reviews without an owner reply, for the report.

    Reviews with a date come first, newest first; reviews without one follow in sample order.

    Returns:
        Entries with date (ISO day or Google's relative date, None if neither), stars and the
        first 160 characters of the text.
    """
    unanswered = [
        (review, parse_time(review.get("publishTime")))
        for review in reviews
        if owner_reply(review) is None
    ]
    dated: list[tuple[dict[str, object], datetime]] = sorted(
        ((review, posted) for review, posted in unanswered if posted is not None),
        key=lambda pair: pair[1],
        reverse=True,
    )
    undated = [(review, posted) for review, posted in unanswered if posted is None]
    examples: list[dict[str, object]] = []
    for review, posted in [*dated, *undated][:UNANSWERED_EXAMPLES]:
        relative = review.get("relativePublishTimeDescription")
        rating = review_rating(review)
        examples.append(
            {
                "date": posted.date().isoformat()
                if posted is not None
                else (relative if isinstance(relative, str) and relative else None),
                "stars": int(rating) if rating is not None and rating.is_integer() else rating,
                "text": review_text(review).strip()[:EXAMPLE_TEXT_CHARS],
            }
        )
    return examples


def reply_rate(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """g4: min_reply_rate of sampled reviews have a reply, on average within max_reply_days.

    Without reply data in the sample the result is PARTIAL with reply_data_available false.
    With reply data, unanswered_examples lists up to three of the most recent reviews without a
    reply (date, stars, first 160 characters).
    """
    judged = judged_reviews(raw)
    if judged is None:
        return not_measurable("no_place")
    reviews, source = judged
    if not reviews:
        return not_measurable("no_reviews")
    min_rate = number(thresholds, "min_reply_rate")
    max_days = number(thresholds, "max_reply_days")
    limits = {"min_reply_rate": min_rate, "max_reply_days": max_days, "review_source": source}
    if not reply_data_available(reviews):
        return CheckOutcome.PARTIAL, {
            "reviews_in_sample": len(reviews),
            "reply_data_available": False,
            **limits,
        }
    replied = sum(1 for review in reviews if owner_reply(review) is not None)
    rate = replied / len(reviews)
    average_delay = _average_reply_days(reviews)
    outcome = at_least_outcome(rate, min_rate)
    if outcome is CheckOutcome.PASSED and average_delay is not None and average_delay > max_days:
        outcome = CheckOutcome.PARTIAL
    return outcome, {
        "reviews_in_sample": len(reviews),
        "replied": replied,
        "reply_rate": round(rate, 3),
        "average_reply_days": round(average_delay, 1) if average_delay is not None else None,
        "reply_data_available": True,
        **limits,
        "unanswered_examples": unanswered_examples(reviews),
    }


def _average_reply_days(reviews: list[dict[str, object]]) -> float | None:
    delays = [delay for review in reviews if (delay := reply_delay_days(review)) is not None]
    return mean(delays) if delays else None


def replies_to_criticism(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """g5: every sampled review with two stars or fewer has a reply; tone is judged by hand.

    Without reply data in the sample the result is PARTIAL with reply_data_available false.
    """
    judged = judged_reviews(raw)
    if judged is None:
        return not_measurable("no_place")
    reviews, source = judged
    negative = [
        review
        for review in reviews
        if (rating := review_rating(review)) is not None and rating <= NEGATIVE_RATING_MAX
    ]
    if not negative:
        return not_measurable("no_negative_reviews")
    if not reply_data_available(reviews):
        return CheckOutcome.PARTIAL, {
            "negative_reviews": len(negative),
            "reply_data_available": False,
            "tone_checked": False,
            "review_source": source,
        }
    replied = sum(1 for review in negative if owner_reply(review) is not None)
    return share_outcome(replied, len(negative)), {
        "negative_reviews": len(negative),
        "replied": replied,
        "reply_data_available": True,
        "tone_checked": False,
        "review_source": source,
    }


def keywords_in_reviews(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """g6: at least half of the sampled reviews name a service word or the city; some is partial."""
    if raw.place is None:
        return not_measurable("no_place")
    texts = [text for review in raw.place.recent_reviews if (text := review_text(review))]
    if not texts:
        return not_measurable("no_review_texts")
    keywords = {word for words in service_terms(raw).values() for word in words}
    city = business_city(raw)
    if city:
        keywords.update(tokens(city))
    if not keywords:
        return not_measurable("no_keywords")
    mentioning = sum(1 for text in texts if keywords & set(tokens(text)))
    share = mentioning / len(texts)
    if share >= MOST_SHARE:
        outcome = CheckOutcome.PASSED
    elif mentioning:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {
        "reviews_with_keywords": mentioning,
        "reviews_with_text": len(texts),
        "keywords": sorted(keywords),
    }


def other_review_platforms(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """g7: the business is listed on at least one review platform besides Google."""
    checked = [hit for hit in raw.directories if fold(hit.directory) in REVIEW_PLATFORMS]
    if not checked:
        return not_measurable("no_review_platforms_checked")
    found = sorted(hit.directory for hit in checked if hit.found)
    outcome = CheckOutcome.PASSED if found else CheckOutcome.FAILED
    return outcome, {"found_on": found, "checked": sorted(hit.directory for hit in checked)}


def genuine_reviews(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """g8: no perfect 5.0 over many reviews and no sample dominated by empty five-star reviews."""
    if raw.place is None:
        return not_measurable("no_place")
    place = raw.place
    flags: list[str] = []
    if (
        place.rating is not None
        and place.rating >= 5
        and place.review_count >= SUSPICIOUS_REVIEW_COUNT
    ):
        flags.append("perfect_rating_many_reviews")
    reviews = place.recent_reviews
    empty_five = sum(
        1 for review in reviews if review_rating(review) == 5 and not review_text(review).strip()
    )
    if reviews and empty_five / len(reviews) >= SUSPICIOUS_EMPTY_FIVE_STAR_SHARE:
        flags.append("empty_five_star_reviews")
    outcomes = {0: CheckOutcome.PASSED, 1: CheckOutcome.PARTIAL}
    return outcomes.get(len(flags), CheckOutcome.FAILED), {
        "flags": flags,
        "rating": place.rating,
        "review_count": place.review_count,
    }


CHECKS: dict[str, CheckFunction] = {
    "g1": review_count_vs_competitors,
    "g2": average_rating,
    "g3": review_frequency,
    "g4": reply_rate,
    "g5": replies_to_criticism,
    "g6": keywords_in_reviews,
    "g7": other_review_platforms,
    "g8": genuine_reviews,
}


PROFILE_FIELDS = {
    "g1": "review_count",
    "g2": "rating",
    "g3": "recent_reviews",
    "g4": "recent_reviews",
    "g5": "recent_reviews",
    "g6": "recent_reviews",
    "g8": "rating",
}
COMPARED_CHECKS = frozenset({"g1"})


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id and add the route of the profile data it judged.

    Reviews read with customer access come from the profile itself and carry no route.
    """
    outcome, measured = dispatch(CHECKS, check_id, raw, thresholds)
    field = PROFILE_FIELDS.get(check_id)
    if (
        raw.place is None
        or field is None
        or outcome is CheckOutcome.NOT_MEASURABLE
        or measured.get("review_source") == "profile"
    ):
        return outcome, measured
    if check_id in COMPARED_CHECKS and measured.get("comparison_source") == "maps_result_list":
        list_route = raw.map_results.route if raw.map_results is not None else None
        return outcome, {**measured, **route_entry([raw.place.route_of(field), list_route])}
    compared = raw.competitor_places[:STRONGEST_COMPETITORS] if check_id in COMPARED_CHECKS else []
    return outcome, {**measured, **place_routes([raw.place, *compared], field)}
