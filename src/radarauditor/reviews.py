"""Pick the unanswered reviews an audit read, for reply drafts.

Context: The review reply PDF (documents/review_replies.py) and the monthly service
(optimization/prepare.py) draft replies only to real reviews whose owner reply is known to be
missing. Reviews read in the browser on Google Maps or with access to the profile carry the reply
field; the Places API never does, so its sample cannot show which reviews are unanswered and yields
none. Reviews with text come first, newest first, then reviews with stars only.
Input: RawAudit, an optional limit and an optional point in time.
Output: UnansweredReviews with the source of the sample.
"""

from dataclasses import dataclass
from datetime import datetime

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.scoring.checks.g_bewertungen import (
    judged_reviews,
    owner_reply,
    parse_time,
    reply_data_available,
    review_rating,
    review_text,
)


@dataclass(frozen=True)
class OpenReview:
    """One review without an owner reply.

    review_id is Google's id, known only for reviews read with access to the profile; date_text is
    the German date or Google's relative date ("vor 2 Wochen") when no date is known.
    """

    review_id: str | None
    published: datetime | None
    date_text: str
    stars: float | None
    text: str


@dataclass(frozen=True)
class UnansweredReviews:
    """The unanswered reviews of one audit and where the sample came from.

    source is "profile", "browser" or "places_api", None without any review sample;
    reply_data_available is False when the sample cannot tell answered from unanswered.
    """

    source: str | None
    reply_data_available: bool
    reviews: list[OpenReview]


def unanswered_reviews(
    raw: RawAudit, limit: int | None = None, since: datetime | None = None
) -> UnansweredReviews:
    """Reviews of the audit without an owner reply, best candidates for a reply first.

    Args:
        raw: Raw data of the audit.
        limit: Most reviews to return; None returns all.
        since: Only reviews published after this time; reviews without a date are left out then.

    Returns:
        The reviews and the source of the sample.
    """
    judged = judged_reviews(raw)
    if judged is None:
        return UnansweredReviews(source=None, reply_data_available=False, reviews=[])
    sample, source = judged
    if not reply_data_available(sample):
        return UnansweredReviews(source=source, reply_data_available=False, reviews=[])
    open_reviews = [_open(review) for review in sample if owner_reply(review) is None]
    if since is not None:
        open_reviews = [
            review
            for review in open_reviews
            if review.published is not None and review.published > since
        ]
    open_reviews.sort(key=_order)
    return UnansweredReviews(source=source, reply_data_available=True, reviews=open_reviews[:limit])


def _order(review: OpenReview) -> tuple[bool, float]:
    newest_first = -review.published.timestamp() if review.published is not None else 0.0
    return (not review.text, newest_first)


def _open(review: dict[str, object]) -> OpenReview:
    published = parse_time(review.get("publishTime"))
    relative = review.get("relativePublishTimeDescription")
    if published is not None:
        date_text = published.strftime("%d.%m.%Y")
    else:
        date_text = relative if isinstance(relative, str) and relative else "ohne Datum"
    review_id = review.get("reviewId")
    return OpenReview(
        review_id=str(review_id) if review_id else None,
        published=published,
        date_text=date_text,
        stars=review_rating(review),
        text=review_text(review).strip(),
    )
