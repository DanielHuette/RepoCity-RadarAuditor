"""Tests for the command line.

Context: Typer CliRunner with fake repositories; modules built elsewhere are monkeypatched.
| Input: Fixtures. | Output: Assertions.
"""

import json
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import respx
from typer.testing import CliRunner

from radarauditor import cli
from radarauditor.collectors import google_auth
from radarauditor.db.models import Audit, AuditKind, Customer, Document, DocumentKind, JobKind
from radarauditor.errors import AuthorizationError, HandoffError, StorageError

runner = CliRunner()


@pytest.fixture
def wired(
    environment: Path, fake_repositories: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> MagicMock:
    """Make every command use the fake repositories with dummy settings."""
    monkeypatch.setattr(cli, "Repositories", MagicMock(return_value=fake_repositories))
    return fake_repositories


def test_health_ok(wired: MagicMock) -> None:
    """Valid settings and a reachable database print ok."""
    result = runner.invoke(cli.app, ["health"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"
    wired.ping.assert_called_once_with()


def test_health_reports_database_error(wired: MagicMock) -> None:
    """A failing read prints the error and exits 1."""
    wired.ping.side_effect = StorageError("health check failed: invalid key")

    result = runner.invoke(cli.app, ["health"])

    assert result.exit_code == 1
    assert "invalid key" in result.stderr


def test_health_reports_missing_settings(wired: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing variables are named and the exit code is 1."""
    monkeypatch.delenv("SUPABASE_URL")

    result = runner.invoke(cli.app, ["health"])

    assert result.exit_code == 1
    assert "SUPABASE_URL" in result.stderr
    wired.ping.assert_not_called()


@pytest.mark.parametrize(
    ("flags", "kind"), [([], AuditKind.REMOTE), (["--with-access"], AuditKind.WITH_ACCESS)]
)
def test_audit_run_creates_audit_and_collect_job(
    wired: MagicMock,
    sample_customer: Customer,
    sample_audit: Audit,
    flags: list[str],
    kind: AuditKind,
) -> None:
    """The audit id is printed after the collect job is queued."""
    wired.create_audit.return_value = sample_audit

    result = runner.invoke(cli.app, ["audit", "run", str(sample_customer.id), *flags])

    assert result.exit_code == 0
    assert result.stdout.strip() == str(sample_audit.id)
    wired.create_audit.assert_called_once_with(sample_customer.id, kind)
    wired.enqueue.assert_called_once_with(JobKind.COLLECT, sample_customer.id, sample_audit.id)


def test_audit_run_rejects_bad_id(wired: MagicMock) -> None:
    """A malformed customer id is a usage error."""
    result = runner.invoke(cli.app, ["audit", "run", "nicht-gueltig"])

    assert result.exit_code == 2
    wired.create_audit.assert_not_called()


def test_audit_run_unknown_customer(wired: MagicMock) -> None:
    """An unknown customer exits 1 with the storage error."""
    wired.customer.side_effect = StorageError("load customer failed: no row")

    result = runner.invoke(cli.app, ["audit", "run", str(uuid4())])

    assert result.exit_code == 1
    assert "no row" in result.stderr


def test_jobs_pull_prints_folders(
    wired: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every prepared folder is printed."""
    folder = tmp_path / "work" / "analyse" / "job"
    monkeypatch.setattr("radarauditor.jobs.handoff.pull", MagicMock(return_value=[folder]))

    result = runner.invoke(cli.app, ["jobs", "pull"])

    assert result.exit_code == 0
    assert result.stdout.strip() == str(folder)


def test_jobs_push_reports_handoff_errors(
    wired: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid hand-off folders exit 1 with the reason."""
    failure = MagicMock(side_effect=HandoffError("findings.json: unknown check id"))
    monkeypatch.setattr("radarauditor.jobs.handoff.push", failure)

    result = runner.invoke(cli.app, ["jobs", "push"])

    assert result.exit_code == 1
    assert "unknown check id" in result.stderr


def test_jobs_push_prints_done_folders(
    wired: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pushed folders are printed."""
    done = tmp_path / "erledigt" / "job"
    monkeypatch.setattr("radarauditor.jobs.handoff.push", MagicMock(return_value=[done]))

    result = runner.invoke(cli.app, ["jobs", "push"])

    assert result.exit_code == 0
    assert result.stdout.strip() == str(done)


@pytest.mark.parametrize(
    ("command", "function"),
    [("sales", "build_sales_report"), ("monthly", "build_monthly_report")],
)
def test_report_commands_print_pdf_path(
    wired: MagicMock,
    sample_audit: Audit,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    function: str,
) -> None:
    """Report commands call the reporting module and print the PDF path."""
    pdf = tmp_path / "bericht.pdf"
    builder = MagicMock(return_value=pdf)
    monkeypatch.setattr(f"radarauditor.reporting.report.{function}", builder)

    result = runner.invoke(cli.app, ["report", command, str(sample_audit.id)])

    assert result.exit_code == 0
    assert result.stdout.strip() == str(pdf)
    assert builder.call_args.args[:2] == (sample_audit.id, wired)


def test_document_build(
    wired: MagicMock,
    sample_customer: Customer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The data file is parsed and the stored file path printed."""
    data_file = tmp_path / "angebot.json"
    data_file.write_text(json.dumps({"positionen": []}), encoding="utf-8")
    document = Document(
        id=uuid4(),
        customer_id=sample_customer.id,
        kind=DocumentKind.QUOTE,
        number=None,
        issued_on=None,
        net_amount=None,
        vat_rate=None,
        gross_amount=None,
        data={},
        file_path="documents/angebot.pdf",
        cancels_document_id=None,
    )
    builder = MagicMock(return_value=document)
    monkeypatch.setattr("radarauditor.documents.builder.build_document", builder)

    result = runner.invoke(
        cli.app, ["document", "build", "quote", str(sample_customer.id), str(data_file)]
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "documents/angebot.pdf"
    kind, customer_id, data = builder.call_args.args[:3]
    assert (kind, customer_id, data) == (DocumentKind.QUOTE, sample_customer.id, {"positionen": []})


@pytest.mark.parametrize("content", ["[1, 2]", "{kaputt"])
def test_document_build_rejects_bad_data_file(
    wired: MagicMock, sample_customer: Customer, tmp_path: Path, content: str
) -> None:
    """The data file must hold a JSON object."""
    data_file = tmp_path / "daten.json"
    data_file.write_text(content, encoding="utf-8")

    result = runner.invoke(
        cli.app, ["document", "build", "quote", str(sample_customer.id), str(data_file)]
    )

    assert result.exit_code == 2


def test_document_build_rejects_unknown_kind(wired: MagicMock, sample_customer: Customer) -> None:
    """Unknown document kinds are usage errors."""
    result = runner.invoke(
        cli.app, ["document", "build", "brief", str(sample_customer.id), "daten.json"]
    )

    assert result.exit_code == 2


@pytest.mark.parametrize("kind", ["invoice", "credit_note"])
def test_document_build_issues_no_invoices(
    wired: MagicMock, sample_customer: Customer, kind: str
) -> None:
    """Invoices and credit notes are written by the CRM."""
    result = runner.invoke(cli.app, ["document", "build", kind, str(sample_customer.id), "d.json"])

    assert result.exit_code == 2


@pytest.mark.parametrize("arguments", [["export", "tax", "2026"], ["monthly", "run"]])
def test_billing_commands_are_gone(wired: MagicMock, arguments: list[str]) -> None:
    """The tax export and the monthly run belong to the CRM."""
    result = runner.invoke(cli.app, arguments)

    assert result.exit_code == 2


def test_worker_run(wired: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker loop is started with loaded settings."""
    run_forever = AsyncMock()
    monkeypatch.setattr("radarauditor.jobs.worker.run_forever", run_forever)

    result = runner.invoke(cli.app, ["worker", "run"])

    assert result.exit_code == 0
    run_forever.assert_awaited_once()


def test_main_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry point sets up INFO logging once and runs the app."""
    basic_config = MagicMock()
    app = MagicMock()
    monkeypatch.setattr(logging, "basicConfig", basic_config)
    monkeypatch.setattr(cli, "app", app)

    cli.main()

    basic_config.assert_called_once_with(level=logging.INFO, format=cli.LOG_FORMAT)
    app.assert_called_once_with()


@respx.mock
def test_google_auth_manual_reads_the_redirect_from_stdin(
    wired: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --manual the pasted address is exchanged and the grant stored; no token printed."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    respx.post(google_auth.TOKEN_URL).respond(
        200,
        json={
            "access_token": "ya29.secret",
            "refresh_token": "1//refresh-secret",
            "scope": " ".join(google_auth.SCOPES[:2]),
        },
    )

    result = runner.invoke(
        cli.app,
        ["google", "auth", "--manual", "--port", "9000"],
        input="http://localhost:9000/?code=4/abc\n",
    )

    assert result.exit_code == 0, result.output
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A9000" in result.stdout
    assert "stored Google authorisation for 2 scopes" in result.stdout
    assert f"not granted: {google_auth.SCOPES[2]}" in result.stderr
    assert "refresh-secret" not in result.output
    assert wired.set_setting.call_args.args[0] == "google_oauth"


def test_google_auth_without_client_fails(wired: MagicMock) -> None:
    """Missing OAuth client settings are reported with exit code 1."""
    result = runner.invoke(cli.app, ["google", "auth", "--manual"])

    assert result.exit_code == 1
    assert "GOOGLE_OAUTH_CLIENT_ID" in result.stderr
    wired.set_setting.assert_not_called()


def test_google_auth_passes_the_options(wired: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --manual the loopback flow runs on the given port."""
    calls: list[dict[str, object]] = []

    def authorise(settings: object, repositories: object, **options: object) -> object:
        calls.append(options)
        raise AuthorizationError("Google refused the authorisation: access_denied")

    monkeypatch.setattr(google_auth, "authorise", authorise)

    result = runner.invoke(cli.app, ["google", "auth", "--port", "8123"])

    assert result.exit_code == 1
    assert "access_denied" in result.stderr
    assert calls[0]["manual"] is False
    assert calls[0]["port"] == 8123


def test_worker_alive_passes_with_a_fresh_file(environment: Path) -> None:
    """The container health check exits 0 while the worker marks itself alive."""
    alive_file = environment / "worker-alive"
    alive_file.parent.mkdir(parents=True)
    alive_file.touch()

    result = runner.invoke(cli.app, ["worker", "alive"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"


def test_worker_alive_fails_with_a_stale_or_missing_file(environment: Path) -> None:
    """A missing or old alive file exits 1 and names the file."""
    missing = runner.invoke(cli.app, ["worker", "alive"])
    assert missing.exit_code == 1
    assert "worker-alive" in missing.stderr

    alive_file = environment / "worker-alive"
    alive_file.parent.mkdir(parents=True)
    alive_file.touch()
    os.utime(alive_file, (0, 0))

    stale = runner.invoke(cli.app, ["worker", "alive"])
    assert stale.exit_code == 1
