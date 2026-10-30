"""Deliver outbox events (audit reported, document issued, optimisation finished, post ready).

Context: Delivery is at-least-once with retry; the endpoint comes from the settings. The worker
writes most events; optimization.finished is written by a database trigger (migration 013) when the
last change of an order is done.
Input: Event rows.
Output: Delivered flags.
"""

import logging

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from radarauditor.db.models import Event
from radarauditor.db.repositories import Repositories

logger = logging.getLogger(__name__)

EVENT_KINDS = (
    "customer.created",
    "audit.reported",
    "document.issued",
    "optimization.finished",
    "post.ready",
    "review.reply_ready",
)
TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 3


def is_transient(exc: BaseException) -> bool:
    """Return whether a delivery error is worth retrying: connection problems and 5xx answers.

    Args:
        exc: Exception raised while posting an event.

    Returns:
        True for transport errors and server errors, False otherwise.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


@retry(
    retry=retry_if_exception(is_transient),
    stop=stop_after_attempt(MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=1, max=10),
    reraise=True,
)
async def post_event(client: httpx.AsyncClient, endpoint: str, event: Event) -> None:
    """POST one event as JSON and raise on any non-2xx answer.

    Args:
        client: Shared HTTP client with timeout.
        endpoint: Receiver URL.
        event: Outbox row to send.

    Raises:
        httpx.HTTPError: If the endpoint is unreachable or answers with an error status.
    """
    response = await client.post(endpoint, json=event.model_dump(mode="json"))
    response.raise_for_status()


async def deliver_pending(repositories: Repositories, endpoint: str) -> int:
    """Send undelivered events to the endpoint; return how many were delivered."""
    events = repositories.pending_events()
    delivered = 0
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        for event in events:
            try:
                await post_event(client, endpoint, event)
            except httpx.HTTPError as exc:
                logger.warning("Event %s (%s) not delivered: %s", event.id, event.kind, exc)
                continue
            repositories.mark_event_delivered(event.id)
            delivered += 1
    return delivered
