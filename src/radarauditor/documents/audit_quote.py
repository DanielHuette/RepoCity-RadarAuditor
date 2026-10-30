"""Render the sales report of an audit and the linked CRM quote into one PDF.

Context: The app (app/src/lib/offerDocs.ts) inserts a documents row of kind audit_quote with
file_path null and data {audit_id, quote_id, quote_number}; the worker completes that row while
idle, like every other document request, and keeps those three keys in the data snapshot. The report
part is the sales report (templates/report/sales.html), the quote part the quote template
(templates/documents/quote.html) filled from crm.quotes and crm.quote_items: positions, the totals
the CRM computed, validity, intro and outro. The row keeps no number, because documents.number is
unique and the same quote may be sent more than once; the quote number is in the data snapshot and
on the PDF.
Input: Requested audit_quote Document row.
Output: The completed row with file path in bucket documents, event document.issued.
"""

import logging
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

from pydantic import BaseModel, TypeAdapter, ValidationError

from radarauditor.config import Settings
from radarauditor.db.crm import CrmCustomer, CrmQuote, CrmQuoteItem
from radarauditor.db.models import AuditStatus, Customer, Document, DocumentKind, Location
from radarauditor.db.repositories import Repositories
from radarauditor.documents.builder import (
    DOCUMENT_DIR,
    DOCUMENT_ISSUED_EVENT,
    DOCUMENTS_BUCKET,
    TEMPLATE_DIR,
    QuoteFields,
    Supplier,
    Totals,
    document_file_name,
    load_supplier,
)
from radarauditor.errors import RenderError
from radarauditor.rendering import filename_slug, render_template, write_combined_pdf
from radarauditor.reporting.report import (
    REPORT_TEMPLATE_DIR,
    SALES_TEMPLATE,
    sales_report_context,
)

logger = logging.getLogger(__name__)

QUOTE_TEMPLATE = "quote.html"
REPORTABLE_STATUSES = frozenset({AuditStatus.ANALYSED, AuditStatus.REPORTED})
GENERIC_SALUTATION = "Sehr geehrte Damen und Herren,"
POSITION_SEPARATOR = " – "

_JSON_ADAPTER: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])


def _today() -> date:
    return datetime.now(UTC).date()


def salutation(customer: CrmCustomer) -> str:
    """Opening line before the CRM intro, which starts in lower case ("vielen Dank …").

    Args:
        customer: CRM customer the quote is addressed to.

    Returns:
        "Guten Tag <contact person>," or the generic German salutation.
    """
    contact = (customer.contact_person or "").strip()
    return f"Guten Tag {contact}," if contact else GENERIC_SALUTATION


def _first_filled(*values: str | None) -> str:
    return next((value.strip() for value in values if value and value.strip()), "")


def recipient(
    crm_customer: CrmCustomer, customer: Customer, location: Location | None
) -> dict[str, str]:
    """Name and address of the quote: the CRM customer, gaps filled from the audited customer.

    Raises:
        RenderError: If street, postal code or city are unknown in both places.
    """
    street = location.street if location else None
    postal_code = location.postal_code if location else None
    city = location.city if location else None
    fields = {
        "customer_name": _first_filled(crm_customer.name, customer.name),
        "customer_street": _first_filled(crm_customer.street, street),
        "customer_postal_code": _first_filled(crm_customer.postal_code, postal_code),
        "customer_city": _first_filled(crm_customer.city, city, customer.city),
    }
    missing = [key.removeprefix("customer_") for key, value in fields.items() if not value]
    if missing:
        raise RenderError(
            f"quote recipient {fields['customer_name'] or crm_customer.id} lacks "
            f"{', '.join(missing)}; complete the address in the CRM, then request the PDF again"
        )
    return fields


