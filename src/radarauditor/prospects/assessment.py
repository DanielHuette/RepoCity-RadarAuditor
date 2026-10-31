"""Rank the businesses of a customer search by signs of a neglected Google profile.

Context: Pure step between the Places API page reader and the stored results. Every signal is a
field the Text Search returns: no website, no opening hours, no phone, no or few photos (Google
returns at most ten), no reviews, or fewer reviews than a share of the median of the same list.
Closed businesses are left out. Equal scores put the business with fewer reviews first.
Input: Listed places and the criteria of config/kundensuche.yaml.
Output: ProspectResult per open business, ranked, with one German reason sentence and the contact
ways its data allows.
"""

import statistics
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from radarauditor.collectors.patterns import find_address
from radarauditor.collectors.places import ProspectPlace
from radarauditor.prospects.criteria import ContactStatus, ContactWay, ProspectCriteria, SignalId

CLOSED_STATUSES = frozenset({"CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"})


class ResultContactWay(BaseModel):
    """A contact way as stored with a result and shown in the app."""

    model_config = ConfigDict(frozen=True)

    channel: str
    label: str
    status: ContactStatus
    note: str


class ProspectResult(BaseModel):
    """One ranked business of a search, in the shape public.finish_prospect_search reads."""

    model_config = ConfigDict(frozen=True)

    rank: int
    place_id: str
    name: str
    address: str
    street: str
    postal_code: str
    city: str
    phone: str | None
    website: str | None
    maps_url: str | None
    rating: float | None
    review_count: int
    photo_count: int
    has_opening_hours: bool
    score: int
    signals: list[SignalId]
    reasons: str
    contact_ways: list[ResultContactWay]


def reason_sentence(parts: Sequence[str], empty_text: str) -> str:
    """Join reason parts into one sentence: "A, b und c."; empty_text when there is none.

    Args:
        parts: Parts in the order they are named, each starting in lower case.
        empty_text: Sentence for a business without any signal.

    Returns:
        One German sentence with a capital first letter and a full stop.
    """
    if not parts:
        return empty_text
    joined = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} und {parts[-1]}"
    return f"{joined[:1].upper()}{joined[1:]}."


def assess(places: Sequence[ProspectPlace], criteria: ProspectCriteria) -> list[ProspectResult]:
    """Rank the open businesses of one search, most signs of a neglected profile first.

    Args:
        places: Businesses in Google's order, possibly from several pages.
        criteria: Signals, thresholds and contact ways.

    Returns:
        One result per open business, rank 1 first.
    """
    open_places = [place for place in places if place.business_status not in CLOSED_STATUSES]
    median = statistics.median_low([place.review_count for place in open_places] or [0])
    scored = [_result(place, median, criteria) for place in open_places]
    scored.sort(key=lambda result: (-result.score, result.review_count, result.name.casefold()))
    return [result.model_copy(update={"rank": rank}) for rank, result in enumerate(scored, 1)]


def _signals(place: ProspectPlace, median: int, criteria: ProspectCriteria) -> list[SignalId]:
    limits = criteria.thresholds
    found: list[SignalId] = []
    if not place.website:
        found.append(SignalId.NO_WEBSITE)
    if not place.has_opening_hours:
        found.append(SignalId.NO_OPENING_HOURS)
    if not place.phone:
        found.append(SignalId.NO_PHONE)
    if place.photo_count == 0:
        found.append(SignalId.NO_PHOTOS)
    elif place.photo_count < limits.few_photos_below:
        found.append(SignalId.FEW_PHOTOS)
    if place.review_count == 0:
        found.append(SignalId.NO_REVIEWS)
    elif (
        median >= limits.few_reviews_min_median
        and place.review_count < limits.few_reviews_share * median
    ):
        found.append(SignalId.FEW_REVIEWS)
    return found


def _result(place: ProspectPlace, median: int, criteria: ProspectCriteria) -> ProspectResult:
    signals = _signals(place, median, criteria)
    values = {"photos": place.photo_count, "reviews": place.review_count, "median": median}
    parts = [criteria.signals[signal].text.format(**values) for signal in signals]
    address = find_address([part.strip() for part in place.formatted_address.split(",")])
    return ProspectResult(
        rank=0,
        place_id=place.place_id,
        name=place.name,
        address=place.formatted_address,
        street=address.get("street", ""),
        postal_code=address.get("postal_code", ""),
        city=address.get("city", ""),
        phone=place.phone,
        website=place.website,
        maps_url=place.maps_url,
        rating=place.rating,
        review_count=place.review_count,
        photo_count=place.photo_count,
        has_opening_hours=place.has_opening_hours,
        score=sum(criteria.signals[signal].points for signal in signals),
        signals=signals,
        reasons=reason_sentence(parts, criteria.no_signal_text),
        contact_ways=_contact_ways(
            criteria.contact_ways, bool(address.get("street")), place.phone is not None
        ),
    )


def _contact_ways(
    ways: Sequence[ContactWay], has_address: bool, has_phone: bool
) -> list[ResultContactWay]:
    available = {"address": has_address, "phone": has_phone}
    return [
        ResultContactWay(channel=way.channel, label=way.label, status=way.status, note=way.note)
        for way in ways
        if way.needs is None or available[way.needs]
    ]
