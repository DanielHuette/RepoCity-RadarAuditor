"""Tests for turning app document requests into issued documents.

Context: Real templates and WeasyPrint; Supabase replaced by a mock. | Input: Fixtures. |
Output: Assertions.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from radarauditor.config import Settings
from radarauditor.db.models import Customer, Document, DocumentKind, Location
from radarauditor.documents import builder, requests
from radarauditor.documents.builder import Supplier
from radarauditor.documents.requests import (
    PackageCatalogue,
    fulfil_request,
    load_packages,
    parse_service_period,
    pending_requests,
    process_pending_requests,
    translate_request,
)
from radarauditor.errors import ConfigurationError, RenderError, StorageError
from radarauditor.rendering import render_template

CUSTOMER_ID = UUID("00000000-0000-4000-8000-000000000001")
PACKAGES_PATH = Path(__file__).resolve().parents[1] / "config" / "pakete.yaml"
QUOTE_FORM: dict[str, object] = {
    "package": "Sichtbarkeit Lokal",
    "setup_net": 490,
    "monthly_net": 149,
    "valid_until": "2026-11-15",
    "notes": ["Start im November möglich.", "Fotos liefert der Kunde."],
}
CONTRACT_FORM: dict[str, object] = {
    "package": "Sichtbarkeit Lokal",
    "setup_net": 0,
    "monthly_net": 149,
    "start_on": "2026-11-01",
    "minimum_term_months": 6,
    "notice_period_months": 3,
}


def request_row(kind: DocumentKind, data: dict[str, object]) -> Document:
    """A documents row as the app inserts it."""
    return Document(
        id=uuid4(),
        customer_id=CUSTOMER_ID,
        kind=kind,
        number=None,
        issued_on=None,
        net_amount=None,
        vat_rate=None,
        gross_amount=None,
        data=data,
        file_path=None,
    )


@pytest.fixture
def packages() -> PackageCatalogue:
    """The real package catalogue."""
    return load_packages(PACKAGES_PATH)


@pytest.fixture
def supplier(report_supplier: dict[str, object]) -> Supplier:
    """Supplier settings as a model."""
    return Supplier.model_validate(report_supplier)


@pytest.fixture
def request_repositories(report_document_repositories: MagicMock) -> MagicMock:
    """Repositories double that completes requested rows by echoing them."""
    report_document_repositories.complete_document.side_effect = lambda document: document
    return report_document_repositories


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


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("Oktober 2026", date(2026, 10, 1), date(2026, 10, 31)),
        (" februar  2028 ", date(2028, 2, 1), date(2028, 2, 29)),
        ("März 2026", date(2026, 3, 1), date(2026, 3, 31)),
        ("Okt. 2026", date(2026, 10, 1), date(2026, 10, 31)),
        ("10/2026", date(2026, 10, 1), date(2026, 10, 31)),
        ("9.2026", date(2026, 9, 1), date(2026, 9, 30)),
        ("2026-04", date(2026, 4, 1), date(2026, 4, 30)),
        ("Q3 2026", date(2026, 7, 1), date(2026, 9, 30)),
        ("4. Quartal 2026", date(2026, 10, 1), date(2026, 12, 31)),
        ("2026", date(2026, 1, 1), date(2026, 12, 31)),
        ("15.10.2026", date(2026, 10, 15), date(2026, 10, 15)),
        ("01.10.2026 - 15.10.2026", date(2026, 10, 1), date(2026, 10, 15)),
        ("01.10.2026-15.10.2026", date(2026, 10, 1), date(2026, 10, 15)),
        ("1.10.2026 bis 2.11.2026", date(2026, 10, 1), date(2026, 11, 2)),
        ("2026-10-01 – 2026-10-31", date(2026, 10, 1), date(2026, 10, 31)),
    ],
)
def test_parse_service_period_accepts_common_forms(text: str, start: date, end: date) -> None:
    assert parse_service_period(text) == (start, end)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Herbst 2026", "not readable"),
        ("Oktember 2026", "not readable"),
        ("13/2026", "impossible date"),
        ("31.02.2026", "impossible date"),
        ("31.10.2026 - 01.10.2026", "ends before it starts"),
        ("", "not readable"),
    ],
)
def test_parse_service_period_rejects_unreadable_text(text: str, problem: str) -> None:
    with pytest.raises(RenderError, match=problem):
        parse_service_period(text)


def test_load_packages_reads_both_packages(packages: PackageCatalogue) -> None:
    assert packages.package("once").name == "Erstoptimierung"
    assert packages.package("monthly").name == "Laufende Betreuung"
    assert all(package.items for package in packages.packages)


@pytest.mark.parametrize(
    "content",
    [
        "packages: [",
        "name_format: x\nposition_format: {once: a, monthly: b}\npackages: []",
        (
            "name_format: x\nposition_format: {once: a}\npackages:\n"
            "  - {name: A, recurrence: once, items: [a]}\n"
            "  - {name: B, recurrence: monthly, items: [b]}"
        ),
    ],
)
def test_load_packages_rejects_malformed_catalogue(tmp_path: Path, content: str) -> None:
    path = tmp_path / "pakete.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="package catalogue"):
        load_packages(path)


def test_load_packages_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="cannot be read"):
        load_packages(tmp_path / "fehlt.yaml")


def test_pending_requests_skip_rows_with_error(fake_repositories: MagicMock) -> None:
    open_request = request_row(DocumentKind.QUOTE, QUOTE_FORM)
    failed = request_row(DocumentKind.QUOTE, {**QUOTE_FORM, "error": "kaputt"})
    fake_repositories.pending_documents.return_value = [open_request, failed]

    assert pending_requests(fake_repositories) == [open_request]


def test_translate_quote_prices_each_package_from_the_form(
    sample_customer: Customer,
    report_location: Location,
    supplier: Supplier,
    packages: PackageCatalogue,
) -> None:
    data = translate_request(
        request_row(DocumentKind.QUOTE, QUOTE_FORM),
        sample_customer,
        report_location,
        supplier,
        packages=packages,
    )

    assert data["customer_street"] == "Hauptstraße 5"
    assert data["customer_postal_code"] == "48143"
    assert data["positions"] == [
        {
            "description": "Erstoptimierung „Sichtbarkeit Lokal“, einmalig",
            "quantity": "1",
            "unit_net": "490",
        },
        {
            "description": "Laufende Betreuung „Sichtbarkeit Lokal“, monatlich",
            "quantity": "1",
            "unit_net": "149",
        },
    ]
    assert data["valid_until"] == "2026-11-15"
    assert data["note"] == "Start im November möglich.\nFotos liefert der Kunde."


def test_translate_quote_needs_a_fee(
    sample_customer: Customer,
    report_location: Location,
    supplier: Supplier,
    packages: PackageCatalogue,
) -> None:
    form = {**QUOTE_FORM, "setup_net": 0, "monthly_net": 0}
    with pytest.raises(RenderError, match="fee above zero"):
        translate_request(
            request_row(DocumentKind.QUOTE, form),
            sample_customer,
            report_location,
            supplier,
            packages=packages,
        )


def test_translate_contract_skips_free_setup_and_keeps_terms(
    sample_customer: Customer,
    report_location: Location,
    supplier: Supplier,
    packages: PackageCatalogue,
) -> None:
    data = translate_request(
        request_row(DocumentKind.CONTRACT, CONTRACT_FORM),
        sample_customer,
        report_location,
        supplier,
        packages=packages,
    )

    assert data["monthly_fee_net"] == "149"
    assert data["setup_fee_net"] == "0"
    assert data["start_date"] == "2026-11-01"
    assert (data["minimum_term_months"], data["notice_period_months"]) == (6, 3)
    contract_packages = data["packages"]
    assert isinstance(contract_packages, list)
    assert [package["name"] for package in contract_packages] == [
        "Laufende Betreuung „Sichtbarkeit Lokal“"
    ]


def test_translate_service_description_lists_catalogue_and_agreed_services(
    sample_customer: Customer,
    report_location: Location,
    supplier: Supplier,
    packages: PackageCatalogue,
) -> None:
    form = {"package": "Basis", "services": ["Profil pflegen"], "start_on": "2026-11-01"}
    data = translate_request(
        request_row(DocumentKind.SERVICE_DESCRIPTION, form),
        sample_customer,
        report_location,
        supplier,
        packages=packages,
    )

    described = data["packages"]
    assert isinstance(described, list)
    assert [package["recurrence"] for package in described] == ["once", "monthly"]
    assert all(package["net_price"] is None for package in described)
    assert data["agreed_services"] == ["Profil pflegen"]
    assert (data["offer"], data["start_date"]) == ("Basis", "2026-11-01")


@pytest.mark.parametrize(("period", "month"), [("Oktober 2026", "2026-10"), ("Q3 2026", None)])
def test_translate_proof_of_service_keeps_the_period_text(
    sample_customer: Customer,
    report_location: Location,
    supplier: Supplier,
    period: str,
    month: str | None,
) -> None:
    form = {"service_period": period, "services": ["Profil gepflegt"]}
    data = translate_request(
        request_row(DocumentKind.PROOF_OF_SERVICE, form),
        sample_customer,
        report_location,
        supplier,
    )

    assert (data["period"], data["month"], data["activities"]) == (
        period,
        month,
        ["Profil gepflegt"],
    )


def test_translate_without_location_names_the_customer(
    sample_customer: Customer, supplier: Supplier
) -> None:
    with pytest.raises(RenderError, match="Dachdeckerei Muster has no location"):
        translate_request(
            request_row(DocumentKind.QUOTE, QUOTE_FORM), sample_customer, None, supplier
        )


def test_translate_reports_the_first_invalid_form_value(
    sample_customer: Customer, report_location: Location, supplier: Supplier
) -> None:
    form = {key: value for key, value in CONTRACT_FORM.items() if key != "monthly_net"}
    with pytest.raises(RenderError, match="contract request .* invalid at monthly_net"):
        translate_request(
            request_row(DocumentKind.CONTRACT, form), sample_customer, report_location, supplier
        )


def test_fulfil_quote_completes_the_requested_row(
    request_repositories: MagicMock, report_settings: Settings
) -> None:
    row = request_row(DocumentKind.QUOTE, QUOTE_FORM)

    document = fulfil_request(row, request_repositories, report_settings)

    request_repositories.save_document.assert_not_called()
    completed = request_repositories.complete_document.call_args.args[0]
    assert completed.id == row.id == document.id
    assert document.issued_on == datetime.now(UTC).date()
    assert document.file_path is not None
    assert document.file_path.startswith(f"{CUSTOMER_ID}/{row.id}/quote_")
    request_repositories.set_document_data.assert_not_called()


@pytest.mark.parametrize(
    ("kind", "form", "expected"),
    [
        (DocumentKind.QUOTE, QUOTE_FORM, ["Sichtbarkeit Lokal", "15.11.2026", "Fotos liefert"]),
        (
            DocumentKind.CONTRACT,
            CONTRACT_FORM,
            ["Mindestlaufzeit von 6 Monaten", "Frist von 3 Monaten", "1.788,00"],
        ),
        (
            DocumentKind.SERVICE_DESCRIPTION,
            {"package": "Basis", "services": ["Profil pflegen"], "start_on": "2026-11-01"},
            ["laut Angebot", "Für Sie vereinbart", "Profil pflegen", "01.11.2026"],
        ),
        (
            DocumentKind.PROOF_OF_SERVICE,
            {"service_period": "Q3 2026", "services": ["Profil gepflegt"]},
            ["Q3 2026", "Zeitraum", "Profil gepflegt"],
        ),
    ],
)
def test_fulfil_renders_every_requestable_kind(
    request_repositories: MagicMock,
    report_settings: Settings,
    captured_html: list[str],
    kind: DocumentKind,
    form: dict[str, object],
    expected: list[str],
) -> None:
    document = fulfil_request(request_row(kind, form), request_repositories, report_settings)

    assert document.number is None
    for text in expected:
        assert text in captured_html[0]


def test_process_records_errors_and_continues(
    request_repositories: MagicMock, report_settings: Settings
) -> None:
    broken = request_row(
        DocumentKind.PROOF_OF_SERVICE, {"service_period": "Herbst", "services": ["x"]}
    )
    good = request_row(
        DocumentKind.PROOF_OF_SERVICE, {"service_period": "Q3 2026", "services": ["x"]}
    )
    request_repositories.pending_documents.return_value = [broken, good]
    request_repositories.document.return_value = broken

    assert process_pending_requests(request_repositories, report_settings) == 1

    document_id, data = request_repositories.set_document_data.call_args.args
    assert document_id == broken.id
    assert data["services"] == ["x"]
    assert "Herbst" in str(data["error"])
    completed = request_repositories.complete_document.call_args.args[0]
    assert completed.id == good.id


def test_process_survives_an_unrecordable_failure(
    request_repositories: MagicMock, report_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    broken = request_row(DocumentKind.PROOF_OF_SERVICE, {"services": ["x"]})
    request_repositories.pending_documents.return_value = [broken]
    request_repositories.document.side_effect = StorageError("offline")

    assert process_pending_requests(request_repositories, report_settings) == 0
    assert "could not be recorded" in caplog.text


def test_process_without_requests_reads_no_catalogue(
    fake_repositories: MagicMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    load = MagicMock()
    monkeypatch.setattr(requests, "load_packages", load)

    assert process_pending_requests(fake_repositories, settings) == 0
    load.assert_not_called()


def test_process_reports_missing_catalogue_on_package_documents(
    request_repositories: MagicMock,
    report_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        requests, "load_packages", MagicMock(side_effect=ConfigurationError("fehlt"))
    )
    quote = request_row(DocumentKind.QUOTE, QUOTE_FORM)
    request_repositories.pending_documents.return_value = [quote]
    request_repositories.document.return_value = quote

    assert process_pending_requests(request_repositories, report_settings) == 0
    assert request_repositories.set_document_data.call_args.args[1]["error"] == "fehlt"
