"""Tests for outbox event delivery.

Context: Unit tests with respx-mocked HTTP and fake repositories. | Input: Fixtures.
| Output: Assertions.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
import respx
from tenacity import wait_none

from radarauditor.db.models import Event
from radarauditor.integrations import events
from radarauditor.integrations.events import deliver_pending, is_transient

ENDPOINT = "https://crm.example/hooks"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry without waiting."""
    monkeypatch.setattr(events.post_event.retry, "wait", wait_none())


def make_event(kind: str = "audit.reported") -> Event:
    """An undelivered outbox event."""
    return Event(
        id=uuid4(),
        kind=kind,
        payload={"customer_id": "c1", "month": "2026-10"},
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        delivered_at=None,
    )


@respx.mock
async def test_delivers_and_marks_events(fake_repositories: MagicMock) -> None:
    """Each event is posted as JSON and marked delivered on 2xx."""
    first, second = make_event(), make_event("audit.reported")
    fake_repositories.pending_events.return_value = [first, second]
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(204))

    delivered = await deliver_pending(fake_repositories, ENDPOINT)

    assert delivered == 2
    assert route.call_count == 2
    body = route.calls[0].request.content
    assert Event.model_validate_json(body) == first
    assert [c.args[0] for c in fake_repositories.mark_event_delivered.call_args_list] == [
        first.id,
        second.id,
    ]


@respx.mock
async def test_retries_server_errors(fake_repositories: MagicMock) -> None:
    """A 5xx answer is retried and succeeds on a later attempt."""
    fake_repositories.pending_events.return_value = [make_event()]
    route = respx.post(ENDPOINT).mock(
        side_effect=[httpx.Response(503), httpx.ConnectError("refused"), httpx.Response(200)]
    )

    assert await deliver_pending(fake_repositories, ENDPOINT) == 1
    assert route.call_count == 3


@respx.mock
async def test_gives_up_after_three_attempts(fake_repositories: MagicMock) -> None:
    """Persistent server errors leave the event undelivered."""
    fake_repositories.pending_events.return_value = [make_event()]
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(500))

    assert await deliver_pending(fake_repositories, ENDPOINT) == 0
    assert route.call_count == events.MAX_ATTEMPTS
    fake_repositories.mark_event_delivered.assert_not_called()


@respx.mock
async def test_client_errors_are_not_retried(fake_repositories: MagicMock) -> None:
    """A 4xx answer fails at once; the next event is still sent."""
    rejected, accepted = make_event(), make_event()
    fake_repositories.pending_events.return_value = [rejected, accepted]
    route = respx.post(ENDPOINT).mock(side_effect=[httpx.Response(422), httpx.Response(200)])

    assert await deliver_pending(fake_repositories, ENDPOINT) == 1
    assert route.call_count == 2
    fake_repositories.mark_event_delivered.assert_called_once_with(accepted.id)


async def test_nothing_pending(fake_repositories: MagicMock) -> None:
    """No events means no requests."""
    assert await deliver_pending(fake_repositories, ENDPOINT) == 0


def test_is_transient() -> None:
    """Connection errors and 5xx are transient; 4xx and others are not."""
    request = httpx.Request("POST", ENDPOINT)

    def status_error(code: int) -> httpx.HTTPStatusError:
        return httpx.HTTPStatusError(
            "status", request=request, response=httpx.Response(code, request=request)
        )

    assert is_transient(httpx.ConnectError("refused"))
    assert is_transient(httpx.ReadTimeout("slow"))
    assert is_transient(status_error(502))
    assert not is_transient(status_error(404))
    assert not is_transient(ValueError("other"))


def test_event_kinds_are_unique() -> None:
    """The documented kinds include the ones the worker and the database write."""
    assert len(set(events.EVENT_KINDS)) == len(events.EVENT_KINDS)
    assert {"audit.reported", "optimization.finished"} <= set(events.EVENT_KINDS)


def test_billing_is_no_event_of_the_radarauditor() -> None:
    """Invoices belong to the CRM, so the RadarAuditor announces none."""
    assert "invoice.due" not in events.EVENT_KINDS
