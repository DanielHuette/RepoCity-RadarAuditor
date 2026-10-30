"""Domain age from the first capture of the Wayback Machine (Internet Archive CDX API).

Context: Free public source for check i5 next to the DataForSEO visibility history: the day the
archive first saved the domain's start page is the best public proof of how long the site exists.
Input: Domain without scheme.
Output: Date of the first capture, or None when the archive never saved the domain.
"""

import json
from datetime import date, datetime

import httpx

from radarauditor.collectors.transport import USER_AGENT, send
from radarauditor.errors import CollectionError

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
REQUEST_TIMEOUT_SECONDS = 30
TIMESTAMP_DATE_DIGITS = 8
SOURCE_NAME = "Wayback Machine"


async def first_capture(domain: str) -> date | None:
    """Return the day the Wayback Machine first archived the domain's start page.

    The CDX index lists the captures of one address oldest first, so the first row is the answer.

    Args:
        domain: Domain without scheme and path, e.g. "dachdecker-hollenbeck.de".

    Returns:
        Day of the first capture, or None when there is none.

    Raises:
        TransientCollectionError: If the archive is unreachable or overloaded after retries.
        CollectionError: On a rejected request or an answer that is not a capture list.
    """
    params = {"url": domain, "output": "json", "fl": "timestamp", "limit": "1"}
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
    ) as client:
        response = await send(client, "GET", CDX_ENDPOINT, source=SOURCE_NAME, params=params)
    return parse_first_capture(response.text)


def parse_first_capture(body: str) -> date | None:
    """Read the date of the first capture from a CDX JSON answer.

    Args:
        body: Answer text; a header row ["timestamp"] followed by capture rows, or empty.

    Returns:
        The capture day, or None when the answer lists no capture.

    Raises:
        CollectionError: If the answer is not a CDX capture list.
    """
    if not body.strip():
        return None
    try:
        rows = json.loads(body)
    except ValueError as error:
        raise CollectionError(f"{SOURCE_NAME} answer is not JSON") from error
    if not isinstance(rows, list):
        raise CollectionError(f"{SOURCE_NAME} answer is not a capture list")
    if len(rows) < 2:
        return None
    capture = rows[1]
    if not isinstance(capture, list) or not capture or not isinstance(capture[0], str):
        raise CollectionError(f"{SOURCE_NAME} capture row is malformed")
    try:
        return datetime.strptime(capture[0][:TIMESTAMP_DATE_DIGITS], "%Y%m%d").date()
    except ValueError as error:
        raise CollectionError(f"{SOURCE_NAME} timestamp {capture[0]!r} is malformed") from error
