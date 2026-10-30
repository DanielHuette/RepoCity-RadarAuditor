"""Combined audit and CRM quote PDF: request contract, quote fields, rendering, completion.

Context: Supabase is a MagicMock (report fixtures); WeasyPrint renders real PDFs. | Input: Fixtures.
| Output: Assertions.
"""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from conftest import AUDIT_ID, CUSTOMER_ID
from weasyprint import HTML  # type: ignore[import-untyped]
from weasyprint.document import Document as WeasyDocument  # type: ignore[import-untyped]

from radarauditor.config import Settings
from radarauditor.db.crm import CrmCustomer, CrmQuote, CrmQuoteItem
from radarauditor.db.models import Audit, AuditStatus, Customer, Document, DocumentKind, Location
from radarauditor.documents import builder
from radarauditor.documents.audit_quote import (
    GENERIC_SALUTATION,
    build_audit_quote,
    quote_fields,
    quote_vat_rate,
    read_request,
    recipient,
    salutation,
)
from radarauditor.documents.builder import Supplier
from radarauditor.documents.requests import process_pending_requests
from radarauditor.errors import RenderError
from radarauditor.rendering import render_template
from radarauditor.reporting.report import sales_report_context

CRM_CUSTOMER_ID = UUID("00000000-0000-4000-8000-0000000000c1")
QUOTE_ID = UUID("00000000-0000-4000-8000-0000000000a1")


def crm_customer(**changes: object) -> CrmCustomer:
    values: dict[str, object] = {
        "id": CRM_CUSTOMER_ID,
        "name": "Dachdeckerei Muster GmbH",
        "contact_person": "Frau Muster",
        "street": "Hauptstraße 5",
        "postal_code": "48143",
        "city": "Münster",
    }
    return CrmCustomer.model_validate({**values, **changes})


def crm_quote(**changes: object) -> CrmQuote:
    values: dict[str, object] = {
        "id": QUOTE_ID,
        "number": "AN-2026-0012",
        "customer_id": CRM_CUSTOMER_ID,
        "status": "draft",
        "issue_date": "2026-10-23",
        "valid_until": "2026-11-06",
        "intro": "vielen Dank für Ihre Anfrage. Gern biete ich Ihnen folgende Leistungen an:",
        "outro": "Ich freue mich auf Ihre Rückmeldung.",
        "net": "1290.00",
        "vat": "245.10",
        "gross": "1535.10",
    }
    return CrmQuote.model_validate({**values, **changes})


def crm_items(rates: tuple[str, ...] = ("19", "19")) -> list[CrmQuoteItem]:
    rows = [
        ("Erstoptimierung Website", "Titel, Beschreibungen, Firmendaten", "1", "900.00"),
        ("Unternehmensprofil", None, "3", "130.00"),
    ]
    return [
        CrmQuoteItem(
            id=uuid4(),
            quote_id=QUOTE_ID,
            position=index,
            product_id=None,
            title=title,
            description=description,
            quantity=Decimal(quantity),
            unit="Stück",
            unit_price_net=Decimal(price),
            vat_rate=Decimal(rate),
        )
        for index, ((title, description, quantity, price), rate) in enumerate(
            zip(rows, rates, strict=True), start=1
        )
    ]


def request_row(data: dict[str, object] | None = None) -> Document:
    return Document(
        id=uuid4(),
        customer_id=CUSTOMER_ID,
        kind=DocumentKind.AUDIT_QUOTE,
        number=None,
        issued_on=None,
        net_amount=None,
        vat_rate=None,
        gross_amount=None,
        data=data
        if data is not None
        else {"audit_id": str(AUDIT_ID), "quote_id": str(QUOTE_ID), "quote_number": "AN-2026-0012"},
        file_path=None,
        cancels_document_id=None,
    )


@pytest.fixture
def supplier(report_supplier: dict[str, object]) -> Supplier:
    return Supplier.model_validate(report_supplier)


