"""Tests for documents.

Context: Real template rendering with WeasyPrint; Supabase replaced by a mock. | Input: Fixtures. |
Output: Assertions.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from radarauditor.config import Settings
from radarauditor.db.models import DocumentKind
from radarauditor.documents import builder
from radarauditor.errors import RenderError
from radarauditor.rendering import render_template

CUSTOMER_ID = UUID("00000000-0000-4000-8000-000000000001")
SMALL_BUSINESS_SENTENCE = "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."


@pytest.fixture
def captured_html(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every HTML text the builder renders, in order."""
    pages: list[str] = []

    def capture(directory: Path, template_name: str, context: Any) -> str:
        html = render_template(directory, template_name, context)
        pages.append(html)
        return html

    monkeypatch.setattr(builder, "render_template", capture)
    return pages


def test_quote_is_rendered_uploaded_saved_and_announced(
    report_document_repositories: MagicMock,
    report_settings: Settings,
    report_quote_data: dict[str, object],
    captured_html: list[str],
) -> None:
    """Amounts as Decimal, PDF in dokumente/<customer>/, letterhead with tax and bank, event out."""
    document = builder.build_document(
        DocumentKind.QUOTE,
        CUSTOMER_ID,
        report_quote_data,
        report_document_repositories,
        report_settings,
    )

    file_name = f"quote_{datetime.now(UTC).date().isoformat()}.pdf"
    assert document.net_amount == Decimal("481.00")
    assert document.vat_rate == Decimal(19)
    assert document.gross_amount == Decimal("572.39")
    pdf = report_settings.radar_work_dir / "dokumente" / "Dachdeckerei_Muster" / file_name
    assert pdf.read_bytes().startswith(b"%PDF")
    bucket, remote_path, local_file = report_document_repositories.upload.call_args.args
    assert (bucket, local_file) == ("documents", pdf)
    assert remote_path == f"{CUSTOMER_ID}/{document.id}/{file_name}"
    assert document.file_path == remote_path
    report_document_repositories.save_document.assert_called_once()
    kind, payload = report_document_repositories.new_event.call_args.args
    assert (kind, payload["kind"]) == ("document.issued", "quote")
    html = captured_html[0]
    for text in ("Steuernummer: 337/5000/1234", "Umsatzsteuer 19 %", "91,39", "572,39"):
        assert text in html
    assert "IBAN: DE02120300000000202051" in html
    assert SMALL_BUSINESS_SENTENCE not in html


def test_quote_snapshot_is_json(
    report_document_repositories: MagicMock,
    report_settings: Settings,
    report_quote_data: dict[str, object],
) -> None:
    """The stored data carry the address taken from the location and the totals."""
    document = builder.build_document(
        DocumentKind.QUOTE,
        CUSTOMER_ID,
        report_quote_data,
        report_document_repositories,
        report_settings,
    )

    assert document.data["customer_street"] == "Hauptstraße 5"
    assert document.data["totals"] == {
        "net": "481.00",
        "vat_rate": "19",
        "vat": "91.39",
        "gross": "572.39",
    }


def test_small_business_quote_has_no_vat(
    report_document_repositories: MagicMock,
    report_settings: Settings,
    report_quote_data: dict[str, object],
    captured_html: list[str],
) -> None:
    """data["small_business"] drops the VAT and prints the § 19 sentence."""
    data = {**report_quote_data, "small_business": True}

    document = builder.build_document(
        DocumentKind.QUOTE, CUSTOMER_ID, data, report_document_repositories, report_settings
    )

    assert document.vat_rate == Decimal(0)
    assert document.gross_amount == document.net_amount == Decimal("481.00")
    assert SMALL_BUSINESS_SENTENCE in captured_html[0]
    assert "Umsatzsteuer 19 %" not in captured_html[0]


def test_missing_supplier_setting(
    report_document_repositories: MagicMock,
    report_settings: Settings,
    report_quote_data: dict[str, object],
) -> None:
    """Without settings key supplier no document can be issued."""
    report_document_repositories.settings_value.side_effect = None
    report_document_repositories.settings_value.return_value = None

    with pytest.raises(RenderError, match="supplier"):
        builder.build_document(
            DocumentKind.QUOTE,
            CUSTOMER_ID,
            report_quote_data,
            report_document_repositories,
            report_settings,
        )


def test_audit_quotes_are_not_built_from_data(
    report_document_repositories: MagicMock,
    report_settings: Settings,
    report_quote_data: dict[str, object],
) -> None:
    """The audit with quote has its own builder; build_document refuses it with a clear error."""
    with pytest.raises(RenderError, match="audit_quote are not built from data"):
        builder.build_document(
            DocumentKind.AUDIT_QUOTE,
            CUSTOMER_ID,
            report_quote_data,
            report_document_repositories,
            report_settings,
        )

    report_document_repositories.save_document.assert_not_called()


def test_supplier_without_tax_id(
    report_document_repositories: MagicMock, report_supplier: dict[str, object]
) -> None:
    """Tax number or VAT id is mandatory."""
    report_document_repositories.settings_value.side_effect = lambda key: {
        **report_supplier,
        "tax_number": None,
    }

    with pytest.raises(RenderError, match="tax_number or vat_id"):
        builder.load_supplier(report_document_repositories)


