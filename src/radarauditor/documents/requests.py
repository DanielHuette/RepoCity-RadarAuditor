"""Turn document requests from the app into issued documents.

Context: The app inserts a documents row with file_path null and the form values in data (keys in
app/src/lib/documents.ts, DOCUMENT_FIELDS); the worker picks the rows up while idle, translates
the form values into the builder's fields and completes the same row. A request that fails gets
its error in data["error"] and is skipped until the error is removed. Package names and included
services come from config/pakete.yaml, prices from the form. An audit_quote request (sales report
plus CRM quote) goes to documents/audit_quote.py instead of the form translation.
Input: Requested Document rows, customer, first location, supplier settings, package catalogue.
Output: Builder data and completed Document rows.
"""

import calendar
import logging
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from radarauditor.config import Settings
from radarauditor.db.models import Customer, Document, DocumentKind, Location
from radarauditor.db.repositories import Repositories
from radarauditor.documents.audit_quote import build_audit_quote
from radarauditor.documents.builder import Supplier, build_document, load_supplier
from radarauditor.errors import ConfigurationError, RadarError, RenderError

logger = logging.getLogger(__name__)

DEFAULT_PACKAGES_PATH = Path("config/pakete.yaml")
ERROR_KEY = "error"
MONTHS_PER_QUARTER = 3
GERMAN_MONTHS = {
    "januar": 1,
    "jan": 1,
    "jänner": 1,
    "februar": 2,
    "feb": 2,
    "märz": 3,
    "maerz": 3,
    "mär": 3,
    "mrz": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "dez": 12,
}
MONTH_NAME_PATTERN = re.compile(r"^([a-zäöü]+)\.?\s+(\d{4})$")
MONTH_NUMBER_PATTERN = re.compile(r"^(\d{1,2})\s*[./-]\s*(\d{4})$")
ISO_MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{1,2})$")
QUARTER_PATTERN = re.compile(r"^(?:q\s*([1-4])|([1-4])\.\s*quartal)\s+(\d{4})$")
YEAR_PATTERN = re.compile(r"^(\d{4})$")
GERMAN_DATE_PATTERN = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
ISO_DATE_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
RANGE_SEPARATOR = re.compile(r"\s*(?:–|—|\s-\s|bis)\s*|(?<=\d)-(?=\d{1,2}\.)")
ACCEPTED_PERIODS = (
    '"Oktober 2026", "10/2026", "2026-10", "Q3 2026", "2026", "01.10.2026 - 31.10.2026" '
    'or "01.10.2026"'
)

Recurrence = Literal["once", "monthly"]


class PackageDefinition(BaseModel):
    """One package of config/pakete.yaml."""

    name: str = Field(min_length=1)
    recurrence: Recurrence
    items: list[str] = Field(min_length=1)


class PackageCatalogue(BaseModel):
    """Content of config/pakete.yaml: name formats and exactly one package per recurrence."""

    name_format: str = Field(min_length=1)
    position_format: dict[Recurrence, str]
    packages: list[PackageDefinition]

    @model_validator(mode="after")
    def _one_package_per_recurrence(self) -> "PackageCatalogue":
        recurrences = sorted(package.recurrence for package in self.packages)
        if recurrences != ["monthly", "once"]:
            raise ValueError("needs exactly one package billed once and one billed monthly")
        if set(self.position_format) != {"once", "monthly"}:
            raise ValueError("position_format needs the keys once and monthly")
        return self

    def package(self, recurrence: Recurrence) -> PackageDefinition:
        """The package billed with the given recurrence."""
        return next(package for package in self.packages if package.recurrence == recurrence)


class _OfferRequest(BaseModel):
    package: str = Field(min_length=1)


class _PricedRequest(_OfferRequest):
    setup_net: Decimal = Field(ge=0)
    monthly_net: Decimal = Field(ge=0)


class QuoteRequest(_PricedRequest):
    """Form values of a quote."""

    valid_until: date
    notes: list[str] = Field(default_factory=list)


class ServiceDescriptionRequest(_OfferRequest):
    """Form values of a service description."""

    services: list[str] = Field(min_length=1)
    start_on: date


class ContractRequest(_PricedRequest):
    """Form values of a contract."""

    start_on: date
    minimum_term_months: int = Field(ge=0)
    notice_period_months: int = Field(ge=1)


class ProofOfServiceRequest(BaseModel):
    """Form values of a proof of service."""

    service_period: str = Field(min_length=1)
    services: list[str] = Field(min_length=1)


def load_packages(path: Path = DEFAULT_PACKAGES_PATH) -> PackageCatalogue:
    """Read the package catalogue.

    Args:
        path: Location of pakete.yaml.

    Returns:
        The validated catalogue.

    Raises:
        ConfigurationError: If the file is missing or malformed.
    """
    try:
        return PackageCatalogue.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"package catalogue {path} cannot be read: {exc}") from exc