@pytest.fixture
def quote_repositories(
    report_repositories: MagicMock,
    report_supplier: dict[str, object],
    sample_customer: Customer,
    sample_audit: Audit,
) -> MagicMock:
    """Report data plus supplier, an analysed audit and the CRM quote."""
    report_repositories.settings_value.side_effect = lambda key: (
        report_supplier if key == "supplier" else None
    )
    report_repositories.audit.return_value = sample_audit.model_copy(
        update={"status": AuditStatus.ANALYSED}
    )
    report_repositories.customer.return_value = sample_customer.model_copy(
        update={"crm_customer_id": CRM_CUSTOMER_ID}
    )
    report_repositories.crm_quote.return_value = crm_quote()
    report_repositories.crm_quote_items.return_value = crm_items()
    report_repositories.crm_customer.return_value = crm_customer()
    report_repositories.complete_document.side_effect = lambda document: document
    return report_repositories


def test_read_request_takes_the_app_contract() -> None:
    request = read_request(request_row())

    assert (request.audit_id, request.quote_id) == (AUDIT_ID, QUOTE_ID)


@pytest.mark.parametrize("missing", ["audit_id", "quote_id"])
def test_read_request_rejects_a_row_without_ids(missing: str) -> None:
    data: dict[str, object] = {"audit_id": str(AUDIT_ID), "quote_id": str(QUOTE_ID)}
    del data[missing]

    with pytest.raises(RenderError, match="needs audit_id and quote_id"):
        read_request(request_row(data))


def test_salutation_names_the_contact_or_stays_generic() -> None:
    assert salutation(crm_customer()) == "Guten Tag Frau Muster,"
    assert salutation(crm_customer(contact_person="  ")) == GENERIC_SALUTATION


def test_recipient_fills_gaps_from_the_audited_customer(
    sample_customer: Customer, report_location: Location
) -> None:
    fields = recipient(crm_customer(street=None, postal_code=""), sample_customer, report_location)

    assert fields["customer_street"] == "Hauptstraße 5"
    assert fields["customer_postal_code"] == "48143"
    assert fields["customer_name"] == "Dachdeckerei Muster GmbH"


def test_recipient_without_any_address_names_what_is_missing(sample_customer: Customer) -> None:
    with pytest.raises(RenderError, match="lacks street, postal_code"):
        recipient(crm_customer(street=None, postal_code=None), sample_customer, None)


def test_vat_rate_is_the_single_rate_or_zero() -> None:
    assert quote_vat_rate(crm_quote(), crm_items()) == Decimal(19)
    assert quote_vat_rate(crm_quote(vat="0", gross="1290"), crm_items(("0", "0"))) == 0
    with pytest.raises(RenderError, match="mixes VAT rates"):
        quote_vat_rate(crm_quote(), crm_items(("19", "7")))


def test_quote_fields_use_the_crm_totals_and_texts(
    sample_customer: Customer, report_location: Location, supplier: Supplier
) -> None:
    fields, totals = quote_fields(
        crm_quote(), crm_items(), crm_customer(), sample_customer, report_location, supplier
    )

    assert fields.number == "AN-2026-0012"
    assert fields.positions[0].description == (
        "Erstoptimierung Website – Titel, Beschreibungen, Firmendaten"
    )
    assert fields.positions[1].net == Decimal("390.00")
    assert (totals.net, totals.vat, totals.gross) == (
        Decimal("1290.00"),
        Decimal("245.10"),
        Decimal("1535.10"),
    )
    assert fields.intro is not None and fields.intro.startswith("vielen Dank")
    assert fields.salutation == "Guten Tag Frau Muster,"


def test_quote_without_positions_or_validity_is_rejected(
    sample_customer: Customer, report_location: Location, supplier: Supplier
) -> None:
    with pytest.raises(RenderError, match="has no positions"):
        quote_fields(crm_quote(), [], crm_customer(), sample_customer, report_location, supplier)
    with pytest.raises(RenderError, match="no validity date"):
        quote_fields(
            crm_quote(valid_until=None),
            crm_items(),
            crm_customer(),
            sample_customer,
            report_location,
            supplier,
        )


