"""Tests for repositories.

Context: Unit tests with a fake Supabase client. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from postgrest import APIError, APIResponse
from storage3.utils import StorageException
from tenacity import wait_none

from radarauditor.config import Settings
from radarauditor.db import repositories as repositories_module
from radarauditor.db.models import (
    Audit,
    AuditKind,
    AuditStatus,
    CheckResult,
    Document,
    DocumentKind,
    Finding,
    JobKind,
    JobStatus,
    Ranking,
)
from radarauditor.db.repositories import Repositories
from radarauditor.errors import StorageError

AUDIT_ID = UUID("00000000-0000-4000-8000-000000000002")
CUSTOMER_ID = UUID("00000000-0000-4000-8000-000000000001")

Call = tuple[str, tuple[Any, ...], dict[str, Any]]


class FakeQuery:
    """Records every builder call and returns the next queued response on execute."""

    def __init__(self, client: "FakeClient", target: str) -> None:
        """Start an empty call log for one table or function."""
        self.client = client
        self.target = target
        self.calls: list[Call] = []

    def __getattr__(self, name: str) -> Any:
        """Return a recorder for any builder method."""

        def record(*args: Any, **kwargs: Any) -> "FakeQuery":
            self.calls.append((name, args, kwargs))
            return self

        return record

    def called(self, name: str) -> list[Call]:
        """Return the recorded calls of one builder method."""
        return [call for call in self.calls if call[0] == name]

    def execute(self) -> APIResponse:
        """Return the next queued response or raise the queued error."""
        self.client.executions += 1
        outcome = self.client.responses.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return APIResponse.model_construct(data=outcome)


class FakeClient:
    """Stands in for supabase.Client: table, rpc and storage."""

    def __init__(self) -> None:
        """Start without queries or responses."""
        self.queries: list[FakeQuery] = []
        self.responses: list[Any] = []
        self.executions = 0
        self.storage = MagicMock()

    def table(self, name: str) -> FakeQuery:
        """Begin a table query."""
        query = FakeQuery(self, name)
        self.queries.append(query)
        return query

    def rpc(self, function: str, params: dict[str, Any]) -> FakeQuery:
        """Begin a database function call."""
        query = FakeQuery(self, f"rpc:{function}")
        query.calls.append(("rpc", (function, params), {}))
        self.queries.append(query)
        return query


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeClient]:
    """Replace create_client with the fake and disable retry waits."""
    fake = FakeClient()
    monkeypatch.setattr(repositories_module, "create_client", MagicMock(return_value=fake))
    monkeypatch.setattr(repositories_module._execute_with_retry.retry, "wait", wait_none())
    monkeypatch.setattr(Repositories._upload_with_retry.retry, "wait", wait_none())
    monkeypatch.setattr(Repositories._download_with_retry.retry, "wait", wait_none())
    monkeypatch.setattr(Repositories._remove_with_retry.retry, "wait", wait_none())
    yield fake


@pytest.fixture
def repositories(client: FakeClient, settings: Settings) -> Repositories:
    """Repositories wired to the fake client."""
    return Repositories(settings)


def audit_row(**overrides: Any) -> dict[str, Any]:
    """Database row of the sample audit."""
    row: dict[str, Any] = {
        "id": str(AUDIT_ID),
        "customer_id": str(CUSTOMER_ID),
        "kind": "remote",
        "status": "requested",
        "requested_at": "2026-10-01T09:00:00+00:00",
        "finished_at": None,
        "total_score": None,
        "raw_data_path": None,
        "report_path": None,
        "error": None,
    }
    row.update(overrides)
    return row


def job_row(**overrides: Any) -> dict[str, Any]:
    """Database row of a queued job."""
    row: dict[str, Any] = {
        "id": str(uuid4()),
        "kind": "score",
        "status": "queued",
        "audit_id": str(AUDIT_ID),
        "customer_id": str(CUSTOMER_ID),
        "attempts": 0,
        "error": None,
        "created_at": "2026-10-01T09:00:00+00:00",
        "updated_at": "2026-10-01T09:00:00+00:00",
    }
    row.update(overrides)
    return row


def document_row(number: str) -> dict[str, Any]:
    """Database row of an issued quote."""
    return {
        "id": str(uuid4()),
        "customer_id": str(CUSTOMER_ID),
        "kind": "quote",
        "number": number,
        "issued_on": "2026-03-01",
        "net_amount": 100,
        "vat_rate": 19,
        "gross_amount": 119,
        "data": {},
        "file_path": None,
    }


def test_client_is_created_from_settings(
    client: FakeClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """URL, key and timeouts come from settings."""
    create_client = MagicMock(return_value=client)
    monkeypatch.setattr(repositories_module, "create_client", create_client)

    Repositories(settings)

    url, key = create_client.call_args.args
    assert url == "https://example.supabase.co/"
    assert key == "service-key-for-tests"
    options = create_client.call_args.kwargs["options"]
    assert options.postgrest_client_timeout == repositories_module.DATABASE_TIMEOUT_SECONDS


def test_ping_reads_one_settings_row(repositories: Repositories, client: FakeClient) -> None:
    """The health read touches one settings row."""
    client.responses = [[]]

    repositories.ping()

    assert client.queries[0].target == "settings"
    assert client.queries[0].called("limit") == [("limit", (1,), {})]


def test_customer_is_loaded(repositories: Repositories, client: FakeClient) -> None:
    """A customer row becomes the model; extra columns are ignored."""
    client.responses = [
        [
            {
                "id": str(CUSTOMER_ID),
                "name": "Muster",
                "website": None,
                "industry": "Dachdecker",
                "city": "Münster",
                "order_value_eur": 99,
                "created_at": "2026-10-01T09:00:00+00:00",
            }
        ]
    ]

    customer = repositories.customer(CUSTOMER_ID)

    assert customer.name == "Muster"
    assert client.queries[0].called("eq") == [("eq", ("id", str(CUSTOMER_ID)), {})]


def test_missing_customer_raises(repositories: Repositories, client: FakeClient) -> None:
    """An empty answer raises StorageError naming the table."""
    client.responses = [[]]

    with pytest.raises(StorageError, match="no row in customers"):
        repositories.customer(CUSTOMER_ID)


def test_api_error_becomes_storage_error(repositories: Repositories, client: FakeClient) -> None:
    """PostgREST errors are wrapped."""
    client.responses = [APIError({"message": "permission denied", "code": "42501"})]

    with pytest.raises(StorageError, match="load audit"):
        repositories.audit(AUDIT_ID)


def test_reads_are_retried_on_transport_errors(
    repositories: Repositories, client: FakeClient
) -> None:
    """A dropped connection is retried for reads."""
    client.responses = [httpx.ConnectError("down"), [audit_row()]]

    audit = repositories.audit(AUDIT_ID)

    assert audit.id == AUDIT_ID
    assert client.executions == 2


def test_reads_give_up_after_three_attempts(repositories: Repositories, client: FakeClient) -> None:
    """Retries stop after three attempts."""
    client.responses = [httpx.ConnectError("down")] * 3

    with pytest.raises(StorageError):
        repositories.audit(AUDIT_ID)
    assert client.executions == 3


def test_claim_jobs_is_never_retried(repositories: Repositories, client: FakeClient) -> None:
    """Claiming is not idempotent and must not be repeated."""
    client.responses = [httpx.ConnectError("down"), []]

    with pytest.raises(StorageError, match="claim score jobs"):
        repositories.claim_jobs(JobKind.SCORE, 1)
    assert client.executions == 1


def test_claim_jobs_calls_database_function(repositories: Repositories, client: FakeClient) -> None:
    """claim_jobs goes through the database function."""
    client.responses = [[job_row(status="running", attempts=1)]]

    jobs = repositories.claim_jobs(JobKind.SCORE, 2)

    assert [job.status for job in jobs] == [JobStatus.RUNNING]
    assert client.queries[0].calls[0] == (
        "rpc",
        ("claim_jobs", {"p_kind": "score", "p_limit": 2}),
        {},
    )


def test_locations_competitors_keywords_filter_by_customer(
    repositories: Repositories, client: FakeClient
) -> None:
    """Lists filter by customer id."""
    client.responses = [[], [], []]

    assert repositories.locations(CUSTOMER_ID) == []
    assert repositories.competitors(CUSTOMER_ID) == []
    assert repositories.keywords(CUSTOMER_ID) == []
    assert [query.target for query in client.queries] == ["locations", "competitors", "keywords"]
    for query in client.queries:
        assert query.called("eq") == [("eq", ("customer_id", str(CUSTOMER_ID)), {})]


def test_customer_access_reads_identifiers_without_password(
    repositories: Repositories, client: FakeClient
) -> None:
    """The access record carries the Google identifiers and never the WordPress password."""
    client.responses = [
        [
            {
                "customer_id": str(CUSTOMER_ID),
                "google_business_profile_granted": True,
                "search_console_property": "sc-domain:muster.de",
                "wordpress_url": None,
                "wordpress_user": None,
                "granted_at": "2026-10-01T09:00:00+00:00",
                "google_location_name": "locations/4711",
                "analytics_property_id": "987654",
            }
        ],
        [],
    ]

    access = repositories.customer_access(CUSTOMER_ID)

    assert access is not None
    assert access.google_location_name == "locations/4711"
    assert access.analytics_property_id == "987654"
    columns = client.queries[0].called("select")[0][1][0]
    assert "wordpress_app_password_encrypted" not in columns
    assert "google_location_name" in columns
    assert client.queries[0].called("eq") == [("eq", ("customer_id", str(CUSTOMER_ID)), {})]
    assert repositories.customer_access(CUSTOMER_ID) is None


def test_update_keyword_volume(repositories: Repositories, client: FakeClient) -> None:
    """The volume is written to the keyword row."""
    keyword_id = UUID("00000000-0000-4000-8000-000000000101")
    client.responses = [[]]

    repositories.update_keyword_volume(keyword_id, 880)

    query = client.queries[0]
    assert query.target == "keywords"
    assert query.called("update") == [("update", ({"monthly_volume": 880},), {})]
    assert query.called("eq") == [("eq", ("id", str(keyword_id)), {})]


def test_create_audit_inserts_row(repositories: Repositories, client: FakeClient) -> None:
    """A new audit is inserted with kind and customer."""
    client.responses = [[audit_row(kind="with_access")]]

    audit = repositories.create_audit(CUSTOMER_ID, AuditKind.WITH_ACCESS)

    assert audit.kind is AuditKind.WITH_ACCESS
    inserted = client.queries[0].called("insert")[0][1][0]
    assert inserted == {"customer_id": str(CUSTOMER_ID), "kind": "with_access"}


def test_create_audit_without_returned_row_raises(
    repositories: Repositories, client: FakeClient
) -> None:
    """An insert without row is an error."""
    client.responses = [[]]

    with pytest.raises(StorageError, match="returned no row"):
        repositories.create_audit(CUSTOMER_ID, AuditKind.REMOTE)


def test_finished_status_sets_finished_at(repositories: Repositories, client: FakeClient) -> None:
    """Final statuses stamp finished_at and keep the error."""
    client.responses = [[], []]

    repositories.set_audit_status(AUDIT_ID, AuditStatus.FAILED, "boom")
    repositories.set_audit_status(AUDIT_ID, AuditStatus.SCORED)

    failed_values = client.queries[0].called("update")[0][1][0]
    scored_values = client.queries[1].called("update")[0][1][0]
    assert failed_values["status"] == "failed"
    assert failed_values["error"] == "boom"
    assert "finished_at" in failed_values
    assert "finished_at" not in scored_values


def test_update_audit_sends_only_given_fields(
    repositories: Repositories, client: FakeClient
) -> None:
    """Only passed fields are written."""
    client.responses = [[]]

    repositories.update_audit(AUDIT_ID, total_score=Decimal("61.50"))
    repositories.update_audit(AUDIT_ID)

    assert len(client.queries) == 1
    assert client.queries[0].called("update")[0][1][0] == {"total_score": "61.50"}


def test_previous_audit_filters_reported_and_earlier(
    repositories: Repositories, client: FakeClient
) -> None:
    """Only earlier reported audits count."""
    current = Audit.model_validate(audit_row())
    client.responses = [[audit_row(id=str(uuid4()), status="reported")], []]

    previous = repositories.previous_audit(current)
    none_found = repositories.previous_audit(current)

    assert previous is not None and previous.status is AuditStatus.REPORTED
    assert none_found is None
    query = client.queries[0]
    assert ("eq", ("status", "reported"), {}) in query.calls
    assert query.called("lt")[0][1][0] == "requested_at"
    assert query.called("order") == [("order", ("requested_at",), {"desc": True})]


def test_check_results_are_replaced(
    repositories: Repositories, client: FakeClient, sample_check_results: list[CheckResult]
) -> None:
    """Saving deletes the results, then upserts on the key."""
    client.responses = [[], []]

    repositories.save_check_results(sample_check_results)

    delete, upsert = client.queries
    assert delete.called("in_") == [("in_", ("audit_id", [str(AUDIT_ID)]), {})]
    assert upsert.called("upsert")[0][2] == {"on_conflict": "audit_id,check_id"}
    assert len(upsert.called("upsert")[0][1][0]) == 2


def test_check_results_are_loaded(
    repositories: Repositories, client: FakeClient, sample_check_results: list[CheckResult]
) -> None:
    """Stored results come back as models."""
    client.responses = [[result.model_dump(mode="json") for result in sample_check_results]]

    assert repositories.check_results(AUDIT_ID) == sample_check_results


def _ranking() -> Ranking:
    return Ranking(
        id=uuid4(),
        audit_id=AUDIT_ID,
        keyword_id=uuid4(),
        subject="customer",
        organic_position=3,
        local_pack_position=None,
        grid_point=None,
        measured_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )


def test_rankings_are_loaded_for_one_audit(repositories: Repositories, client: FakeClient) -> None:
    """Stored rankings come back as models, filtered by audit and ordered."""
    ranking = _ranking()
    client.responses = [[ranking.model_dump(mode="json")]]

    assert repositories.rankings(AUDIT_ID) == [ranking]
    query = client.queries[0]
    assert query.target == "rankings"
    assert ("eq", ("audit_id", str(AUDIT_ID)), {}) in query.calls
    assert [call[1][0] for call in query.called("order")] == ["keyword_id", "subject"]


def test_rankings_are_upserted(repositories: Repositories, client: FakeClient) -> None:
    """Saving rankings upserts all rows in one request."""
    client.responses = [[]]

    repositories.save_rankings([_ranking(), _ranking()])

    assert len(client.queries[0].called("upsert")[0][1][0]) == 2


def test_saving_nothing_sends_nothing(repositories: Repositories, client: FakeClient) -> None:
    """Empty lists cause no requests."""
    repositories.save_findings([])
    repositories.save_rankings([])

    assert client.queries == []


def test_findings_are_replaced(repositories: Repositories, client: FakeClient) -> None:
    """Findings use delete-then-upsert as well."""
    finding = Finding(
        id=uuid4(),
        audit_id=AUDIT_ID,
        check_id="a1",
        severity=3,
        title="t",
        explanation="e",
        customer_impact="c",
        fix="f",
        fix_payload=None,
    )
    client.responses = [[], []]

    repositories.save_findings([finding])

    assert [query.target for query in client.queries] == ["findings", "findings"]
    assert client.queries[0].called("delete")


def test_enqueue_returns_existing_queued_job(
    repositories: Repositories, client: FakeClient
) -> None:
    """An identical queued job is reused."""
    existing = job_row()
    client.responses = [[existing]]

    job = repositories.enqueue(JobKind.SCORE, CUSTOMER_ID, AUDIT_ID)

    assert str(job.id) == existing["id"]
    assert len(client.queries) == 1
    assert ("eq", ("audit_id", str(AUDIT_ID)), {}) in client.queries[0].calls


def test_enqueue_inserts_new_job_without_audit(
    repositories: Repositories, client: FakeClient
) -> None:
    """Jobs without audit match a null audit id."""
    client.responses = [[], [job_row(kind="optimize", audit_id=None)]]

    job = repositories.enqueue(JobKind.OPTIMIZE, CUSTOMER_ID, None)

    assert job.kind is JobKind.OPTIMIZE
    lookup, insert = client.queries
    assert ("is_", ("audit_id", "null"), {}) in lookup.calls
    assert insert.called("insert")[0][1][0]["audit_id"] is None


def test_enqueue_with_run_after_stores_the_due_time(
    repositories: Repositories, client: FakeClient
) -> None:
    """A deferred job carries its audit id and the time before which it is not claimed."""
    due = datetime(2026, 10, 23, 12, 15, tzinfo=UTC)
    client.responses = [[], [job_row(kind="serp_retry")]]

    repositories.enqueue(JobKind.SERP_RETRY, CUSTOMER_ID, AUDIT_ID, run_after=due)

    inserted = client.queries[1].called("insert")[0][1][0]
    assert inserted["audit_id"] == str(AUDIT_ID)
    assert inserted["run_after"] == due.isoformat()


def test_requeue_job_with_run_after_defers_the_next_claim(
    repositories: Repositories, client: FakeClient
) -> None:
    """Requeueing with a due time writes it next to status and error."""
    due = datetime(2026, 10, 23, 13, 0, tzinfo=UTC)
    client.responses = [[]]

    repositories.requeue_job(uuid4(), "blocked", run_after=due)

    values = client.queries[0].called("update")[0][1][0]
    assert values["status"] == "queued"
    assert values["error"] == "blocked"
    assert values["run_after"] == due.isoformat()


def test_finish_and_requeue_job(repositories: Repositories, client: FakeClient) -> None:
    """Finishing and requeueing update status and error."""
    client.responses = [[], []]
    job_id = uuid4()

    repositories.finish_job(job_id, JobStatus.DONE)
    repositories.requeue_job(job_id, "timeout")

    assert client.queries[0].called("update")[0][1][0]["status"] == "done"
    requeued = client.queries[1].called("update")[0][1][0]
    assert requeued["status"] == "queued"
    assert requeued["error"] == "timeout"
    assert "run_after" not in requeued


def test_requeue_running_jobs_puts_every_running_job_back(
    repositories: Repositories, client: FakeClient
) -> None:
    """Only running jobs are updated, back to queued with the reason, and they are counted."""
    client.responses = [[job_row(), job_row()]]

    count = repositories.requeue_running_jobs("neu gestartet")

    query = client.queries[0]
    values = query.called("update")[0][1][0]
    assert values["status"] == "queued"
    assert values["error"] == "neu gestartet"
    assert query.called("eq")[0][1] == ("status", "running")
    assert count == 2


def test_save_document_inserts_the_row(repositories: Repositories, client: FakeClient) -> None:
    """A document is inserted and the stored row returned."""
    row = document_row("AN-2026-0001")
    client.responses = [[row]]

    saved = repositories.save_document(Document.model_validate(row))

    assert saved.kind is DocumentKind.QUOTE
    assert client.queries[0].called("insert")


def test_save_document_without_row_raises(repositories: Repositories, client: FakeClient) -> None:
    """An insert without row is an error."""
    client.responses = [[]]
    document = Document.model_validate(document_row("AN-2026-0001"))

    with pytest.raises(StorageError, match="returned no row"):
        repositories.save_document(document)


def test_events_round_trip(repositories: Repositories, client: FakeClient) -> None:
    """Events are written, listed and marked."""
    client.responses = [[], [], []]

    event = repositories.new_event("audit.reported", {"customer_id": str(CUSTOMER_ID)})
    pending = repositories.pending_events()
    repositories.mark_event_delivered(event.id)

    assert pending == []
    assert client.queries[0].called("upsert")[0][1][0]["kind"] == "audit.reported"
    assert ("is_", ("delivered_at", "null"), {}) in client.queries[1].calls
    assert "delivered_at" in client.queries[2].called("update")[0][1][0]


def test_upload_returns_storage_path(
    repositories: Repositories, client: FakeClient, tmp_path: Path
) -> None:
    """Uploads overwrite and return the path in the bucket."""
    local_file = tmp_path / "raw.json"
    local_file.write_text("{}", encoding="utf-8")

    path = repositories.upload("raw", "a/raw.json", local_file)

    assert path == "a/raw.json"
    client.storage.from_.assert_called_with("raw")
    remote, payload, options = client.storage.from_.return_value.upload.call_args.args
    assert (remote, payload) == ("a/raw.json", b"{}")
    assert options == {"content-type": "application/json", "upsert": "true"}


def test_upload_errors_become_storage_errors(
    repositories: Repositories, client: FakeClient, tmp_path: Path
) -> None:
    """Storage and file errors are wrapped."""
    local_file = tmp_path / "raw.json"
    local_file.write_text("{}", encoding="utf-8")
    client.storage.from_.return_value.upload.side_effect = StorageException("bucket missing")

    with pytest.raises(StorageError, match="upload to raw/a/raw.json"):
        repositories.upload("raw", "a/raw.json", local_file)
    with pytest.raises(StorageError, match="read"):
        repositories.upload("raw", "a/raw.json", tmp_path / "missing.json")


def test_download_writes_file(
    repositories: Repositories, client: FakeClient, tmp_path: Path
) -> None:
    """Downloads are retried and written atomically."""
    client.storage.from_.return_value.download.side_effect = [httpx.ReadTimeout("slow"), b"{}"]

    target = repositories.download("raw", "a/raw.json", tmp_path / "nested" / "raw.json")

    assert target.read_bytes() == b"{}"
    assert not (tmp_path / "nested" / ".raw.json.part").exists()


def test_download_errors_become_storage_errors(
    repositories: Repositories, client: FakeClient, tmp_path: Path
) -> None:
    """Storage errors are wrapped."""
    client.storage.from_.return_value.download.side_effect = StorageException("not found")

    with pytest.raises(StorageError, match="download of raw/a/raw.json"):
        repositories.download("raw", "a/raw.json", tmp_path / "raw.json")


def test_download_bytes_returns_the_content(repositories: Repositories, client: FakeClient) -> None:
    """A file is read into memory, retried on a dropped connection."""
    client.storage.from_.return_value.download.side_effect = [httpx.ReadTimeout("slow"), b"RIFF"]

    assert repositories.download_bytes("crm-assistant", "anfragen/a.wav") == b"RIFF"
    client.storage.from_.assert_called_with("crm-assistant")


def test_download_bytes_errors_become_storage_errors(
    repositories: Repositories, client: FakeClient
) -> None:
    """A missing object is a storage error naming bucket and path."""
    client.storage.from_.return_value.download.side_effect = StorageException("not found")

    with pytest.raises(StorageError, match="download of crm-assistant/anfragen/a.wav"):
        repositories.download_bytes("crm-assistant", "anfragen/a.wav")


def test_remove_deletes_one_object_and_retries(
    repositories: Repositories, client: FakeClient
) -> None:
    """Removing is retried on a dropped connection; it is idempotent."""
    bucket = client.storage.from_.return_value
    bucket.remove.side_effect = [httpx.ConnectError("down"), []]

    repositories.remove("crm-assistant", "anfragen/a.wav")

    client.storage.from_.assert_called_with("crm-assistant")
    assert bucket.remove.call_args.args == (["anfragen/a.wav"],)
    assert bucket.remove.call_count == 2


def test_remove_errors_become_storage_errors(
    repositories: Repositories, client: FakeClient
) -> None:
    """A refused removal is a storage error naming bucket and path."""
    client.storage.from_.return_value.remove.side_effect = StorageException("denied")

    with pytest.raises(StorageError, match="remove crm-assistant/anfragen/a.wav"):
        repositories.remove("crm-assistant", "anfragen/a.wav")


def test_document_loads_one_row(repositories: Repositories, client: FakeClient) -> None:
    """One document by id; a missing one is a storage error."""
    row = document_row("AN-2026-0001")
    client.responses = [[row], []]

    assert str(repositories.document(UUID(row["id"])).id) == row["id"]
    with pytest.raises(StorageError, match="no row in documents"):
        repositories.document(uuid4())


def test_pending_documents_filters_open_requests(
    repositories: Repositories, client: FakeClient
) -> None:
    """Rows without file and without error, oldest first."""
    client.responses = [[document_row("AN-2026-0001") | {"number": None}]]

    pending = repositories.pending_documents()

    assert len(pending) == 1
    query = client.queries[0]
    filters = [call[1] for call in query.called("is_")]
    assert ("file_path", "null") in filters
    assert ("data->>error", "null") in filters
    assert query.called("order")[0][1] == ("created_at",)


def test_complete_document_updates_only_open_requests(
    repositories: Repositories, client: FakeClient
) -> None:
    """The update targets the row by id and only while it has no file."""
    row = document_row("AN-2026-0001") | {"file_path": "x/y.pdf"}
    client.responses = [[row]]
    document = Document.model_validate(row)

    completed = repositories.complete_document(document)

    query = client.queries[0]
    values = query.called("update")[0][1][0]
    assert values["number"] == "AN-2026-0001"
    assert values["file_path"] == "x/y.pdf"
    assert "id" not in values and "customer_id" not in values
    assert query.called("eq")[0][1] == ("id", row["id"])
    assert query.called("is_")[0][1] == ("file_path", "null")
    assert completed.number == "AN-2026-0001"


def test_complete_document_without_open_request_raises(
    repositories: Repositories, client: FakeClient
) -> None:
    """An already completed or unknown row is reported."""
    client.responses = [[]]
    document = Document.model_validate(document_row("AN-2026-0001"))

    with pytest.raises(StorageError, match="no open request"):
        repositories.complete_document(document)


def test_set_document_data_replaces_data_of_open_request(
    repositories: Repositories, client: FakeClient
) -> None:
    """Only the data column of a row without file is written."""
    client.responses = [[]]
    document_id = uuid4()

    repositories.set_document_data(document_id, {"error": "kaputt"})

    query = client.queries[0]
    assert query.called("update")[0][1][0] == {"data": {"error": "kaputt"}}
    assert query.called("is_")[0][1] == ("file_path", "null")


def test_set_setting_upserts_by_key(repositories: Repositories, client: FakeClient) -> None:
    """Settings are upserted by key."""
    client.responses = [[]]

    repositories.set_setting("supplier", {"name": "RepoCity"})

    assert client.queries[0].called("upsert")[0][1][0] == {
        "key": "supplier",
        "value": {"name": "RepoCity"},
    }


def test_settings_values_reads_several_keys_at_once(
    repositories: Repositories, client: FakeClient
) -> None:
    """One query returns the values by key; missing keys stay absent."""
    client.responses = [[{"key": "sources", "value": {"places": {"enabled": True}}}]]

    values = repositories.settings_values(("sources", "worker_heartbeat"))

    assert values == {"sources": {"places": {"enabled": True}}}
    query = client.queries[0]
    assert query.called("in_")[0][1] == ("key", ["sources", "worker_heartbeat"])


@pytest.mark.parametrize(("data", "allowed"), [(True, True), (False, False), ([True], True)])
def test_consume_source_quota_calls_database_function(
    repositories: Repositories, client: FakeClient, data: Any, allowed: bool
) -> None:
    """The answer of consume_source_quota says whether the use was within the cap."""
    client.responses = [data]

    assert repositories.consume_source_quota("chatgpt", 200) is allowed
    assert client.queries[0].calls[0] == (
        "rpc",
        ("consume_source_quota", {"p_source": "chatgpt", "p_cap": 200}),
        {},
    )


def test_consume_source_quota_is_never_retried(
    repositories: Repositories, client: FakeClient
) -> None:
    """A transport error is not retried, so one use is never counted twice."""
    client.responses = [httpx.ConnectError("reset")]

    with pytest.raises(StorageError, match="consume quota of chatgpt"):
        repositories.consume_source_quota("chatgpt", 200)
    assert client.executions == 1


def test_consume_source_quota_rejects_odd_answers(
    repositories: Repositories, client: FakeClient
) -> None:
    """Anything but a boolean is an error."""
    client.responses = [None]

    with pytest.raises(StorageError, match="returned None"):
        repositories.consume_source_quota("chatgpt", 200)


@pytest.mark.parametrize(("data", "cost"), [(12.5, "12.5"), ("0.0018", "0.0018"), ([3], "3")])
def test_month_source_cost_reads_the_database_function(
    repositories: Repositories, client: FakeClient, data: Any, cost: str
) -> None:
    """PostgREST may send numeric as number or text; both become a Decimal."""
    client.responses = [data]

    assert repositories.month_source_cost("dataforseo") == Decimal(cost)
    assert client.queries[0].calls[0] == (
        "rpc",
        ("source_month_cost", {"p_source": "dataforseo"}),
        {},
    )


def test_add_source_cost_sends_the_exact_amount_once(
    repositories: Repositories, client: FakeClient
) -> None:
    """The cost travels as text, so no float rounding; a transport error is not retried."""
    client.responses = ["7.0018"]

    assert repositories.add_source_cost("dataforseo", Decimal("0.0018")) == Decimal("7.0018")
    assert client.queries[0].calls[0] == (
        "rpc",
        ("add_source_cost", {"p_source": "dataforseo", "p_cost": "0.0018"}),
        {},
    )
    client.responses = [httpx.ConnectError("reset")]
    with pytest.raises(StorageError, match="add cost of dataforseo"):
        repositories.add_source_cost("dataforseo", Decimal(1))
    assert client.executions == 2


@pytest.mark.parametrize("data", [None, True, "much", []])
def test_cost_functions_reject_odd_answers(
    repositories: Repositories, client: FakeClient, data: Any
) -> None:
    client.responses = [data]

    with pytest.raises(StorageError, match="cost of dataforseo returned"):
        repositories.month_source_cost("dataforseo")


def test_setting_errors_become_storage_errors(
    repositories: Repositories, client: FakeClient
) -> None:
    """A rejected write names the key."""
    client.responses = [APIError({"message": "denied"})]

    with pytest.raises(StorageError, match="save setting supplier"):
        repositories.set_setting("supplier", {})


def test_update_customer_sends_only_given_fields(
    repositories: Repositories, client: FakeClient
) -> None:
    """Identified values are written; fields passed as None are left out, nothing sends nothing."""
    client.responses = [[]]

    repositories.update_customer(CUSTOMER_ID, city="Münster", industry="kfz")
    repositories.update_customer(CUSTOMER_ID)

    assert len(client.queries) == 1
    query = client.queries[0]
    assert query.target == "customers"
    assert query.called("update") == [("update", ({"city": "Münster", "industry": "kfz"},), {})]
    assert query.called("eq") == [("eq", ("id", str(CUSTOMER_ID)), {})]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("00000000-0000-4000-8000-000000000009", UUID("00000000-0000-4000-8000-000000000009")),
        (None, None),
        ([], None),
    ],
)
def test_link_crm_customer_calls_the_database_function(
    repositories: Repositories, client: FakeClient, answer: Any, expected: UUID | None
) -> None:
    """The CRM link goes through link_crm_customer; null means not linkable yet."""
    client.responses = [answer]

    assert repositories.link_crm_customer(CUSTOMER_ID) == expected
    assert client.queries[0].calls[0] == (
        "rpc",
        ("link_crm_customer", {"p_customer_id": str(CUSTOMER_ID)}),
        {},
    )


def test_link_crm_customer_rejects_odd_answers(
    repositories: Repositories, client: FakeClient
) -> None:
    """Anything but an id or null is a storage error."""
    client.responses = ["kein-id"]

    with pytest.raises(StorageError, match="returned 'kein-id'"):
        repositories.link_crm_customer(CUSTOMER_ID)


def test_update_location_sends_only_given_fields(
    repositories: Repositories, client: FakeClient
) -> None:
    """Street, city and coordinates go out; the other fields stay unchanged."""
    location_id = uuid4()
    client.responses = [[]]

    repositories.update_location(location_id, street="Domplatz 3", city="Münster", latitude=51.9)

    assert client.queries[0].target == "locations"
    assert client.queries[0].called("update") == [
        ("update", ({"street": "Domplatz 3", "city": "Münster", "latitude": 51.9},), {})
    ]


def test_add_keywords_skips_known_phrases_and_marks_the_first_primary(
    repositories: Repositories, client: FakeClient
) -> None:
    """Keywords are upserted on their unique key, ignoring rows that exist already."""
    client.responses = [
        [
            {
                "id": str(uuid4()),
                "customer_id": str(CUSTOMER_ID),
                "phrase": "Elektriker",
                "city": "Münster",
                "monthly_volume": None,
                "is_primary": True,
            }
        ]
    ]

    added = repositories.add_keywords(
        CUSTOMER_ID, ["Elektriker", "Heizung Notdienst"], "Münster", first_primary=True
    )

    assert [keyword.phrase for keyword in added] == ["Elektriker"]
    (upsert,) = client.queries[0].called("upsert")
    rows = upsert[1][0]
    assert [(row["phrase"], row["city"], row["is_primary"]) for row in rows] == [
        ("Elektriker", "Münster", True),
        ("Heizung Notdienst", "Münster", False),
    ]
    assert upsert[2] == {"on_conflict": "customer_id,phrase,city", "ignore_duplicates": True}
    assert repositories.add_keywords(CUSTOMER_ID, [], "Münster", first_primary=True) == []


def test_create_location_and_keyword_city(repositories: Repositories, client: FakeClient) -> None:
    """A new location is inserted and returned; keyword cities are updated in one query."""
    location = {
        "id": str(uuid4()),
        "customer_id": str(CUSTOMER_ID),
        "name": "Elektro Brinkmann GmbH",
        "street": "Münsterstraße 5",
        "postal_code": "48291",
        "city": "Telgte",
        "phone": "",
        "google_place_id": None,
        "latitude": None,
        "longitude": None,
    }
    keyword_ids = [uuid4(), uuid4()]
    client.responses = [[location], []]

    from radarauditor.db.models import Location

    created = repositories.create_location(Location.model_validate(location))
    repositories.update_keywords_city(keyword_ids, "Telgte")
    repositories.update_keywords_city([], "Telgte")

    assert created.city == "Telgte"
    assert len(client.queries) == 2
    assert client.queries[1].called("update") == [("update", ({"city": "Telgte"},), {})]
    assert client.queries[1].called("in_") == [
        ("in_", ("id", [str(keyword_id) for keyword_id in keyword_ids]), {})
    ]


def test_audit_reads_its_selection_of_checks(
    repositories: Repositories, client: FakeClient
) -> None:
    """Column disabled_checks of migration 011 arrives on the audit; older rows default to none."""
    client.responses = [[audit_row(disabled_checks=["a2", "n1"])], [audit_row()]]

    assert repositories.audit(AUDIT_ID).disabled_checks == ["a2", "n1"]
    assert repositories.audit(AUDIT_ID).disabled_checks == []
