"""HTTP plumbing shared by the collectors: user agent, status mapping and retries.

Context: Every collector that calls a JSON API sends its requests through `send` so rate limits,
server errors and network failures are handled the same way.
Input: An httpx client and request arguments.
Output: Successful httpx responses or CollectionError / TransientCollectionError.
"""

import logging
from collections.abc import Callable
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from radarauditor.errors import CollectionError, TransientCollectionError

logger = logging.getLogger(__name__)

USER_AGENT = "RepoCity-RadarAuditor/0.1 (+https://speedofthespirit.dev)"
RETRY_ATTEMPTS = 3
RETRY_WAIT: wait_base = wait_exponential(multiplier=1, min=1, max=10)
ERROR_DETAIL_CHARS = 200

ResponseCheck = Callable[[httpx.Response], None]


def raise_for_status(response: httpx.Response, source: str) -> None:
    """Map an HTTP error status to the collector exception hierarchy.

    Args:
        response: Response to inspect.
        source: Human-readable name of the data source for the message.

    Raises:
        TransientCollectionError: On 429 or any 5xx status.
        CollectionError: On any other 4xx status.
    """
    status = response.status_code
    if status == httpx.codes.TOO_MANY_REQUESTS or status >= httpx.codes.INTERNAL_SERVER_ERROR:
        raise TransientCollectionError(f"{source} answered HTTP {status}")
    if status >= httpx.codes.BAD_REQUEST:
        detail = _detail(response)
        raise CollectionError(f"{source} rejected the request with HTTP {status}{detail}")


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return f": {error['message'][:ERROR_DETAIL_CHARS]}"
        if isinstance(error, str):
            return f": {error[:ERROR_DETAIL_CHARS]}"
    return ""


async def send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    source: str,
    check: ResponseCheck | None = None,
    **request_arguments: Any,
) -> httpx.Response:
    """Send one request, retrying transient failures with exponential backoff.

    Args:
        client: Open httpx client.
        method: HTTP method.
        url: Absolute URL.
        source: Name of the data source used in error messages.
        check: Optional extra validation of a successful response, run inside the retry loop so
            it may raise TransientCollectionError to trigger another attempt.
        **request_arguments: Passed to `httpx.AsyncClient.request`.

    Returns:
        The successful response.

    Raises:
        TransientCollectionError: If every attempt failed temporarily.
        CollectionError: On a permanent failure.
    """
    retrying = AsyncRetrying(
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=RETRY_WAIT,
        retry=retry_if_exception_type(TransientCollectionError),
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            response = await _request_once(client, method, url, source, request_arguments)
            if check is not None:
                check(response)
            return response
    raise CollectionError(f"{source}: no request attempt was made")


async def _request_once(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    source: str,
    request_arguments: dict[str, Any],
) -> httpx.Response:
    try:
        response = await client.request(method, url, **request_arguments)
    except httpx.TransportError as error:
        logger.info("%s unreachable: %s", source, type(error).__name__)
        raise TransientCollectionError(f"{source} unreachable: {type(error).__name__}") from error
    raise_for_status(response, source)
    return response
