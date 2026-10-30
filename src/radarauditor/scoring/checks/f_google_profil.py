"""Checks of area f: google_profil (ids f1..).

Context: One pure function per catalogue check; each returns outcome and measured values. The
profile comes from Google Maps in the browser or, when Maps blocked the browser, from the Places
API. Description, services, products, posts, questions, service areas and attributes are only on
the Maps page; a profile without them reports the field as not read instead of a value. The
browser shows only the primary category, so f3 needs the Places API's category list. The capture
date of the newest photo (f15) comes from the Maps photo viewer of the customer's own profile.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

from collections import Counter

from radarauditor.collectors.patterns import host_of
from radarauditor.collectors.snapshot import RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    CheckFunction,
    CheckReturn,
    Thresholds,
    at_least_outcome,
    at_most_outcome,
    business_city,
    dispatch,
    fold,
    listed,
    not_measurable,
    number,
    place_routes,
    service_terms,
    tokens,
)

GENERIC_PLACE_TYPES = frozenset({"point_of_interest", "establishment"})
STRONGEST_COMPETITORS = 3
DESCRIPTION_FRONT_CHARS = 250
CLOSED_STATUSES = {
    "CLOSED_PERMANENTLY": CheckOutcome.FAILED,
    "CLOSED_TEMPORARILY": CheckOutcome.PARTIAL,
}


def profile_exists(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f1: a Google profile was found and is operational; ownership is not public."""
    if raw.place is None:
        if any("place" in warning.casefold() for warning in raw.warnings):
            return not_measurable("collector_failed")
        return CheckOutcome.FAILED, {"profile_found": False}
    status = raw.place.business_status
    outcome = CLOSED_STATUSES.get(status or "", CheckOutcome.PASSED)
    return outcome, {
        "profile_found": True,
        "business_status": status,
        "ownership_checked": False,
    }


