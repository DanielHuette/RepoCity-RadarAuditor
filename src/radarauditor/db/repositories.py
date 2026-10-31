"""Read and write rows through the Supabase client.

Context: Thin typed layer over supabase-py; no business rules here.
Input: Settings and models.
Output: Models and storage paths.
"""

import logging
import mimetypes
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
from postgrest import APIError, APIResponse, SyncPostgrestClient
from pydantic import SecretStr, ValidationError
from storage3.utils import StorageException
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from radarauditor.config import Settings
from radarauditor.db.crm import (
    AssistantRequest,
    AssistantResult,
    CrmCustomer,
    CrmOrder,
    CrmProduct,
    CrmQuote,
    CrmQuoteItem,
    MailTriage,
)
from radarauditor.db.models import (
    Audit,
    AuditKind,
    AuditStatus,
    CheckResult,
    Competitor,
    Customer,
    CustomerAccess,
    Document,
    Event,
    Finding,
    Job,
    JobKind,
    JobStatus,
    Keyword,
    Location,
    OptimizationChange,
    OptimizationChangeStatus,
    ProspectSearch,
    Ranking,
)
from radarauditor.errors import StorageError, UnreadableMailError
from supabase import ClientOptions, create_client

logger = logging.getLogger(__name__)

DATABASE_TIMEOUT_SECONDS = 15
STORAGE_TIMEOUT_SECONDS = 60
PAGE_SIZE = 500
CUSTOMER_ACCESS_COLUMNS = (
    "customer_id,google_business_profile_granted,search_console_property,wordpress_url,"
    "wordpress_user,granted_at,google_location_name,analytics_property_id"
)
CRM_SCHEMA = "crm"
# Codes of the German refusals the database functions raise themselves (raise exception, optionally
# with errcode); an error Postgres raised itself has an English text the Cockpit must not show.
CRM_REFUSAL_CODES = frozenset({"P0001", "P0002", "22023", "55000"})
WORDPRESS_PASSWORD_COLUMN = "wordpress_app_password_encrypted"
FINISHED_STATUSES = frozenset({AuditStatus.REPORTED, AuditStatus.FAILED})
CLIENT_ERRORS = (APIError, StorageException, httpx.HTTPError)


class ExecutableQuery(Protocol):
    """Any postgrest builder that can be executed."""

    def execute(self) -> APIResponse:
        """Send the query and return the response."""
        ...


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
def _execute_with_retry(query: ExecutableQuery) -> APIResponse:
    return query.execute()


def _rows(response: APIResponse) -> list[dict[str, Any]]:
    return [row for row in response.data if isinstance(row, dict)]


def _now() -> datetime:
    return datetime.now(UTC)


def _cost(response: APIResponse, source: str) -> Decimal:
    """Read the numeric answer of a cost function; PostgREST sends numeric as number or text."""
    value: object = response.data
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise StorageError(f"cost of {source} returned {value!r}")
    try:
        return Decimal(str(value))
    except ArithmeticError as exc:
        raise StorageError(f"cost of {source} returned {value!r}") from exc


def _claimed_mail(row: dict[str, Any]) -> MailTriage | UnreadableMailError:
    try:
        return MailTriage.model_validate(row)
    except ValidationError as exc:
        try:
            mail_id: UUID | None = UUID(str(row.get("id")))
        except ValueError:
            mail_id = None
        return UnreadableMailError(f"claimed mail {mail_id} has another shape: {exc}", mail_id)