def _position(item: CrmQuoteItem) -> dict[str, object]:
    detail = (item.description or "").strip()
    description = f"{item.title}{POSITION_SEPARATOR}{detail}" if detail else item.title
    return {"description": description, "quantity": item.quantity, "unit_net": item.unit_price_net}


def quote_vat_rate(quote: CrmQuote, items: list[CrmQuoteItem]) -> Decimal:
    """The single VAT rate of the quote; zero when the CRM charged no VAT.

    Raises:
        RenderError: If the positions carry different rates, which the quote layout cannot show.
    """
    if quote.vat == 0:
        return Decimal(0)
    rates = {item.vat_rate for item in items}
    if len(rates) != 1:
        listed = ", ".join(str(rate) for rate in sorted(rates))
        raise RenderError(
            f"quote {quote.number} mixes VAT rates ({listed}); one rate per quote is supported"
        )
    return rates.pop()


def quote_fields(
    quote: CrmQuote,
    items: list[CrmQuoteItem],
    crm_customer: CrmCustomer,
    customer: Customer,
    location: Location | None,
    supplier: Supplier,
) -> tuple[QuoteFields, Totals]:
    """Fill the quote template's fields and totals from the CRM quote.

    The totals are the ones the CRM stored, so the PDF shows exactly what the CRM computed.

    Raises:
        RenderError: If the quote has no positions, no validity, mixed VAT rates or no address.
    """
    if not items:
        raise RenderError(f"quote {quote.number} has no positions")
    issued_on = quote.issue_date or _today()
    if quote.valid_until is None:
        raise RenderError(f"quote {quote.number} has no validity date")
    rate = quote_vat_rate(quote, items)
    fields = QuoteFields.model_validate(
        {
            **recipient(crm_customer, customer, location),
            "supplier": supplier.model_dump(mode="json"),
            "issued_on": issued_on,
            "number": quote.number,
            "positions": [_position(item) for item in items],
            "vat_rate": rate,
            "small_business": supplier.small_business and quote.vat == 0,
            "valid_until": quote.valid_until,
            "salutation": salutation(crm_customer),
            "intro": quote.intro,
            "outro": quote.outro,
        }
    )
    totals = Totals(net=quote.net, vat_rate=rate, vat=quote.vat, gross=quote.gross)
    return fields, totals


class _Sources(NamedTuple):
    audit_id: UUID
    customer: Customer
    quote: CrmQuote
    items: list[CrmQuoteItem]
    crm_customer: CrmCustomer


class AuditQuoteRequest(BaseModel):
    """Values the app stores in data of an audit_quote request."""

    audit_id: UUID
    quote_id: UUID
    quote_number: str | None = None


def read_request(document: Document) -> AuditQuoteRequest:
    """Read audit and CRM quote id from the request row.

    Raises:
        RenderError: If either id is missing or not a UUID.
    """
    try:
        return AuditQuoteRequest.model_validate(document.data)
    except ValidationError as exc:
        raise RenderError(
            f"audit_quote request {document.id} needs audit_id and quote_id in data; "
            "request the PDF again from the quote page"
        ) from exc


def _load_sources(document: Document, repositories: Repositories) -> _Sources:
    request = read_request(document)
    audit = repositories.audit(request.audit_id)
    if audit.customer_id != document.customer_id:
        raise RenderError(f"audit {audit.id} belongs to another customer than the request")
    if audit.status not in REPORTABLE_STATUSES:
        raise RenderError(
            f"audit {audit.id} is {audit.status.value}; the PDF can be made once it is analysed"
        )
    quote = repositories.crm_quote(request.quote_id)
    customer = repositories.customer(document.customer_id)
    if customer.crm_customer_id is not None and quote.customer_id != customer.crm_customer_id:
        raise RenderError(f"quote {quote.number} belongs to another CRM customer")
    return _Sources(
        audit_id=audit.id,
        customer=customer,
        quote=quote,
        items=repositories.crm_quote_items(quote.id),
        crm_customer=repositories.crm_customer(quote.customer_id),
    )