def primary_category(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f2: the primary category is the most common one among the three strongest competitors."""
    if raw.place is None:
        return not_measurable("no_place")
    competitors = raw.competitor_places[:STRONGEST_COMPETITORS]
    if not competitors:
        return not_measurable("no_competitors")
    # Maps shows category names, the Places API type ids; only one kind compares with the other.
    route = raw.place.route_of("primary_type")
    comparable = [place for place in competitors if place.route_of("primary_type") == route]
    if not comparable:
        return not_measurable("mixed_category_sources")
    competitor_types = {place.name: place.primary_type for place in comparable}
    counts = Counter(kind for kind in competitor_types.values() if kind)
    own = raw.place.primary_type
    if own is not None and counts and own == counts.most_common(1)[0][0]:
        outcome = CheckOutcome.PASSED
    elif own is not None and own in counts:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {"primary_type": own, "competitor_types": competitor_types}


def secondary_categories(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f3: the profile lists between min and max categories besides the primary one."""
    if raw.place is None:
        return not_measurable("no_place")
    if raw.place.route_of("types") is None:
        return not_measurable("categories_not_read")
    minimum, maximum = number(thresholds, "min"), number(thresholds, "max")
    secondary = [
        kind
        for kind in raw.place.types
        if kind not in GENERIC_PLACE_TYPES and kind != raw.place.primary_type
    ]
    count = len(secondary)
    if minimum <= count <= maximum:
        outcome = CheckOutcome.PASSED
    elif count > maximum:
        outcome = at_most_outcome(count, maximum)
    else:
        outcome = at_least_outcome(count, minimum)
    return outcome, {
        "secondary_categories": secondary,
        "count": count,
        "min": minimum,
        "max": maximum,
    }


def description(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f4: description of min_chars to max_chars that names a service and the city up front.

    Passed with both; a description that misses the length or the front words is partial.
    """
    if raw.place is None:
        return not_measurable("no_place")
    text = raw.place.description
    if text is None:
        return not_measurable("description_not_read")
    minimum, maximum = number(thresholds, "min_chars"), number(thresholds, "max_chars")
    front = set(tokens(text[:DESCRIPTION_FRONT_CHARS]))
    city = business_city(raw)
    city_in_front = bool(city) and set(tokens(city or "")) <= front
    services = service_terms(raw)
    service_in_front = any(set(words) <= front for words in services.values())
    length_ok = minimum <= len(text) <= maximum
    if not text:
        outcome = CheckOutcome.FAILED
    elif length_ok and city_in_front and (service_in_front or not services):
        outcome = CheckOutcome.PASSED
    else:
        outcome = CheckOutcome.PARTIAL
    return outcome, {
        "description_chars": len(text),
        "min_chars": minimum,
        "max_chars": maximum,
        "city_in_front": city_in_front,
        "service_in_front": service_in_front,
    }


def services(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f5: services are listed, at most max, and cover a tracked service word.

    The order by demand is not public; services_order_checked records that.
    """
    if raw.place is None:
        return not_measurable("no_place")
    listed_services = raw.place.services
    if listed_services is None:
        return not_measurable("services_not_read")
    maximum = number(thresholds, "max")
    listed_words = set(tokens(" ".join(listed_services)))
    terms = service_terms(raw)
    covered = sorted(phrase for phrase, words in terms.items() if set(words) <= listed_words)
    if not listed_services:
        outcome = CheckOutcome.FAILED
    elif len(listed_services) > maximum or (terms and not covered):
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.PASSED
    return outcome, {
        "services": listed(listed_services),
        "service_count": len(listed_services),
        "max": maximum,
        "phrases_covered": covered,
        "services_order_checked": False,
    }


def products(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f6: the profile shows at least one product."""
    if raw.place is None:
        return not_measurable("no_place")
    shown = raw.place.products
    if shown is None:
        return not_measurable("products_not_read")
    outcome = CheckOutcome.PASSED if shown else CheckOutcome.FAILED
    return outcome, {"products": listed(shown), "product_count": len(shown)}


def recent_post(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f9: the newest profile post is at most max_age_days old; an older one is partial."""
    if raw.place is None:
        return not_measurable("no_place")
    ages = raw.place.post_ages_days
    if ages is None:
        return not_measurable("posts_not_read")
    maximum = number(thresholds, "max_age_days")
    newest = min(ages, default=None)
    if newest is None:
        outcome = CheckOutcome.FAILED
    elif newest <= maximum:
        outcome = CheckOutcome.PASSED
    else:
        outcome = CheckOutcome.PARTIAL
    return outcome, {"post_count": len(ages), "newest_post_days": newest, "max_age_days": maximum}


def questions(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f10: the profile shows questions with answers; who answered is not checked."""
    if raw.place is None:
        return not_measurable("no_place")
    count = raw.place.question_count
    if count is None:
        return not_measurable("questions_not_shown")
    outcome = CheckOutcome.PASSED if count else CheckOutcome.FAILED
    return outcome, {"question_count": count, "owner_answers_checked": False}


def service_areas(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f11: between one and max service areas; more than max is judged like any upper limit."""
    if raw.place is None:
        return not_measurable("no_place")
    areas = raw.place.service_areas
    if areas is None:
        return not_measurable("service_areas_not_read")
    maximum = number(thresholds, "max")
    outcome = at_most_outcome(len(areas), maximum) if areas else CheckOutcome.FAILED
    return outcome, {
        "service_areas": listed(areas),
        "service_area_count": len(areas),
        "max": maximum,
    }


def attributes(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f13: the profile shows attributes such as payment options or accessibility."""
    if raw.place is None:
        return not_measurable("no_place")
    shown = raw.place.attributes
    if shown is None:
        return not_measurable("attributes_not_read")
    outcome = CheckOutcome.PASSED if shown else CheckOutcome.FAILED
    return outcome, {"attributes": listed(shown), "attribute_count": len(shown)}


def opening_hours_complete(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f7: regular opening hours are set; holiday hours are not in the public data."""
    if raw.place is None:
        return not_measurable("no_place")
    outcome = CheckOutcome.PASSED if raw.place.has_opening_hours else CheckOutcome.FAILED
    return outcome, {
        "has_opening_hours": raw.place.has_opening_hours,
        "holiday_hours_checked": False,
    }


def photos(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f8: at least min_photos photos; logo and cover image are not in the public data.

    The age of the photos is f15.
    """
    if raw.place is None:
        return not_measurable("no_place")
    minimum = number(thresholds, "min_photos")
    return at_least_outcome(raw.place.photo_count, minimum), {
        "photo_count": raw.place.photo_count,
        "min_photos": minimum,
        "logo_and_cover_checked": False,
    }


def photo_age(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f15: the newest dated photo is at most max_age_days old, counted to the collection day.

    Street View and 360° images do not count. Without the photo viewer or without any dated
    photo the check is not measurable.
    """
    if raw.place is None:
        return not_measurable("no_place")
    if raw.place.photo_dated_count is None:
        return not_measurable("photos_not_read")
    newest = raw.place.photo_newest_date
    if newest is None:
        return not_measurable("no_dated_photos", photos_with_date=raw.place.photo_dated_count)
    maximum = number(thresholds, "max_age_days")
    age = max((raw.collected_at.date() - newest).days, 0)
    outcome = CheckOutcome.PASSED if age <= maximum else CheckOutcome.FAILED
    return outcome, {
        "newest_photo_date": newest.isoformat(),
        "newest_photo_days": age,
        "photos_with_date": raw.place.photo_dated_count,
        "max_age_days": maximum,
    }


def contact_options(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f12: the profile links the customer's own website; booking and chat are not public."""
    if raw.place is None:
        return not_measurable("no_place")
    linked = host_of(raw.place.website)
    own = host_of(raw.website.start_url) if raw.website is not None else None
    if linked is None:
        outcome = CheckOutcome.FAILED
    elif own is None or linked == own:
        outcome = CheckOutcome.PASSED
    else:
        outcome = CheckOutcome.PARTIAL
    return outcome, {
        "profile_website": str(raw.place.website) if raw.place.website else None,
        "website_host": own,
        "booking_and_messages_checked": False,
    }


def policy_compliance(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """f14: the profile name carries no city or service words that the real business name lacks."""
    if raw.place is None:
        return not_measurable("no_place")
    website_name = raw.website.footer_nap.get("name") if raw.website is not None else None
    real_words = set(tokens(website_name or ""))
    city = business_city(raw)
    keywords = {word for words in service_terms(raw).values() for word in words}
    if city:
        keywords.update(tokens(city))
    profile_words = set(tokens(raw.place.name))
    stuffed = sorted((profile_words & keywords) - real_words) if website_name else []
    if not website_name and city and fold(city) in fold(raw.place.name):
        stuffed = tokens(city)
    outcome = CheckOutcome.FAILED if stuffed else CheckOutcome.PASSED
    return outcome, {
        "profile_name": raw.place.name,
        "website_name": website_name,
        "keywords_in_name": stuffed,
    }


CHECKS: dict[str, CheckFunction] = {
    "f1": profile_exists,
    "f2": primary_category,
    "f3": secondary_categories,
    "f4": description,
    "f5": services,
    "f6": products,
    "f7": opening_hours_complete,
    "f8": photos,
    "f9": recent_post,
    "f10": questions,
    "f11": service_areas,
    "f12": contact_options,
    "f13": attributes,
    "f14": policy_compliance,
    "f15": photo_age,
}


PROFILE_FIELDS = {
    "f1": "business_status",
    "f2": "primary_type",
    "f3": "types",
    "f4": "description",
    "f5": "services",
    "f6": "products",
    "f7": "has_opening_hours",
    "f8": "photo_count",
    "f9": "post_ages_days",
    "f10": "question_count",
    "f11": "service_areas",
    "f12": "website",
    "f13": "attributes",
    "f14": "name",
    "f15": "photo_newest_date",
}
COMPARED_CHECKS = frozenset({"f2"})


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id and add the route of the profile data it judged."""
    outcome, measured = dispatch(CHECKS, check_id, raw, thresholds)
    field = PROFILE_FIELDS.get(check_id)
    if raw.place is None or field is None or outcome is CheckOutcome.NOT_MEASURABLE:
        return outcome, measured
    compared = raw.competitor_places[:STRONGEST_COMPETITORS] if check_id in COMPARED_CHECKS else []
    return outcome, {**measured, **place_routes([raw.place, *compared], field)}