class Repositories:
    """One object holding every table accessor and the storage bucket."""

    def __init__(self, settings: Settings) -> None:
        """Create the Supabase client from settings."""
        options = ClientOptions(
            postgrest_client_timeout=DATABASE_TIMEOUT_SECONDS,
            storage_client_timeout=STORAGE_TIMEOUT_SECONDS,
        )
        self._client = create_client(
            str(settings.supabase_url),
            settings.supabase_service_key.get_secret_value(),
            options=options,
        )
        self._crm_client: SyncPostgrestClient | None = None

    def _crm(self) -> SyncPostgrestClient:
        """PostgREST client of schema crm, created once; writes go through CRM functions only."""
        if self._crm_client is None:
            self._crm_client = self._client.schema(CRM_SCHEMA)
        return self._crm_client

    def _run(self, query: ExecutableQuery, action: str, *, idempotent: bool = True) -> APIResponse:
        """Execute a query; only idempotent queries are retried on transport errors."""
        try:
            if idempotent:
                return _execute_with_retry(query)
            return query.execute()
        except CLIENT_ERRORS as exc:
            own = isinstance(exc, APIError) and exc.code in CRM_REFUSAL_CODES
            reason = exc.message if own and isinstance(exc, APIError) else None
            raise StorageError(f"{action} failed: {exc}", reason) from exc

    def _one(self, table: str, column: str, value: str, action: str) -> dict[str, Any]:
        query = self._client.table(table).select("*").eq(column, value).limit(1)
        rows = _rows(self._run(query, action))
        if not rows:
            raise StorageError(f"{action} failed: no row in {table} with {column}={value}")
        return rows[0]

    def _by_customer(self, table: str, customer_id: UUID) -> list[dict[str, Any]]:
        query = self._client.table(table).select("*").eq("customer_id", str(customer_id))
        return _rows(self._run(query, f"load {table} of customer {customer_id}"))

    def ping(self) -> None:
        """Read one row of the settings table to prove the connection works.

        Raises:
            StorageError: If Supabase cannot be reached or rejects the key.
        """
        self._run(self._client.table("settings").select("key").limit(1), "health check")

    def customer(self, customer_id: UUID) -> Customer:
        """Load one customer or raise StorageError."""
        row = self._one("customers", "id", str(customer_id), f"load customer {customer_id}")
        return Customer.model_validate(row)

    def settings_value(self, key: str) -> object | None:
        """Return the JSON value stored under a key in the settings table, or None."""
        query = self._client.table("settings").select("value").eq("key", key).limit(1)
        rows = _rows(self._run(query, f"load setting {key}"))
        return rows[0].get("value") if rows else None

    def settings_values(self, keys: Sequence[str]) -> dict[str, object]:
        """Return the JSON values of several settings keys in one query; missing keys are absent."""
        query = self._client.table("settings").select("key,value").in_("key", list(keys))
        rows = _rows(self._run(query, f"load settings {', '.join(keys)}"))
        return {str(row["key"]): row.get("value") for row in rows if "key" in row}

    def consume_source_quota(self, source: str, cap: int) -> bool:
        """Count one use of a source today and answer whether it was within the daily cap.

        Not retried: a lost answer would count the use twice.

        Raises:
            StorageError: If the database function fails or answers something else than a boolean.
        """
        query = self._client.rpc("consume_source_quota", {"p_source": source, "p_cap": cap})
        response = self._run(query, f"consume quota of {source}", idempotent=False)
        allowed: object = response.data
        if isinstance(allowed, list):
            allowed = allowed[0] if allowed else None
        if not isinstance(allowed, bool):
            raise StorageError(f"consume quota of {source} returned {allowed!r}")
        return allowed

    def month_source_cost(self, source: str) -> Decimal:
        """Return what a source cost so far this calendar month (Europe/Berlin), in euros.

        Raises:
            StorageError: If the database function fails or answers something else than a number.
        """
        query = self._client.rpc("source_month_cost", {"p_source": source})
        return _cost(self._run(query, f"load monthly cost of {source}"), source)

    def add_source_cost(self, source: str, cost: Decimal) -> Decimal:
        """Add the cost of one request of a source to today's row and return the month's spend.

        Not retried: a lost answer would count the cost twice.

        Raises:
            StorageError: If the database function fails or answers something else than a number.
        """
        query = self._client.rpc("add_source_cost", {"p_source": source, "p_cost": str(cost)})
        return _cost(self._run(query, f"add cost of {source}", idempotent=False), source)

    def set_setting(self, key: str, value: object) -> None:
        """Store a JSON value under a key in the settings table, replacing an existing one."""
        row: dict[str, Any] = {"key": key, "value": value}
        query = self._client.table("settings").upsert(row)
        self._run(query, f"save setting {key}")

    def customer_access(self, customer_id: UUID) -> CustomerAccess | None:
        """Return what the customer granted, without the WordPress password, or None."""
        query = (
            self._client.table("customer_access")
            .select(CUSTOMER_ACCESS_COLUMNS)
            .eq("customer_id", str(customer_id))
            .limit(1)
        )
        rows = _rows(self._run(query, f"load access of customer {customer_id}"))
        return CustomerAccess.model_validate(rows[0]) if rows else None

    def wordpress_password(self, customer_id: UUID) -> SecretStr | None:
        """Return the stored WordPress application password of a customer, or None.

        The app writes it to column wordpress_app_password_encrypted as entered; it is only read
        here, right before a change is applied, and never written to a file.
        """
        query = (
            self._client.table("customer_access")
            .select(WORDPRESS_PASSWORD_COLUMN)
            .eq("customer_id", str(customer_id))
            .limit(1)
        )
        rows = _rows(self._run(query, f"load WordPress access of customer {customer_id}"))
        value = rows[0].get(WORDPRESS_PASSWORD_COLUMN) if rows else None
        return SecretStr(value) if isinstance(value, str) and value.strip() else None

    def update_customer(
        self,
        customer_id: UUID,
        *,
        name: str | None = None,
        city: str | None = None,
        industry: str | None = None,
    ) -> None:
        """Set the given identity fields of a customer; fields passed as None stay unchanged.

        Args:
            customer_id: Customer to update.
            name: New business name.
            city: New city.
            industry: New industry group id.

        Raises:
            StorageError: If Supabase rejects the update.
        """
        values: dict[str, Any] = {
            key: value
            for key, value in (("name", name), ("city", city), ("industry", industry))
            if value is not None
        }
        if not values:
            return
        query = self._client.table("customers").update(values).eq("id", str(customer_id))
        self._run(query, f"update customer {customer_id}")

    def link_crm_customer(self, customer_id: UUID) -> UUID | None:
        """Link a customer to a CRM customer once name and city are known (migration 011).

        Returns:
            The CRM customer id, None while the city is empty or the name is the website host.

        Raises:
            StorageError: If the database function fails or answers something else than an id.
        """
        query = self._client.rpc("link_crm_customer", {"p_customer_id": str(customer_id)})
        answer: object = self._run(query, f"link customer {customer_id} to the CRM").data
        if isinstance(answer, list):
            answer = answer[0] if answer else None
        if answer is None:
            return None
        try:
            return UUID(str(answer))
        except ValueError as exc:
            raise StorageError(f"link customer {customer_id} returned {answer!r}") from exc

    def create_location(self, location: Location) -> Location:
        """Insert a location and return the stored row.

        Args:
            location: Location to insert, with its new id.

        Returns:
            The location as stored.

        Raises:
            StorageError: If Supabase rejects the insert or returns no row.
        """
        query = self._client.table("locations").insert(location.model_dump(mode="json"))
        action = f"create location of customer {location.customer_id}"
        rows = _rows(self._run(query, action, idempotent=False))
        if not rows:
            raise StorageError(f"{action} returned no row")
        return Location.model_validate(rows[0])

    def update_location(
        self,
        location_id: UUID,
        *,
        name: str | None = None,
        street: str | None = None,
        postal_code: str | None = None,
        city: str | None = None,
        phone: str | None = None,
        google_place_id: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> None:
        """Set the given fields of a location; fields passed as None stay unchanged.

        Args:
            location_id: Location to update.
            name: Name shown for the location.
            street: Street and house number.
            postal_code: Postal code.
            city: City.
            phone: Phone number.
            google_place_id: ChIJ place id of the Google profile.
            latitude: Latitude of the profile on the map.
            longitude: Longitude of the profile on the map.

        Raises:
            StorageError: If Supabase rejects the update.
        """
        given: dict[str, object] = {
            "name": name,
            "street": street,
            "postal_code": postal_code,
            "city": city,
            "phone": phone,
            "google_place_id": google_place_id,
            "latitude": latitude,
            "longitude": longitude,
        }
        values: dict[str, Any] = {key: value for key, value in given.items() if value is not None}
        if not values:
            return
        query = self._client.table("locations").update(values).eq("id", str(location_id))
        self._run(query, f"update location {location_id}")

    def add_keywords(
        self, customer_id: UUID, phrases: list[str], city: str, *, first_primary: bool
    ) -> list[Keyword]:
        """Insert keywords of a customer in one city; phrases it already tracks there are skipped.

        Args:
            customer_id: Customer the keywords belong to.
            phrases: Phrases without the city, in order.
            city: City searched together with every phrase.
            first_primary: Whether the first phrase becomes the primary keyword.

        Returns:
            The inserted rows.
        """
        if not phrases:
            return []
        rows: list[dict[str, Any]] = [
            {
                "customer_id": str(customer_id),
                "phrase": phrase,
                "city": city,
                "is_primary": first_primary and index == 0,
            }
            for index, phrase in enumerate(phrases)
        ]
        query = self._client.table("keywords").upsert(
            rows, on_conflict="customer_id,phrase,city", ignore_duplicates=True
        )
        inserted = _rows(self._run(query, f"add keywords of customer {customer_id}"))
        return [Keyword.model_validate(row) for row in inserted]

    def update_keywords_city(self, keyword_ids: Sequence[UUID], city: str) -> None:
        """Set the city of keywords stored while the customer's city was unknown, in one query.

        Args:
            keyword_ids: Keywords to update; none means no query.
            city: City to store.

        Raises:
            StorageError: If Supabase rejects the update.
        """
        if not keyword_ids:
            return
        values: dict[str, Any] = {"city": city}
        ids = [str(keyword_id) for keyword_id in keyword_ids]
        query = self._client.table("keywords").update(values).in_("id", ids)
        self._run(query, f"update city of {len(ids)} keywords")

    def locations(self, customer_id: UUID) -> list[Location]:
        """Load all locations of a customer."""
        return [Location.model_validate(row) for row in self._by_customer("locations", customer_id)]

    def competitors(self, customer_id: UUID) -> list[Competitor]:
        """Load the competitors of a customer."""
        rows = self._by_customer("competitors", customer_id)
        return [Competitor.model_validate(row) for row in rows]

    def keywords(self, customer_id: UUID) -> list[Keyword]:
        """Load the tracked keywords of a customer."""
        return [Keyword.model_validate(row) for row in self._by_customer("keywords", customer_id)]

    def update_keyword_volume(self, keyword_id: UUID, volume: int) -> None:
        """Store the measured monthly search volume of a keyword."""
        values: dict[str, Any] = {"monthly_volume": volume}
        query = self._client.table("keywords").update(values).eq("id", str(keyword_id))
        self._run(query, f"update volume of keyword {keyword_id}")

    def audit(self, audit_id: UUID) -> Audit:
        """Load one audit."""
        row = self._one("audits", "id", str(audit_id), f"load audit {audit_id}")
        return Audit.model_validate(row)

    def create_audit(self, customer_id: UUID, kind: AuditKind) -> Audit:
        """Insert a new audit in status requested and return it."""
        row = {"customer_id": str(customer_id), "kind": kind.value}
        query = self._client.table("audits").insert(row)
        rows = _rows(self._run(query, f"create audit for {customer_id}", idempotent=False))
        if not rows:
            raise StorageError(f"create audit for {customer_id} returned no row")
        return Audit.model_validate(rows[0])

    def set_audit_status(
        self, audit_id: UUID, status: AuditStatus, error: str | None = None
    ) -> None:
        """Advance an audit's lifecycle."""
        values: dict[str, Any] = {"status": status.value, "error": error}
        if status in FINISHED_STATUSES:
            values["finished_at"] = _now().isoformat()
        query = self._client.table("audits").update(values).eq("id", str(audit_id))
        self._run(query, f"set status of audit {audit_id}")

    def update_audit(
        self,
        audit_id: UUID,
        *,
        raw_data_path: str | None = None,
        total_score: Decimal | None = None,
        report_path: str | None = None,
    ) -> None:
        """Set the given result fields of an audit; fields passed as None stay unchanged."""
        values: dict[str, Any] = {}
        if raw_data_path is not None:
            values["raw_data_path"] = raw_data_path
        if total_score is not None:
            values["total_score"] = str(total_score)
        if report_path is not None:
            values["report_path"] = report_path
        if not values:
            return
        query = self._client.table("audits").update(values).eq("id", str(audit_id))
        self._run(query, f"update audit {audit_id}")

    def previous_audit(self, audit: Audit) -> Audit | None:
        """Return the last finished audit of the same customer before this one."""
        query = (
            self._client.table("audits")
            .select("*")
            .eq("customer_id", str(audit.customer_id))
            .eq("status", AuditStatus.REPORTED.value)
            .lt("requested_at", audit.requested_at.isoformat())
            .neq("id", str(audit.id))
            .order("requested_at", desc=True)
            .limit(1)
        )
        rows = _rows(self._run(query, f"load audit before {audit.id}"))
        return Audit.model_validate(rows[0]) if rows else None

    def audits_of_customer(self, customer_id: UUID) -> list[Audit]:
        """Load every audit of a customer, oldest first."""
        query = (
            self._client.table("audits")
            .select("*")
            .eq("customer_id", str(customer_id))
            .order("requested_at")
        )
        rows = _rows(self._run(query, f"load audits of customer {customer_id}"))
        return [Audit.model_validate(row) for row in rows]

    def check_results(self, audit_id: UUID) -> list[CheckResult]:
        """Load the check results of an audit ordered by check id."""
        query = (
            self._client.table("check_results")
            .select("*")
            .eq("audit_id", str(audit_id))
            .order("check_id")
        )
        rows = _rows(self._run(query, f"load check results of audit {audit_id}"))
        return [CheckResult.model_validate(row) for row in rows]

    def findings_of_audit(self, audit_id: UUID) -> list[Finding]:
        """Load the findings of an audit ordered by check id."""
        query = (
            self._client.table("findings")
            .select("*")
            .eq("audit_id", str(audit_id))
            .order("check_id")
        )
        rows = _rows(self._run(query, f"load findings of audit {audit_id}"))
        return [Finding.model_validate(row) for row in rows]

    def save_check_results(self, results: list[CheckResult]) -> None:
        """Replace the check results of an audit (idempotent).

        Unset optional columns are left out, so a database without migration 006 accepts the rows.
        """
        rows = [result.model_dump(mode="json", exclude_none=True) for result in results]
        self._replace_by_audit("check_results", rows)

    def rankings(self, audit_id: UUID) -> list[Ranking]:
        """Load the ranking rows of an audit ordered by keyword and subject."""
        query = (
            self._client.table("rankings")
            .select("*")
            .eq("audit_id", str(audit_id))
            .order("keyword_id")
            .order("subject")
        )
        rows = _rows(self._run(query, f"load rankings of audit {audit_id}"))
        return [Ranking.model_validate(row) for row in rows]

    def save_rankings(self, rankings: list[Ranking]) -> None:
        """Upsert ranking rows by id, so a rerun with derived ids replaces them."""
        if not rankings:
            return
        rows = [ranking.model_dump(mode="json") for ranking in rankings]
        self._run(self._client.table("rankings").upsert(rows), "save rankings")

    def save_findings(self, findings: list[Finding]) -> None:
        """Replace the findings of an audit (idempotent)."""
        self._replace_by_audit("findings", [f.model_dump(mode="json") for f in findings])

    def _replace_by_audit(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Delete every row of the audits involved, then insert the new rows."""
        if not rows:
            return
        audit_ids = sorted({str(row["audit_id"]) for row in rows})
        delete = self._client.table(table).delete().in_("audit_id", audit_ids)
        self._run(delete, f"clear {table} of audits {', '.join(audit_ids)}")
        insert = self._client.table(table).upsert(rows, on_conflict="audit_id,check_id")
        self._run(insert, f"save {table} of audits {', '.join(audit_ids)}")

    def claim_jobs(self, kind: JobKind, limit: int) -> list[Job]:
        """Atomically move queued jobs of a kind to running and return them."""
        query = self._client.rpc("claim_jobs", {"p_kind": kind.value, "p_limit": limit})
        response = self._run(query, f"claim {kind.value} jobs", idempotent=False)
        return [Job.model_validate(row) for row in _rows(response)]

    def finish_job(self, job_id: UUID, status: JobStatus, error: str | None = None) -> None:
        """Mark a job done or failed."""
        values = {"status": status.value, "error": error, "updated_at": _now().isoformat()}
        query = self._client.table("jobs").update(values).eq("id", str(job_id))
        self._run(query, f"finish job {job_id}")

    def requeue_job(self, job_id: UUID, error: str, *, run_after: datetime | None = None) -> None:
        """Put a failed job back into the queue and keep the error for the next attempt.

        With run_after the job is not claimed before that time (migration 007).
        """
        values: dict[str, Any] = {
            "status": JobStatus.QUEUED.value,
            "error": error,
            "updated_at": _now().isoformat(),
        }
        if run_after is not None:
            values["run_after"] = run_after.isoformat()
        query = self._client.table("jobs").update(values).eq("id", str(job_id))
        self._run(query, f"requeue job {job_id}")

    def requeue_running_jobs(self, error: str) -> int:
        """Put every running job back into the queue, for a worker that starts after a stop.

        One worker runs the queue, so a job still marked running at its start was cut off by a
        restart or update of the container and would otherwise stay running for good.

        Returns:
            How many jobs were put back.
        """
        values: dict[str, Any] = {
            "status": JobStatus.QUEUED.value,
            "error": error,
            "updated_at": _now().isoformat(),
        }
        query = self._client.table("jobs").update(values).eq("status", JobStatus.RUNNING.value)
        return len(_rows(self._run(query, "requeue running jobs")))

    def enqueue(
        self,
        kind: JobKind,
        customer_id: UUID,
        audit_id: UUID | None,
        *,
        run_after: datetime | None = None,
    ) -> Job:
        """Add a job unless an identical queued job exists.

        With run_after the job is not claimed before that time (migration 007).
        """
        existing = (
            self._client.table("jobs")
            .select("*")
            .eq("kind", kind.value)
            .eq("status", JobStatus.QUEUED.value)
            .eq("customer_id", str(customer_id))
        )
        if audit_id is None:
            existing = existing.is_("audit_id", "null")
        else:
            existing = existing.eq("audit_id", str(audit_id))
        rows = _rows(self._run(existing.limit(1), f"look up queued {kind.value} job"))
        if rows:
            return Job.model_validate(rows[0])
        row = {
            "kind": kind.value,
            "customer_id": str(customer_id),
            "audit_id": str(audit_id) if audit_id else None,
        }
        if run_after is not None:
            row["run_after"] = run_after.isoformat()
        query = self._client.table("jobs").insert(row)
        inserted = _rows(self._run(query, f"enqueue {kind.value} job", idempotent=False))
        if not inserted:
            raise StorageError(f"enqueue {kind.value} job returned no row")
        return Job.model_validate(inserted[0])

    def save_document(self, document: Document) -> Document:
        """Insert a document row."""
        query = self._client.table("documents").insert(document.model_dump(mode="json"))
        rows = _rows(self._run(query, f"save document {document.id}", idempotent=False))
        if not rows:
            raise StorageError(f"save document {document.id} returned no row")
        return Document.model_validate(rows[0])

    def document(self, document_id: UUID) -> Document:
        """Load one document or raise StorageError."""
        row = self._one("documents", "id", str(document_id), f"load document {document_id}")
        return Document.model_validate(row)

    def pending_documents(self) -> list[Document]:
        """Load document requests of the app: rows without a file and without a recorded error.

        Oldest first; a request whose data carries "error" stays out until the error is removed.
        """
        query = (
            self._client.table("documents")
            .select("*")
            .is_("file_path", "null")
            .is_("data->>error", "null")
            .order("created_at")
            .limit(PAGE_SIZE)
        )
        rows = _rows(self._run(query, "load document requests"))
        return [Document.model_validate(row) for row in rows]

    def complete_document(self, document: Document) -> Document:
        """Fill a requested row with number, date, amounts, data and file path.

        Only a row that still has no file is updated, so a request is completed at most once.

        Raises:
            StorageError: If the row is missing or already completed, or the update fails.
        """
        values = document.model_dump(
            mode="json",
            include={
                "number",
                "issued_on",
                "net_amount",
                "vat_rate",
                "gross_amount",
                "data",
                "file_path",
            },
        )
        query = (
            self._client.table("documents")
            .update(values)
            .eq("id", str(document.id))
            .is_("file_path", "null")
        )
        rows = _rows(self._run(query, f"complete document {document.id}", idempotent=False))
        if not rows:
            raise StorageError(f"complete document {document.id}: no open request with this id")
        return Document.model_validate(rows[0])

    def set_document_data(self, document_id: UUID, data: dict[str, object]) -> None:
        """Replace the data of a document that has no file yet, e.g. to record an error."""
        values: dict[str, Any] = {"data": data}
        query = (
            self._client.table("documents")
            .update(values)
            .eq("id", str(document_id))
            .is_("file_path", "null")
        )
        self._run(query, f"update data of document {document_id}")

    def _crm_one(self, table: str, row_id: UUID, action: str) -> dict[str, Any]:
        query = self._crm().table(table).select("*").eq("id", str(row_id)).limit(1)
        rows = _rows(self._run(query, action))
        if not rows:
            raise StorageError(f"{action} failed: no row in crm.{table} with id={row_id}")
        return rows[0]

    def crm_customer(self, customer_id: UUID) -> CrmCustomer:
        """Load one CRM customer or raise StorageError."""
        action = f"load CRM customer {customer_id}"
        return CrmCustomer.model_validate(self._crm_one("customers", customer_id, action))

    def crm_quote(self, quote_id: UUID) -> CrmQuote:
        """Load one CRM quote or raise StorageError."""
        action = f"load CRM quote {quote_id}"
        return CrmQuote.model_validate(self._crm_one("quotes", quote_id, action))

    def crm_quote_items(self, quote_id: UUID) -> list[CrmQuoteItem]:
        """Load the positions of a CRM quote in their order."""
        query = (
            self._crm()
            .table("quote_items")
            .select("*")
            .eq("quote_id", str(quote_id))
            .order("position")
        )
        rows = _rows(self._run(query, f"load items of CRM quote {quote_id}"))
        return [CrmQuoteItem.model_validate(row) for row in rows]

    def crm_order(self, order_id: UUID) -> CrmOrder:
        """Load one CRM order or raise StorageError."""
        return CrmOrder.model_validate(self._crm_one("orders", order_id, f"load order {order_id}"))

    def crm_products(self, product_ids: list[UUID]) -> list[CrmProduct]:
        """Load several CRM products in one query; unknown ids are skipped."""
        if not product_ids:
            return []
        ids = sorted({str(product_id) for product_id in product_ids})
        query = self._crm().table("products").select("*").in_("id", ids)
        rows = _rows(self._run(query, f"load {len(ids)} CRM products"))
        return [CrmProduct.model_validate(row) for row in rows]

    def claim_mail_triage(self, limit: int, seconds: int) -> list[MailTriage | UnreadableMailError]:
        """Claim up to limit queued mails for seconds through crm.claim_mail_triage.

        Returns:
            One entry per claimed row: the mail, or the error of a row of another shape.

        Raises:
            StorageError: If the CRM function fails; a claim is never repeated on its own.
        """
        params: dict[str, Any] = {"p_limit": limit, "p_seconds": seconds}
        query = self._crm().rpc("claim_mail_triage", params)
        rows = _rows(self._run(query, "claim mails for triage", idempotent=False))
        return [_claimed_mail(row) for row in rows]

    def classify_mail_triage(self, mail_id: UUID, result: dict[str, object]) -> None:
        """Hand Claude Code's validated answer about a mail to crm.classify_mail_triage."""
        params: dict[str, Any] = {"p_id": str(mail_id), "p_result": result}
        query = self._crm().rpc("classify_mail_triage", params)
        self._run(query, f"classify mail {mail_id}", idempotent=False)

    def fail_mail_triage(self, mail_id: UUID, error: str) -> None:
        """Report why a mail could not be read; the CRM queues it again or gives up."""
        params: dict[str, Any] = {"p_id": str(mail_id), "p_error": error}
        query = self._crm().rpc("fail_mail_triage", params)
        self._run(query, f"record failure of mail {mail_id}", idempotent=False)

    def claim_assistant_request(self) -> AssistantRequest | None:
        """Claim the oldest waiting request of the Cockpit assistant.

        Returns:
            The claimed request, or None when none waits.

        Raises:
            StorageError: If the CRM function fails or the row has another shape; a claim is never
                repeated on its own.
        """
        params: dict[str, Any] = {}
        query = self._crm().rpc("claim_assistant_request", params)
        rows = _rows(self._run(query, "claim assistant request", idempotent=False))
        if not rows:
            return None
        try:
            return AssistantRequest.model_validate(rows[0])
        except ValidationError as exc:
            raise StorageError(f"claim assistant request failed: unexpected row: {exc}") from exc

    def orphan_recordings(self) -> list[str]:
        """Paths of recordings older than ten minutes that no request names."""
        params: dict[str, Any] = {}
        query = self._crm().rpc("orphan_recordings", params)
        rows = _rows(self._run(query, "list recordings without a request"))
        return [str(row["path"]) for row in rows]

    def save_assistant_transcript(self, request_id: UUID, text: str) -> None:
        """Store what Daniel said as the text of a claimed request."""
        params: dict[str, Any] = {"p_request": str(request_id), "p_text": text}
        query = self._crm().rpc("save_assistant_transcript", params)
        self._run(query, f"save transcript of assistant request {request_id}", idempotent=False)

    def apply_assistant_intent(
        self, request_id: UUID, intent: dict[str, object]
    ) -> AssistantResult:
        """Hand a validated intent to crm.apply_assistant_intent, which carries it out.

        Returns:
            Whether the CRM did it, and its German sentence about what it stored.

        Raises:
            StorageError: If the CRM function fails or answers in another shape.
        """
        params: dict[str, Any] = {"p_request": str(request_id), "p_intent": intent}
        query = self._crm().rpc("apply_assistant_intent", params)
        action = f"apply assistant intent of request {request_id}"
        response = self._run(query, action, idempotent=False)
        try:
            return AssistantResult.model_validate(response.data)
        except ValidationError as exc:
            raise StorageError(f"{action} failed: unexpected answer {response.data!r}") from exc

    def fail_assistant_request(self, request_id: UUID, error: str) -> None:
        """Finish a request as not done with a German reason; the Cockpit shows it."""
        params: dict[str, Any] = {"p_request": str(request_id), "p_error": error}
        query = self._crm().rpc("fail_assistant_request", params)
        self._run(query, f"record failure of assistant request {request_id}", idempotent=False)

    def claim_prospect_search(self) -> ProspectSearch | None:
        """Take the oldest waiting customer search; stored lists past their cache days are purged.

        Returns:
            The search now running, or None when none waits.
        """
        query = self._client.rpc("claim_prospect_search", {})
        rows = _rows(self._run(query, "claim customer search", idempotent=False))
        return ProspectSearch.model_validate(rows[0]) if rows else None

    def finish_prospect_search(
        self, search_id: UUID, results: list[dict[str, object]], note: str | None
    ) -> int:
        """Store the ranked list of a running search in one transaction.

        Args:
            search_id: The claimed search.
            results: Rows in the shape of ProspectResult.model_dump(mode="json").
            note: Why the list is incomplete, or None.

        Returns:
            How many results the search now has.
        """
        params: dict[str, Any] = {"p_search": str(search_id), "p_results": results, "p_note": note}
        query = self._client.rpc("finish_prospect_search", params)
        value: object = self._run(query, f"store customer search {search_id}").data
        return value if isinstance(value, int) else len(results)

    def fail_prospect_search(self, search_id: UUID, error: str) -> None:
        """Record the German reason why a running search failed."""
        params = {"p_search": str(search_id), "p_error": error}
        query = self._client.rpc("fail_prospect_search", params)
        self._run(query, f"record failure of customer search {search_id}")

    def queue_monthly_service(self, audit_id: UUID) -> UUID | None:
        """Queue the optimize job of the monthly service for a monthly audit (migration 016).

        Returns:
            The job id, or None when the customer has no subscription order in the CRM.
        """
        query = self._client.rpc("queue_monthly_service", {"p_audit": str(audit_id)})
        value: object = self._run(query, f"queue monthly service of audit {audit_id}").data
        return UUID(value) if isinstance(value, str) else None

    def optimization_changes(
        self, order_id: UUID, status: OptimizationChangeStatus | None = None
    ) -> list[OptimizationChange]:
        """Load the prepared changes of an order, oldest first, optionally of one status."""
        query = self._client.table("optimization_changes").select("*").eq("order_id", str(order_id))
        if status is not None:
            query = query.eq("status", status.value)
        rows = _rows(self._run(query.order("created_at"), f"load changes of order {order_id}"))
        return [OptimizationChange.model_validate(row) for row in rows]

    def replace_draft_changes(
        self, order_id: UUID, audit_id: UUID | None, changes: list[OptimizationChange]
    ) -> None:
        """Replace the undecided drafts an order got from one audit; all other rows stay.

        Drafts of another audit, such as the open drafts of the first optimisation while a monthly
        run stores its own, are never touched.

        Raises:
            StorageError: If deleting or inserting fails.
        """
        delete = (
            self._client.table("optimization_changes")
            .delete()
            .eq("order_id", str(order_id))
            .eq("status", OptimizationChangeStatus.DRAFT.value)
        )
        delete = (
            delete.is_("audit_id", "null")
            if audit_id is None
            else delete.eq("audit_id", str(audit_id))
        )
        self._run(delete, f"clear drafts of order {order_id}")
        if not changes:
            return
        rows = [
            change.model_dump(
                mode="json", exclude={"decided_at", "applied_at", "error"}, exclude_none=True
            )
            for change in changes
        ]
        insert = self._client.table("optimization_changes").insert(rows)
        self._run(insert, f"save {len(rows)} drafts of order {order_id}", idempotent=False)

    def finish_change(
        self, change_id: UUID, status: OptimizationChangeStatus, error: str | None = None
    ) -> None:
        """Record the outcome of an approved change: applied, manual or failed with the reason.

        Only a change that is still approved is updated, so an outcome is recorded once.
        """
        values: dict[str, Any] = {"status": status.value, "error": error}
        if status is OptimizationChangeStatus.APPLIED:
            values["applied_at"] = _now().isoformat()
        query = (
            self._client.table("optimization_changes")
            .update(values)
            .eq("id", str(change_id))
            .eq("status", OptimizationChangeStatus.APPROVED.value)
        )
        self._run(query, f"record outcome of change {change_id}")

    def emit_event(self, event: Event) -> None:
        """Write an outbox event for the customer-management system."""
        query = self._client.table("events").upsert(event.model_dump(mode="json"))
        self._run(query, f"emit event {event.kind}")

    def new_event(self, kind: str, payload: dict[str, object]) -> Event:
        """Build and write an outbox event with a fresh id and timestamp."""
        event = Event(id=uuid4(), kind=kind, payload=payload, created_at=_now(), delivered_at=None)
        self.emit_event(event)
        return event

    def pending_events(self) -> list[Event]:
        """Load undelivered outbox events, oldest first."""
        query = (
            self._client.table("events")
            .select("*")
            .is_("delivered_at", "null")
            .order("created_at")
            .limit(PAGE_SIZE)
        )
        return [Event.model_validate(row) for row in _rows(self._run(query, "load events"))]

    def mark_event_delivered(self, event_id: UUID) -> None:
        """Stamp an outbox event as delivered."""
        values = {"delivered_at": _now().isoformat()}
        query = self._client.table("events").update(values).eq("id", str(event_id))
        self._run(query, f"mark event {event_id} delivered")

    def upload(self, bucket: str, remote_path: str, local_file: Path) -> str:
        """Upload a file and return its storage path."""
        content_type = mimetypes.guess_type(local_file.name)[0] or "application/octet-stream"
        options = {"content-type": content_type, "upsert": "true"}
        try:
            payload = local_file.read_bytes()
            self._upload_with_retry(bucket, remote_path, payload, options)
        except OSError as exc:
            raise StorageError(f"read {local_file} for upload failed: {exc}") from exc
        except CLIENT_ERRORS as exc:
            raise StorageError(f"upload to {bucket}/{remote_path} failed: {exc}") from exc
        logger.info("Uploaded %s to %s/%s", local_file.name, bucket, remote_path)
        return remote_path

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )
    def _upload_with_retry(
        self, bucket: str, remote_path: str, payload: bytes, options: dict[str, str]
    ) -> None:
        self._client.storage.from_(bucket).upload(remote_path, payload, options)  # type: ignore[arg-type]

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )
    def _download_with_retry(self, bucket: str, remote_path: str) -> bytes:
        return self._client.storage.from_(bucket).download(remote_path)

    def download_bytes(self, bucket: str, remote_path: str) -> bytes:
        """Read a file of a bucket into memory."""
        try:
            return self._download_with_retry(bucket, remote_path)
        except CLIENT_ERRORS as exc:
            raise StorageError(f"download of {bucket}/{remote_path} failed: {exc}") from exc

    def download(self, bucket: str, remote_path: str, local_file: Path) -> Path:
        """Download a file to disk."""
        content = self.download_bytes(bucket, remote_path)
        partial = local_file.with_name(f".{local_file.name}.part")
        try:
            local_file.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(content)
            partial.replace(local_file)
        except OSError as exc:
            raise StorageError(f"write {local_file} failed: {exc}") from exc
        return local_file

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )
    def _remove_with_retry(self, bucket: str, remote_path: str) -> None:
        self._client.storage.from_(bucket).remove([remote_path])

    def remove(self, bucket: str, remote_path: str) -> None:
        """Delete one file of a bucket; a file that is already gone counts as removed."""
        try:
            self._remove_with_retry(bucket, remote_path)
        except CLIENT_ERRORS as exc:
            raise StorageError(f"remove {bucket}/{remote_path} failed: {exc}") from exc
        logger.info("Removed %s/%s", bucket, remote_path)