def test_quote_is_valid_for_fourteen_days(
    report_document_repositories: MagicMock,
    report_settings: Settings,
    report_quote_data: dict[str, object],
    captured_html: list[str],
) -> None:
    """Quotes are not numbered, carry amounts and an acceptance line."""
    quote = builder.build_document(
        DocumentKind.QUOTE,
        CUSTOMER_ID,
        report_quote_data,
        report_document_repositories,
        report_settings,
    )

    assert quote.number is None
    assert quote.gross_amount == Decimal("572.39")
    valid_until = datetime.now(UTC).date() + timedelta(days=14)
    assert quote.data["valid_until"] == valid_until.isoformat()
    assert quote.file_path is not None
    assert quote.file_path.endswith(f"quote_{datetime.now(UTC).date().isoformat()}.pdf")
    assert "Angenommen" in captured_html[0]
    assert "Gesamtbetrag brutto" in captured_html[0]


PACKAGES = [
    {
        "name": "Erstoptimierung",
        "items": ["Google-Profil vervollständigen", "Strukturierte Daten einbauen"],
        "net_price": "1490.00",
        "recurrence": "once",
    },
    {
        "name": "Laufende Betreuung",
        "items": ["Monatliches Audit", "Zwei Profil-Beiträge"],
        "net_price": "390.00",
        "recurrence": "monthly",
    },
]


def test_service_description_lists_packages(
    report_document_repositories: MagicMock, report_settings: Settings, captured_html: list[str]
) -> None:
    """Each package becomes a table row with price and billing."""
    document = builder.build_document(
        DocumentKind.SERVICE_DESCRIPTION,
        CUSTOMER_ID,
        {"packages": PACKAGES},
        report_document_repositories,
        report_settings,
    )

    assert document.net_amount is None
    for text in ("Erstoptimierung", "Laufende Betreuung", "1.490,00", "einmalig", "monatlich"):
        assert text in captured_html[0]


def test_contract_caps_liability_at_annual_fee(
    report_document_repositories: MagicMock, report_settings: Settings, captured_html: list[str]
) -> None:
    """Twelve monthly fees, one month notice, Art. 28 DSGVO, signatures."""
    data = {
        "start_date": "2026-11-01",
        "monthly_fee_net": "390.00",
        "setup_fee_net": "1490.00",
        "packages": PACKAGES,
    }

    builder.build_document(
        DocumentKind.CONTRACT, CUSTOMER_ID, data, report_document_repositories, report_settings
    )

    html = captured_html[0]
    for text in (
        "4.680,00",
        "einem Monat",
        "Art. 28 DSGVO",
        "Schriftform",
        "Unterschrift Auftraggeber",
    ):
        assert text in html


def test_contract_template_demands_legal_review(report_settings: Settings) -> None:
    """The header comment tells the operator to have the wording reviewed."""
    header = (report_settings.templates_dir / "documents" / "contract.html").read_text(
        encoding="utf-8"
    )

    assert header.startswith("{#")
    assert "lawyer must review" in header.splitlines()[0]


def test_proof_of_service_lists_activities(
    report_document_repositories: MagicMock, report_settings: Settings, captured_html: list[str]
) -> None:
    """Month and activities appear in the PDF."""
    data = {
        "month": "2026-10",
        "activities": ["Profil-Beitrag veröffentlicht", "Bewertungen beantwortet"],
    }

    builder.build_document(
        DocumentKind.PROOF_OF_SERVICE,
        CUSTOMER_ID,
        data,
        report_document_repositories,
        report_settings,
    )

    assert "10/2026" in captured_html[0]
    assert "Bewertungen beantwortet" in captured_html[0]


def test_proof_of_service_rejects_bad_month(
    report_document_repositories: MagicMock, report_settings: Settings
) -> None:
    """The month must be YYYY-MM."""
    with pytest.raises(RenderError, match="month"):
        builder.build_document(
            DocumentKind.PROOF_OF_SERVICE,
            CUSTOMER_ID,
            {"month": "Oktober", "activities": ["x"]},
            report_document_repositories,
            report_settings,
        )


def test_render_pdf_rejects_unknown_template(report_settings: Settings, tmp_path: Path) -> None:
    """A missing template is a render error."""
    with pytest.raises(RenderError, match="fehlt.html"):
        builder.render_pdf("fehlt.html", {}, tmp_path / "x.pdf", report_settings.templates_dir)


def test_document_file_name_is_storage_safe() -> None:
    """Number or date, never spaces or umlauts."""
    issued = datetime(2026, 10, 16, tzinfo=UTC).date()

    assert builder.document_file_name(DocumentKind.AUDIT_QUOTE, "AN-2026/0001", issued) == (
        "audit_quote_AN-2026_0001.pdf"
    )
    assert builder.document_file_name(DocumentKind.QUOTE, None, issued) == "quote_2026-10-16.pdf"
