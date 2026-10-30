"""Fill a document template, render it and store the snapshot.

Context: Quotes, service descriptions, contracts and proofs of service of the RadarAuditor; invoices
are written by the CRM.
Input: DocumentKind and data.
Output: Document row with file path.

Supplier data come from the settings table, key "supplier", a JSON object with the required keys
name, street, postal_code, city, email, iban, bic, bank_name and at least one of tax_number or
vat_id; optional keys are owner, phone, small_business (default false) and payment_terms_days
(default 14).

Keys of `data` per kind; customer_name, customer_street, customer_postal_code and customer_city
default to the customer and its first location, note is optional everywhere:
- quote: positions [{description, quantity (default 1), unit_net}], optional vat_rate (default 19),
  small_business and valid_until (default 14 days).
- service_description: packages [{name, items, optional net_price, recurrence: once | monthly}],
  optional offer, start_date and agreed_services [text].
- contract: start_date, monthly_fee_net, optional setup_fee_net, packages, minimum_term_months
  (default 0) and notice_period_months (default 1).
- proof_of_service: month (YYYY-MM) or period (free text such as "Q3 2026"), activities [text].

A document requested by the app already has a row; build_document(request=row) fills that row
instead of inserting one.
"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal, NamedTuple
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

from radarauditor.config import Settings
from radarauditor.db.models import Customer, Document, DocumentKind
from radarauditor.db.repositories import Repositories
from radarauditor.errors import RenderError
from radarauditor.rendering import filename_slug, render_template, write_pdf

logger = logging.getLogger(__name__)

DOCUMENTS_BUCKET = "documents"
DOCUMENT_DIR = "dokumente"
TEMPLATE_DIR = "documents"
SUPPLIER_SETTING = "supplier"
DOCUMENT_ISSUED_EVENT = "document.issued"
QUOTE_VALIDITY_DAYS = 14
DEFAULT_VAT_RATE = Decimal(19)
DEFAULT_PAYMENT_TERMS_DAYS = 14
DEFAULT_NOTICE_PERIOD_MONTHS = 1
MONTHS_PER_YEAR = 12
HUNDRED = Decimal(100)
CENT = Decimal("0.01")
DEFAULT_TEMPLATES_DIR = Path("templates")

_JSON_ADAPTER: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class Supplier(BaseModel):
    """The issuing business as stored in settings key "supplier"."""

    name: str = Field(min_length=1)
    owner: str | None = None
    street: str = Field(min_length=1)
    postal_code: str = Field(min_length=1)
    city: str = Field(min_length=1)
    email: str = Field(min_length=1)
    phone: str | None = None
    tax_number: str | None = None
    vat_id: str | None = None
    iban: str = Field(min_length=1)
    bic: str = Field(min_length=1)
    bank_name: str = Field(min_length=1)
    small_business: bool = False
    payment_terms_days: int = Field(default=DEFAULT_PAYMENT_TERMS_DAYS, ge=0)

    @model_validator(mode="after")
    def _has_tax_id(self) -> "Supplier":
        if not (self.tax_number or self.vat_id):
            raise ValueError("supplier needs tax_number or vat_id")
        return self


class Position(BaseModel):
    """One line of a quote."""

    description: str = Field(min_length=1)
    quantity: Decimal = Decimal(1)
    unit_net: Decimal

    @property
    def net(self) -> Decimal:
        """Quantity times unit price, rounded to cents."""
        return _cents(self.quantity * self.unit_net)


class Totals(BaseModel):
    """Net, VAT and gross of a priced document."""

    net: Decimal
    vat_rate: Decimal
    vat: Decimal
    gross: Decimal


class Package(BaseModel):
    """One service package of the service description or contract."""

    name: str = Field(min_length=1)
    items: list[str] = Field(min_length=1)
    net_price: Decimal | None = None
    recurrence: Literal["once", "monthly"]


class CommonFields(BaseModel):
    """Fields every document shows."""

    supplier: Supplier
    customer_name: str = Field(min_length=1)
    customer_street: str = Field(min_length=1)
    customer_postal_code: str = Field(min_length=1)
    customer_city: str = Field(min_length=1)
    issued_on: date
    number: str | None = None
    note: str | None = None


class PricedFields(CommonFields):
    """Documents with positions and VAT."""

    positions: list[Position] = Field(min_length=1)
    vat_rate: Decimal = DEFAULT_VAT_RATE
    small_business: bool = False

    @property
    def totals(self) -> Totals:
        """Sum of positions; small businesses charge no VAT (§ 19 UStG)."""
        net = sum((position.net for position in self.positions), Decimal(0))
        rate = Decimal(0) if self.small_business else self.vat_rate
        vat = _cents(net * rate / HUNDRED)
        return Totals(net=net, vat_rate=rate, vat=vat, gross=net + vat)


class QuoteFields(PricedFields):
    """Quote with a validity date; salutation, intro and outro replace the standard sentences."""

    valid_until: date
    salutation: str | None = None
    intro: str | None = None
    outro: str | None = None


class ServiceDescriptionFields(CommonFields):
    """Service description with its packages and the services agreed with this customer."""

    packages: list[Package] = Field(min_length=1)
    offer: str | None = None
    start_date: date | None = None
    agreed_services: list[str] = Field(default_factory=list)


class ContractFields(CommonFields):
    """Service contract."""

    start_date: date
    monthly_fee_net: Decimal = Field(gt=0)
    setup_fee_net: Decimal = Decimal(0)
    packages: list[Package] = Field(default_factory=list)
    minimum_term_months: int = Field(default=0, ge=0)
    notice_period_months: int = Field(default=DEFAULT_NOTICE_PERIOD_MONTHS, ge=1)

    @property
    def liability_cap(self) -> Decimal:
        """Liability is capped at the net fees of one year."""
        return _cents(self.monthly_fee_net * MONTHS_PER_YEAR)


class ProofOfServiceFields(CommonFields):
    """Proof of the work done in a month or another period."""

    month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    period: str | None = None
    activities: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _has_period(self) -> "ProofOfServiceFields":
        if not (self.month or self.period):
            raise ValueError("proof of service needs month or period")
        return self


def render_pdf(
    template_name: str,
    context: dict[str, object],
    output: Path,
    templates_dir: Path = DEFAULT_TEMPLATES_DIR,
) -> Path:
    """Render a Jinja2 template to PDF with WeasyPrint.

    Args:
        template_name: File in templates/documents, e.g. "quote.html".
        context: Values the template reads.
        output: Target PDF path.
        templates_dir: Root template folder holding documents/.

    Returns:
        The output path.

    Raises:
        RenderError: If the template fails or the file cannot be written.
    """
    html = render_template(templates_dir / TEMPLATE_DIR, template_name, context)
    return write_pdf(html, output)


def _today() -> date:
    return datetime.now(UTC).date()


def load_supplier(repositories: Repositories) -> Supplier:
    """Read the supplier from settings key "supplier".

    Raises:
        RenderError: If the setting is missing or lacks required keys.
    """
    value = repositories.settings_value(SUPPLIER_SETTING)
    if not isinstance(value, dict):
        raise RenderError(f"settings key {SUPPLIER_SETTING!r} is missing or not an object")
    try:
        return Supplier.model_validate(value)
    except ValidationError as exc:
        raise RenderError(f"settings key {SUPPLIER_SETTING!r} is invalid: {exc}") from exc


def _base_fields(
    data: dict[str, object],
    customer: Customer,
    repositories: Repositories,
    supplier: Supplier,
    issued_on: date,
) -> dict[str, object]:
    fields: dict[str, object] = {"customer_name": customer.name, "customer_city": customer.city}
    locations = repositories.locations(customer.id)
    if locations:
        fields["customer_street"] = locations[0].street
        fields["customer_postal_code"] = locations[0].postal_code
        fields["customer_city"] = locations[0].city
    fields["small_business"] = supplier.small_business
    fields.update(data)
    fields["supplier"] = supplier.model_dump(mode="json")
    fields["issued_on"] = issued_on
    return fields


def _parse[Model: BaseModel](model: type[Model], fields: dict[str, object], kind: str) -> Model:
    try:
        return model.model_validate(fields)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        raise RenderError(f"{kind} data invalid at {location}: {error['msg']}") from exc


class PreparedDocument(NamedTuple):
    """Validated fields of one document, ready to render."""

    fields: CommonFields
    totals: Totals | None


def _prepare(kind: DocumentKind, fields: dict[str, object]) -> PreparedDocument:
    if kind is DocumentKind.QUOTE:
        valid_until = fields.get("valid_until") or _today() + timedelta(days=QUOTE_VALIDITY_DAYS)
        quote = _parse(QuoteFields, {**fields, "valid_until": valid_until}, kind.value)
        return PreparedDocument(fields=quote, totals=quote.totals)
    parsers: dict[DocumentKind, Callable[[], CommonFields]] = {
        DocumentKind.SERVICE_DESCRIPTION: lambda: _parse(
            ServiceDescriptionFields, fields, kind.value
        ),
        DocumentKind.CONTRACT: lambda: _parse(ContractFields, fields, kind.value),
        DocumentKind.PROOF_OF_SERVICE: lambda: _parse(ProofOfServiceFields, fields, kind.value),
    }
    if kind not in parsers:
        raise RenderError(f"documents of kind {kind.value} are not built from data")
    return PreparedDocument(fields=parsers[kind](), totals=None)


def _snapshot(fields: CommonFields, totals: Totals | None) -> dict[str, object]:
    content: dict[str, Any] = fields.model_dump()
    if totals is not None:
        content["totals"] = totals.model_dump()
    snapshot: dict[str, object] = _JSON_ADAPTER.dump_python(content, mode="json")
    return snapshot


def document_file_name(kind: DocumentKind, number: str | None, issued_on: date) -> str:
    """File name <kind>_<number or date>.pdf with storage-safe characters."""
    return f"{kind.value}_{filename_slug(number) if number else issued_on.isoformat()}.pdf"


def build_document(
    kind: DocumentKind,
    customer_id: UUID,
    data: dict[str, object],
    repositories: Repositories,
    settings: Settings,
    *,
    request: Document | None = None,
) -> Document:
    """Validate data for the kind, render the PDF, upload it, insert or complete the row.

    Args:
        kind: Document kind; audit_quote has its own builder in documents/audit_quote.py.
        customer_id: Recipient.
        data: Kind-specific fields as described in the module docstring.
        repositories: Data access.
        settings: Work directory and template folder.
        request: Row the app inserted for this document; it is completed instead of inserting
            a new row, and keeps its id.

    Returns:
        The inserted or completed document row.

    Raises:
        RenderError: If data or supplier settings are incomplete or rendering fails.
        StorageError: If loading, uploading or inserting fails.
    """
    customer = repositories.customer(customer_id)
    supplier = load_supplier(repositories)
    base = _base_fields(data, customer, repositories, supplier, _today())
    prepared = _prepare(kind, base)
    fields = prepared.fields
    file_name = document_file_name(kind, fields.number, fields.issued_on)
    output = settings.radar_work_dir / DOCUMENT_DIR / filename_slug(customer.name) / file_name
    context: dict[str, object] = {"document": fields, "totals": prepared.totals}
    render_pdf(f"{kind.value}.html", context, output, settings.templates_dir)
    document_id = request.id if request is not None else uuid4()
    remote_path = repositories.upload(
        DOCUMENTS_BUCKET, f"{customer_id}/{document_id}/{file_name}", output
    )
    store = repositories.save_document if request is None else repositories.complete_document
    document = store(_document_row(document_id, customer_id, kind, prepared, remote_path))
    repositories.new_event(
        DOCUMENT_ISSUED_EVENT,
        {
            "document_id": str(document.id),
            "customer_id": str(customer_id),
            "kind": kind.value,
            "number": fields.number,
            "file_path": remote_path,
        },
    )
    logger.info("Issued %s %s for customer %s", kind.value, fields.number or "", customer_id)
    return document


def _document_row(
    document_id: UUID,
    customer_id: UUID,
    kind: DocumentKind,
    prepared: PreparedDocument,
    file_path: str,
) -> Document:
    """Row of the rendered document with amounts, data snapshot and storage path."""
    fields, totals = prepared
    return Document(
        id=document_id,
        customer_id=customer_id,
        kind=kind,
        number=fields.number,
        issued_on=fields.issued_on,
        net_amount=totals.net if totals else None,
        vat_rate=totals.vat_rate if totals else None,
        gross_amount=totals.gross if totals else None,
        data=_snapshot(fields, totals),
        file_path=file_path,
    )
