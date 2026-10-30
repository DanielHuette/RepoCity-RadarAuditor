"""Derive ranking rows for the customer and its competitors from the collected searches.

Context: Runs after COLLECT so positions are stored per keyword and audit for the history (j9) and
the monthly before-and-after report.
Input: RawAudit, keywords, competitors, the customer's hostname and Google place name.
Output: Ranking rows with ids derived from audit, keyword and subject.
"""

from uuid import UUID, uuid5

from radarauditor.collectors.patterns import collapse_whitespace, host_of
from radarauditor.collectors.snapshot import RawAudit, SerpResult
from radarauditor.db.models import Competitor, Keyword, Ranking
from radarauditor.scoring.checks.common import google_web_serps, names_match, same_site

CUSTOMER_SUBJECT = "customer"


def rankings_from_raw(
    audit_id: UUID,
    raw: RawAudit,
    keywords: list[Keyword],
    competitors: list[Competitor],
    customer_hostname: str | None,
    customer_place_name: str | None,
) -> list[Ranking]:
    """Build one ranking row per keyword for the customer and for every configured competitor.

    A keyword is matched to the Google web search whose phrase is "{phrase} {city}" or the bare
    phrase, ignoring case and spacing; keywords without such a search get no rows. Organic
    positions come from the subject's hostname (subdomains count), local pack positions from its
    name. A competitor without a website has no organic position. Grid points of the map grid
    follow as customer rows of the primary keyword (see grid_rankings).

    Args:
        audit_id: Audit the rows belong to; row ids derive from it, so reruns are idempotent.
        raw: Collected audit data.
        keywords: Tracked keywords of the customer.
        competitors: Configured competitors of the customer.
        customer_hostname: Host of the customer's website, with or without "www.".
        customer_place_name: Name of the customer's Google profile as shown in the map pack.

    Returns:
        Rows in keyword order, the customer first, then the competitors in configured order,
        then the grid rows.
    """
    searches = {_phrase_key(serp.phrase): serp for serp in google_web_serps(raw)}
    customer_host = host_of(customer_hostname)
    rows: list[Ranking] = []
    for keyword in keywords:
        serp = searches.get(_phrase_key(f"{keyword.phrase} {keyword.city}")) or searches.get(
            _phrase_key(keyword.phrase)
        )
        if serp is None:
            continue
        rows.append(
            _row(audit_id, keyword, serp, CUSTOMER_SUBJECT, customer_host, customer_place_name)
        )
        rows.extend(
            _row(audit_id, keyword, serp, rival.name, host_of(rival.website), rival.name)
            for rival in competitors
        )
    return rows + grid_rankings(audit_id, raw, keywords)


def grid_rankings(audit_id: UUID, raw: RawAudit, keywords: list[Keyword]) -> list[Ranking]:
    """One customer row per grid point of the map grid, for the primary keyword.

    Args:
        audit_id: Audit the rows belong to; row ids derive from it and the grid key.
        raw: Collected audit data; raw.grid is empty when the grid could not be measured.
        keywords: Tracked keywords; the grid belongs to the primary one, else the first.

    Returns:
        Rows with grid_point set to the point key and the local pack position, in grid order;
        empty without grid points or keywords.
    """
    keyword = next((item for item in keywords if item.is_primary), None) or (
        keywords[0] if keywords else None
    )
    if keyword is None:
        return []
    return [
        Ranking(
            id=uuid5(audit_id, f"{keyword.id}/{CUSTOMER_SUBJECT}/{point.key}"),
            audit_id=audit_id,
            keyword_id=keyword.id,
            subject=CUSTOMER_SUBJECT,
            organic_position=None,
            local_pack_position=point.position,
            grid_point=point.key,
            measured_at=raw.collected_at,
        )
        for point in raw.grid
    ]


def _row(
    audit_id: UUID,
    keyword: Keyword,
    serp: SerpResult,
    subject: str,
    host: str | None,
    place_name: str | None,
) -> Ranking:
    return Ranking(
        id=uuid5(audit_id, f"{keyword.id}/{subject}"),
        audit_id=audit_id,
        keyword_id=keyword.id,
        subject=subject,
        organic_position=_organic_position(serp, host),
        local_pack_position=_local_pack_position(serp, place_name),
        grid_point=None,
        measured_at=serp.measured_at,
    )


def _organic_position(serp: SerpResult, host: str | None) -> int | None:
    if host is None:
        return None
    return min((position for position, url in serp.organic if same_site(url, host)), default=None)


def _local_pack_position(serp: SerpResult, name: str | None) -> int | None:
    if not name:
        return None
    return min(
        (position for position, entry in serp.local_pack if names_match(entry, name)),
        default=None,
    )


def _phrase_key(phrase: str) -> str:
    return collapse_whitespace(phrase).casefold()