def pending_requests(repositories: Repositories) -> list[Document]:
    """Document rows the app requested and the worker has not yet issued or rejected.

    Args:
        repositories: Data access.

    Returns:
        Rows with file_path null and no recorded error, oldest first.

    Raises:
        StorageError: If the rows cannot be loaded.
    """
    return [
        document for document in repositories.pending_documents() if ERROR_KEY not in document.data
    ]


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    if not 1 <= month <= 12:
        raise ValueError(f"month {month} does not exist")
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _single_date(text: str) -> date | None:
    german = GERMAN_DATE_PATTERN.match(text)
    if german:
        return date(int(german[3]), int(german[2]), int(german[1]))
    iso = ISO_DATE_PATTERN.match(text)
    if iso:
        return date(int(iso[1]), int(iso[2]), int(iso[3]))
    return None


def _whole_period(text: str) -> tuple[date, date] | None:
    if match := MONTH_NAME_PATTERN.match(text):
        month = GERMAN_MONTHS.get(match[1])
        return None if month is None else _month_bounds(int(match[2]), month)
    if match := MONTH_NUMBER_PATTERN.match(text):
        return _month_bounds(int(match[2]), int(match[1]))
    if match := ISO_MONTH_PATTERN.match(text):
        return _month_bounds(int(match[1]), int(match[2]))
    if match := QUARTER_PATTERN.match(text):
        quarter, year = int(match[1] or match[2]), int(match[3])
        first_month = (quarter - 1) * MONTHS_PER_QUARTER + 1
        return (
            _month_bounds(year, first_month)[0],
            _month_bounds(year, first_month + MONTHS_PER_QUARTER - 1)[1],
        )
    if match := YEAR_PATTERN.match(text):
        return date(int(match[1]), 1, 1), date(int(match[1]), 12, 31)
    return None


def parse_service_period(text: str) -> tuple[date, date]:
    """Read a service period as typed in the app into first and last day.

    Args:
        text: A month ("Oktober 2026", "10/2026", "2026-10"), a quarter ("Q3 2026",
            "3. Quartal 2026"), a year, a single date or a range of two dates.

    Returns:
        First and last day of the period.

    Raises:
        RenderError: If the text matches none of the accepted forms or names an impossible date.
    """
    normalised = " ".join(text.strip().casefold().split())
    try:
        whole = _whole_period(normalised)
        if whole is not None:
            return whole
        parts = RANGE_SEPARATOR.split(normalised)
        days = [_single_date(part.strip()) for part in parts if part.strip()]
    except ValueError as exc:
        raise RenderError(f"service period {text!r} names an impossible date: {exc}") from exc
    if len(days) == 1 and days[0] is not None:
        return days[0], days[0]
    if len(days) == 2 and days[0] is not None and days[1] is not None:
        if days[1] < days[0]:
            raise RenderError(f"service period {text!r} ends before it starts")
        return days[0], days[1]
    raise RenderError(f"service period {text!r} is not readable; write it like {ACCEPTED_PERIODS}")


def _single_month(start: date, end: date) -> str | None:
    whole_month = _month_bounds(start.year, start.month)
    return start.strftime("%Y-%m") if (start, end) == whole_month else None


def _form[Model: BaseModel](model: type[Model], document: Document) -> Model:
    try:
        return model.model_validate(document.data)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        raise RenderError(
            f"{document.kind.value} request {document.id} invalid at {location}: {error['msg']}"
        ) from exc


def _address(customer: Customer, location: Location | None, kind: DocumentKind) -> dict[str, str]:
    if location is None:
        raise RenderError(
            f"customer {customer.name} has no location, so the {kind.value} has no address; "
            "add a location with street and postal code, then request the document again"
        )
    return {
        "customer_name": customer.name,
        "customer_street": location.street,
        "customer_postal_code": location.postal_code,
        "customer_city": location.city,
    }


def _fees(form: _PricedRequest) -> dict[Recurrence, Decimal]:
    return {"once": form.setup_net, "monthly": form.monthly_net}


def _quote(form: QuoteRequest, packages: PackageCatalogue) -> dict[str, object]:
    positions = [
        {
            "description": packages.position_format[recurrence].format(
                package=packages.package(recurrence).name, offer=form.package
            ),
            "quantity": "1",
            "unit_net": str(fee),
        }
        for recurrence, fee in _fees(form).items()
        if fee > 0
    ]
    if not positions:
        raise RenderError("quote needs a setup or monthly fee above zero")
    data: dict[str, object] = {
        "positions": positions,
        "valid_until": form.valid_until.isoformat(),
    }
    if form.notes:
        data["note"] = "\n".join(form.notes)
    return data


