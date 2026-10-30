"""Tests for the repository readers added for reports, documents and exports.

Context: Supabase client replaced by a recording fake. | Input: Fixtures. | Output: Assertions.
"""

from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from postgrest import APIError, APIResponse

from radarauditor.config import Settings
from radarauditor.db import repositories as repositories_module
from radarauditor.db.repositories import Repositories
from radarauditor.errors import StorageError

CUSTOMER_ID = UUID("00000000-0000-4000-8000-000000000001")
AUDIT_ID = UUID("00000000-0000-4000-8000-000000000002")


class RecordingQuery:
    """Records builder calls and returns a fixed response."""

    def __init__(self, outcome: Any) -> None:
        """Remember the rows or the error to return."""
        self.outcome = outcome
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Any:
        """Record any builder method and keep chaining."""

        def record(*args: Any, **kwargs: Any) -> "RecordingQuery":
            self.calls.append((name, args))
            return self

        return record

    def execute(self) -> APIResponse:
        """Return the rows or raise the error."""
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return APIResponse.model_construct(data=self.outcome)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """A client whose table() returns queued RecordingQuery objects."""
    client = MagicMock()
    monkeypatch.setattr(repositories_module, "create_client", MagicMock(return_value=client))
    return client


def respond(client: MagicMock, outcome: Any) -> RecordingQuery:
    """Make the next table() call answer with outcome."""
    query = RecordingQuery(outcome)
    client.table.return_value = query
    return query


def test_settings_value_returns_json(fake_client: MagicMock, settings: Settings) -> None:
    """The value column of the matching key is returned."""
    query = respond(fake_client, [{"value": {"name": "RepoCity"}}])

    assert Repositories(settings).settings_value("supplier") == {"name": "RepoCity"}
    fake_client.table.assert_called_with("settings")
    assert ("eq", ("key", "supplier")) in query.calls


def test_settings_value_missing_key(fake_client: MagicMock, settings: Settings) -> None:
    """An unknown key yields None."""
    respond(fake_client, [])

    assert Repositories(settings).settings_value("datev") is None


def test_audits_of_customer_oldest_first(fake_client: MagicMock, settings: Settings) -> None:
    """Audits are filtered by customer and ordered by request time."""
    row = {
        "id": str(AUDIT_ID),
        "customer_id": str(CUSTOMER_ID),
        "kind": "remote",
        "status": "reported",
        "requested_at": "2026-10-01T09:00:00+00:00",
        "finished_at": None,
        "total_score": "61.5",
        "raw_data_path": None,
        "report_path": None,
    }
    query = respond(fake_client, [row])

    audits = Repositories(settings).audits_of_customer(CUSTOMER_ID)

    assert [audit.id for audit in audits] == [AUDIT_ID]
    assert ("order", ("requested_at",)) in query.calls


def test_findings_of_audit(fake_client: MagicMock, settings: Settings) -> None:
    """Findings are filtered by audit."""
    row = {
        "id": "00000000-0000-4000-8000-0000000000f1",
        "audit_id": str(AUDIT_ID),
        "check_id": "a1",
        "severity": 3,
        "title": "t",
        "explanation": "e",
        "customer_impact": "c",
        "fix": "f",
        "fix_payload": None,
    }
    query = respond(fake_client, [row])

    findings = Repositories(settings).findings_of_audit(AUDIT_ID)

    assert [finding.check_id for finding in findings] == ["a1"]
    assert ("eq", ("audit_id", str(AUDIT_ID))) in query.calls


def test_reader_errors_become_storage_errors(fake_client: MagicMock, settings: Settings) -> None:
    """A rejected query raises StorageError naming the action."""
    respond(fake_client, APIError({"message": "denied"}))

    with pytest.raises(StorageError, match="load setting supplier"):
        Repositories(settings).settings_value("supplier")
