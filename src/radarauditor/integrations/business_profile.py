"""Change a customer's Google Business Profile with the operator's authorisation.

Context: Only used by the apply step, and only when the customer added the operator's Google
account to its profile (customer_access.google_business_profile_granted, google_location_name)
and the operator's grant is stored (collectors/google_auth.py, scope business.manage).
Input: Access token, location name, field and value or review id and reply.
Output: Nothing on success; CollectionError when Google rejects the change.
"""

import logging
from typing import Any

import httpx

from radarauditor.collectors.access import account_of
from radarauditor.collectors.transport import send
from radarauditor.optimization.targets import ProfileField

logger = logging.getLogger(__name__)

LOCATION_URL = "https://mybusinessbusinessinformation.googleapis.com/v1/{location}"
REPLY_URL = "https://mybusiness.googleapis.com/v4/{account}/{location}/reviews/{review}/reply"
REQUEST_TIMEOUT_SECONDS = 30
SOURCE = "Business Profile"


def profile_patch(field: ProfileField, value: str) -> tuple[str, dict[str, Any]]:
    """Update mask and body of a location patch for one field.

    Args:
        field: Profile field; review replies are not a location field.
        value: New value.

    Returns:
        The updateMask and the partial location body.

    Raises:
        ValueError: For ProfileField.REVIEW_REPLY.
    """
    if field is ProfileField.NAME:
        return "title", {"title": value}
    if field is ProfileField.DESCRIPTION:
        return "profile.description", {"profile": {"description": value}}
    if field is ProfileField.PHONE:
        return "phoneNumbers.primaryPhone", {"phoneNumbers": {"primaryPhone": value}}
    if field is ProfileField.WEBSITE:
        return "websiteUri", {"websiteUri": value}
    raise ValueError(f"{field.value} is not a location field")


async def _call(method: str, url: str, token: str, **arguments: Any) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        await send(client, method, url, source=SOURCE, headers=headers, **arguments)


async def update_field(token: str, location_name: str, field: ProfileField, value: str) -> None:
    """Write one field of the location.

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: If Google rejects the change.
    """
    mask, body = profile_patch(field, value)
    url = LOCATION_URL.format(location=location_name)
    await _call("PATCH", url, token, params={"updateMask": mask}, json=body)
    logger.info("Business Profile %s updated: %s", location_name, mask)


async def reply_to_review(token: str, location_name: str, review_id: str, reply: str) -> None:
    """Post or replace the owner's reply to a review.

    Raises:
        TransientCollectionError: On rate limits or server errors after retries.
        CollectionError: If the location is not managed by the operator or Google rejects it.
    """
    account = await account_of(token, location_name)
    url = REPLY_URL.format(account=account, location=location_name, review=review_id)
    await _call("PUT", url, token, json={"comment": reply})
    logger.info("Business Profile %s: replied to review %s", location_name, review_id)