def build_audit_quote(
    document: Document, repositories: Repositories, settings: Settings
) -> Document:
    """Render report and quote into one PDF, upload it and complete the requested row.

    Args:
        document: Requested audit_quote row whose data names audit_id and quote_id, with no file.
        repositories: Data access to public and crm.
        settings: Work directory, catalogue, forecast curves and templates.

    Returns:
        The completed row with file path, date, amounts and data snapshot.

    Raises:
        RenderError: If ids, audit state, quote, supplier settings or rendering are not usable.
        StorageError: If loading, uploading or updating fails.
        ConfigurationError: If catalogue or forecast curves are malformed.
    """
    audit_id, customer, quote, items, crm_customer = _load_sources(document, repositories)
    locations = repositories.locations(customer.id)
    supplier = load_supplier(repositories)
    fields, totals = quote_fields(
        quote, items, crm_customer, customer, locations[0] if locations else None, supplier
    )
    file_name = document_file_name(DocumentKind.AUDIT_QUOTE, quote.number, fields.issued_on)
    output = settings.radar_work_dir / DOCUMENT_DIR / filename_slug(customer.name) / file_name
    _write_pdf(audit_id, fields, totals, repositories, settings, output)
    remote_path = repositories.upload(
        DOCUMENTS_BUCKET, f"{customer.id}/{document.id}/{file_name}", output
    )
    completed = repositories.complete_document(
        _completed_row(document, audit_id, quote, fields, totals, remote_path)
    )
    repositories.new_event(
        DOCUMENT_ISSUED_EVENT,
        _issued_payload(completed.id, customer.id, audit_id, quote, remote_path),
    )
    logger.info("Issued audit and quote %s for customer %s", quote.number, customer.id)
    return completed


def _write_pdf(
    audit_id: UUID,
    fields: QuoteFields,
    totals: Totals,
    repositories: Repositories,
    settings: Settings,
    output: Path,
) -> None:
    """Render the sales report and the quote and write both into one PDF."""
    with tempfile.TemporaryDirectory(prefix="radar-audit-quote-") as scratch:
        report = sales_report_context(audit_id, repositories, settings, Path(scratch))
        report_html = render_template(
            settings.templates_dir / REPORT_TEMPLATE_DIR, SALES_TEMPLATE, {"report": report}
        )
        quote_html = render_template(
            settings.templates_dir / TEMPLATE_DIR,
            QUOTE_TEMPLATE,
            {"document": fields, "totals": totals},
        )
        write_combined_pdf([report_html, quote_html], output)


def _completed_row(
    document: Document,
    audit_id: UUID,
    quote: CrmQuote,
    fields: QuoteFields,
    totals: Totals,
    file_path: str,
) -> Document:
    """The requested row with date, amounts, data snapshot and file path filled in."""
    snapshot: dict[str, object] = _JSON_ADAPTER.dump_python(
        {
            "audit_id": audit_id,
            "quote_id": quote.id,
            "quote_number": quote.number,
            "quote": fields.model_dump(),
            "totals": totals.model_dump(),
        },
        mode="json",
    )
    return document.model_copy(
        update={
            "issued_on": fields.issued_on,
            "net_amount": totals.net,
            "vat_rate": totals.vat_rate,
            "gross_amount": totals.gross,
            "data": snapshot,
            "file_path": file_path,
        }
    )


def _issued_payload(
    document_id: UUID, customer_id: UUID, audit_id: UUID, quote: CrmQuote, file_path: str
) -> dict[str, object]:
    """Payload of the document.issued event for an audit and quote PDF."""
    return {
        "document_id": str(document_id),
        "customer_id": str(customer_id),
        "kind": DocumentKind.AUDIT_QUOTE.value,
        "number": quote.number,
        "audit_id": str(audit_id),
        "quote_id": str(quote.id),
        "file_path": file_path,
    }