def _packages(
    packages: PackageCatalogue, offer: str, fees: dict[Recurrence, Decimal] | None
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for package in packages.packages:
        price = None if fees is None else fees[package.recurrence]
        if price is not None and price <= 0:
            continue
        rows.append(
            {
                "name": packages.name_format.format(package=package.name, offer=offer),
                "items": package.items,
                "net_price": None if price is None else str(price),
                "recurrence": package.recurrence,
            }
        )
    return rows


def _service_description(
    form: ServiceDescriptionRequest, packages: PackageCatalogue
) -> dict[str, object]:
    return {
        "packages": _packages(packages, form.package, None),
        "offer": form.package,
        "start_date": form.start_on.isoformat(),
        "agreed_services": form.services,
    }


def _contract(form: ContractRequest, packages: PackageCatalogue) -> dict[str, object]:
    return {
        "start_date": form.start_on.isoformat(),
        "monthly_fee_net": str(form.monthly_net),
        "setup_fee_net": str(form.setup_net),
        "packages": _packages(packages, form.package, _fees(form)),
        "minimum_term_months": form.minimum_term_months,
        "notice_period_months": form.notice_period_months,
    }


def _proof_of_service(form: ProofOfServiceRequest) -> dict[str, object]:
    start, end = parse_service_period(form.service_period)
    return {
        "period": form.service_period.strip(),
        "month": _single_month(start, end),
        "activities": form.services,
    }


def translate_request(
    document: Document,
    customer: Customer,
    location: Location | None,
    supplier_defaults: Supplier,
    *,
    packages: PackageCatalogue | None = None,
    packages_path: Path = DEFAULT_PACKAGES_PATH,
) -> dict[str, object]:
    """Map the form values of a requested row to the data build_document expects.

    Args:
        document: Requested row; data holds the app's form values.
        customer: Recipient.
        location: First location of the customer; its address goes on the document.
        supplier_defaults: Supplier settings; small_business carries over.
        packages: Package catalogue; read from packages_path when omitted and needed.
        packages_path: Location of pakete.yaml.

    Returns:
        Builder data with customer address, small_business and the kind-specific fields.

    Raises:
        RenderError: If the customer has no location or a form value is missing or unreadable.
        ConfigurationError: If the package catalogue is needed but cannot be read.
    """
    data: dict[str, object] = {
        **_address(customer, location, document.kind),
        "small_business": supplier_defaults.small_business,
    }
    kind = document.kind
    if kind is DocumentKind.PROOF_OF_SERVICE:
        return {**data, **_proof_of_service(_form(ProofOfServiceRequest, document))}
    catalogue = packages or load_packages(packages_path)
    if kind is DocumentKind.QUOTE:
        return {**data, **_quote(_form(QuoteRequest, document), catalogue)}
    if kind is DocumentKind.CONTRACT:
        return {**data, **_contract(_form(ContractRequest, document), catalogue)}
    form_values = _form(ServiceDescriptionRequest, document)
    return {**data, **_service_description(form_values, catalogue)}


def fulfil_request(
    document: Document,
    repositories: Repositories,
    settings: Settings,
    *,
    packages: PackageCatalogue | None = None,
) -> Document:
    """Translate a requested row and complete it through build_document, or build_audit_quote.

    Args:
        document: Requested row with file_path null.
        repositories: Data access.
        settings: Work directory, template folder and package catalogue path.
        packages: Package catalogue; read from settings.packages_path when omitted and needed.

    Returns:
        The completed row with date, amounts and file path.

    Raises:
        RenderError: If the request or the supplier settings are incomplete or rendering fails.
        StorageError: If loading, uploading or updating fails.
        ConfigurationError: If the package catalogue cannot be read.
    """
    if document.kind is DocumentKind.AUDIT_QUOTE:
        return build_audit_quote(document, repositories, settings)
    customer = repositories.customer(document.customer_id)
    locations = repositories.locations(customer.id)
    supplier = load_supplier(repositories)
    data = translate_request(
        document,
        customer,
        locations[0] if locations else None,
        supplier,
        packages=packages,
        packages_path=settings.packages_path,
    )
    return build_document(
        document.kind, document.customer_id, data, repositories, settings, request=document
    )


def _record_failure(document: Document, repositories: Repositories, message: str) -> None:
    try:
        current = repositories.document(document.id).data
        repositories.set_document_data(document.id, {**current, ERROR_KEY: message})
    except RadarError as exc:
        logger.error("Failure of document request %s could not be recorded: %s", document.id, exc)


def process_pending_requests(repositories: Repositories, settings: Settings) -> int:
    """Issue every pending document request; a failing one gets its error recorded.

    Args:
        repositories: Data access.
        settings: Work directory, template folder and package catalogue path.

    Returns:
        Number of documents issued.

    Raises:
        StorageError: If the pending requests cannot be listed.
    """
    pending = pending_requests(repositories)
    if not pending:
        return 0
    try:
        packages: PackageCatalogue | None = load_packages(settings.packages_path)
    except ConfigurationError as exc:
        logger.error("Package catalogue unavailable: %s", exc)
        packages = None
    issued = 0
    for document in pending:
        try:
            fulfil_request(document, repositories, settings, packages=packages)
        except RadarError as exc:
            logger.error("Document request %s failed: %s", document.id, exc)
            _record_failure(document, repositories, str(exc))
            continue
        issued += 1
    return issued