def test_build_renders_report_and_quote_into_one_pdf(
    quote_repositories: MagicMock,
    report_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The PDF holds every report page and the one-page quote, in that order of rendering."""
    uploaded: dict[str, bytes] = {}

    def upload(bucket: str, remote_path: str, local_file: Path) -> str:
        uploaded[f"{bucket}/{remote_path}"] = local_file.read_bytes()
        return remote_path

    quote_repositories.upload.side_effect = upload
    written_pages: list[int] = []
    original_write = WeasyDocument.write_pdf

    def counting_write(self: WeasyDocument, *args: object, **kwargs: object) -> object:
        written_pages.append(len(self.pages))
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(WeasyDocument, "write_pdf", counting_write)
    report_only = HTML(
        string=render_template(
            report_settings.templates_dir / "report",
            "sales.html",
            {
                "report": sales_report_context(
                    AUDIT_ID, quote_repositories, report_settings, tmp_path
                )
            },
        )
    ).render()
    row = request_row()

    document = build_audit_quote(row, quote_repositories, report_settings)

    assert document.id == row.id
    assert document.number is None
    expected_path = f"{CUSTOMER_ID}/{row.id}/audit_quote_AN-2026-0012.pdf"
    assert document.file_path == expected_path
    pdf = uploaded[f"documents/{expected_path}"]
    assert pdf.startswith(b"%PDF")
    assert written_pages == [len(report_only.pages) + 1]
    assert document.gross_amount == Decimal("1535.10")
    assert document.data["quote_id"] == str(QUOTE_ID)
    assert document.data["audit_id"] == str(AUDIT_ID)
    assert document.data["quote_number"] == "AN-2026-0012"
    event = quote_repositories.new_event.call_args
    assert event.args[0] == builder.DOCUMENT_ISSUED_EVENT
    assert event.args[1]["kind"] == "audit_quote"


def test_quote_html_carries_number_salutation_intro_and_outro(
    quote_repositories: MagicMock,
    report_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from radarauditor.documents import audit_quote

    captured: list[list[str]] = []

    def capture(htmls: list[str], output: Path) -> Path:
        captured.append(list(htmls))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-fake")
        return output

    monkeypatch.setattr(audit_quote, "write_combined_pdf", capture)

    build_audit_quote(request_row(), quote_repositories, report_settings)

    report_html, quote_html = captured[0]
    assert "Dachdeckerei Muster" in report_html
    assert "AN-2026-0012" in quote_html
    assert "Guten Tag Frau Muster," in quote_html
    assert "vielen Dank für Ihre Anfrage" in quote_html
    assert "Ich freue mich auf Ihre Rückmeldung." in quote_html
    assert "06.11.2026" in quote_html


def test_unanalysed_audit_or_foreign_quote_is_rejected(
    quote_repositories: MagicMock, report_settings: Settings, sample_audit: Audit
) -> None:
    quote_repositories.audit.return_value = sample_audit
    with pytest.raises(RenderError, match="once it is analysed"):
        build_audit_quote(request_row(), quote_repositories, report_settings)

    quote_repositories.audit.return_value = sample_audit.model_copy(
        update={"status": AuditStatus.REPORTED}
    )
    quote_repositories.crm_quote.return_value = crm_quote(customer_id=uuid4())
    with pytest.raises(RenderError, match="another CRM customer"):
        build_audit_quote(request_row(), quote_repositories, report_settings)


def test_pending_audit_quote_failure_is_recorded_on_the_row(
    quote_repositories: MagicMock, report_settings: Settings
) -> None:
    row = request_row({"audit_id": str(AUDIT_ID)})
    quote_repositories.pending_documents.return_value = [row]
    quote_repositories.document.return_value = row

    issued = process_pending_requests(quote_repositories, report_settings)

    assert issued == 0
    recorded = quote_repositories.set_document_data.call_args.args[1]
    assert recorded["audit_id"] == str(AUDIT_ID)
    assert "needs audit_id and quote_id" in recorded["error"]
