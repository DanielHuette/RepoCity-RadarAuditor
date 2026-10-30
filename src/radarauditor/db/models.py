"""Typed rows of every table, mirroring the tables in supabase/migrations/.

Context: Shared by collectors, scoring, reporting and the app contract.
Input: Database rows as dictionaries.
Output: Validated pydantic models.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class Customer(BaseModel):
    """A business the operator audits; whether it buys is tracked in the CRM."""

    id: UUID
    name: str
    website: HttpUrl | None
    industry: str
    city: str
    created_at: datetime
    crm_customer_id: UUID | None = None


class Location(BaseModel):
    """One physical or service location of a customer (name, address, phone as written)."""

    id: UUID
    customer_id: UUID
    name: str
    street: str
    postal_code: str
    city: str
    phone: str
    google_place_id: str | None
    latitude: float | None
    longitude: float | None


class Contact(BaseModel):
    """A person at the customer."""

    id: UUID
    customer_id: UUID
    name: str
    role: str
    email: str | None
    phone: str | None


class Competitor(BaseModel):
    """A competing business used for comparison."""

    id: UUID
    customer_id: UUID
    name: str
    google_place_id: str | None
    website: HttpUrl | None


class Keyword(BaseModel):
    """A search phrase tracked for a customer, with the city attached."""

    id: UUID
    customer_id: UUID
    phrase: str
    city: str
    monthly_volume: int | None
    is_primary: bool


class CustomerAccess(BaseModel):
    """What the customer granted the operator's Google account; the WordPress password stays out.

    google_location_name is the Business Profile location ("locations/<id>"),
    analytics_property_id the numeric Google Analytics 4 property id.
    """

    customer_id: UUID
    google_business_profile_granted: bool
    search_console_property: str | None
    wordpress_url: str | None
    wordpress_user: str | None
    granted_at: datetime | None
    google_location_name: str | None = None
    analytics_property_id: str | None = None


class AuditKind(StrEnum):
    """Remote audit from public data, or extended audit with customer access."""

    REMOTE = "remote"
    WITH_ACCESS = "with_access"


class AuditPurpose(StrEnum):
    """Why an audit runs: to win the business, or as the monthly check of a subscription."""

    SALES = "sales"
    MONTHLY = "monthly"


class AuditStatus(StrEnum):
    """Lifecycle of an audit."""

    REQUESTED = "requested"
    COLLECTING = "collecting"
    COLLECTED = "collected"
    SCORED = "scored"
    AWAITING_ANALYSIS = "awaiting_analysis"
    ANALYSED = "analysed"
    REPORTED = "reported"
    FAILED = "failed"


class Audit(BaseModel):
    """One audit run; raw data lives in storage, scores and findings in tables.

    disabled_checks holds the check ids switched off for this audit (migration 011); empty means
    every check runs. purpose (migration 013) is monthly for audits the CRM queues for a
    subscription.
    """

    id: UUID
    customer_id: UUID
    kind: AuditKind
    status: AuditStatus
    requested_at: datetime
    finished_at: datetime | None
    total_score: Decimal | None
    raw_data_path: str | None
    report_path: str | None
    disabled_checks: list[str] = Field(default_factory=list)
    purpose: AuditPurpose = AuditPurpose.SALES


class CheckOutcome(StrEnum):
    """Result of one catalogue check."""

    PASSED = "passed"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_MEASURABLE = "not_measurable"
    SKIPPED = "skipped"


class CheckResult(BaseModel):
    """Outcome of one catalogue check within an audit.

    potential_points is the weight share the check would add when passed; uplift_low and
    uplift_high are the expected extra enquiries per month.
    """

    id: UUID
    audit_id: UUID
    check_id: str
    area: str
    outcome: CheckOutcome
    measured: dict[str, object]
    weight: Decimal
    potential_points: Decimal | None = None
    uplift_low: Decimal | None = None
    uplift_high: Decimal | None = None


class Finding(BaseModel):
    """Plain-language finding plus concrete fix, written by the analyser."""

    id: UUID
    audit_id: UUID
    check_id: str
    severity: int
    title: str
    explanation: str
    customer_impact: str
    fix: str
    fix_payload: dict[str, object] | None


class Ranking(BaseModel):
    """Position of a customer or competitor for a keyword at a point in time."""

    id: UUID
    audit_id: UUID
    keyword_id: UUID
    subject: str
    organic_position: int | None
    local_pack_position: int | None
    grid_point: str | None
    measured_at: datetime


class JobKind(StrEnum):
    """Work items in the queue."""

    COLLECT = "collect"
    SCORE = "score"
    ANALYSE = "analyse"
    REPORT = "report"
    SERP_RETRY = "serp_retry"
    OPTIMIZE = "optimize"
    APPLY = "apply"


class JobStatus(StrEnum):
    """Lifecycle of a queued job."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(BaseModel):
    """A unit of work for the worker or for Claude Code; not claimed before run_after."""

    id: UUID
    kind: JobKind
    status: JobStatus
    audit_id: UUID | None
    customer_id: UUID
    attempts: int
    error: str | None
    created_at: datetime
    updated_at: datetime
    order_id: UUID | None = None
    run_after: datetime | None = None


class DocumentKind(StrEnum):
    """Business documents generated from templates."""

    QUOTE = "quote"
    SERVICE_DESCRIPTION = "service_description"
    CONTRACT = "contract"
    PROOF_OF_SERVICE = "proof_of_service"
    AUDIT_QUOTE = "audit_quote"


class Document(BaseModel):
    """A generated PDF with its data snapshot."""

    id: UUID
    customer_id: UUID
    kind: DocumentKind
    number: str | None
    issued_on: date | None
    net_amount: Decimal | None
    vat_rate: Decimal | None
    gross_amount: Decimal | None
    data: dict[str, object]
    file_path: str | None


class Event(BaseModel):
    """Outbox row for the later customer-management system (webhooks)."""

    id: UUID
    kind: str
    payload: dict[str, object]
    created_at: datetime
    delivered_at: datetime | None


class OptimizationChangeStatus(StrEnum):
    """Lifecycle of a prepared change: nothing is applied before the operator approves it.

    An approved change without an automatic way becomes manual, a to-do for the operator, who ticks
    it off as manual_done in the app (migration 010).
    """

    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"
    MANUAL = "manual"
    MANUAL_DONE = "manual_done"


class OptimizationChange(BaseModel):
    """One change the optimiser prepared for a CRM order, with the text before and after."""

    id: UUID
    order_id: UUID
    audit_id: UUID | None
    check_id: str | None
    target: str
    before: str | None
    after: str
    status: OptimizationChangeStatus
    error: str | None
    created_at: datetime
    decided_at: datetime | None
    applied_at: datetime | None
