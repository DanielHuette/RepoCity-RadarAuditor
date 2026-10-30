"""Command line entry point.

Context: Used by the operator, the container and Claude Code.
Input: Arguments.
Output: Console text and files.

Usage:
    radarauditor health
    radarauditor worker run
    radarauditor worker alive
    radarauditor audit run <customer-id> [--with-access]
    radarauditor jobs pull
    radarauditor jobs push
    radarauditor report sales <audit-id>
    radarauditor report monthly <audit-id>
    radarauditor document build <kind> <customer-id> <data.json>
    radarauditor google auth [--manual] [--port <port>]
"""

import asyncio
import functools
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import typer

from radarauditor.config import Settings, load_settings
from radarauditor.db.models import AuditKind, DocumentKind, JobKind
from radarauditor.db.repositories import Repositories
from radarauditor.errors import RadarError

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

app = typer.Typer(no_args_is_help=True)
worker_app = typer.Typer()
audit_app = typer.Typer()
jobs_app = typer.Typer()
report_app = typer.Typer()
document_app = typer.Typer()
google_app = typer.Typer()
app.add_typer(worker_app, name="worker")
app.add_typer(audit_app, name="audit")
app.add_typer(jobs_app, name="jobs")
app.add_typer(report_app, name="report")
app.add_typer(document_app, name="document")
app.add_typer(google_app, name="google")


def exits_on_error[**P](command: Callable[P, None]) -> Callable[P, None]:
    """Print package errors to stderr and exit with code 1 instead of a traceback.

    Args:
        command: Typer command function.

    Returns:
        The wrapped command.
    """

    @functools.wraps(command)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            command(*args, **kwargs)
        except RadarError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    return wrapper


def _context() -> tuple[Settings, Repositories]:
    settings = load_settings()
    return settings, Repositories(settings)


def _parse_uuid(value: str, name: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{value!r} is not a valid id", param_hint=name) from exc


@app.command()
@exits_on_error
def health() -> None:
    """Check settings and the Supabase connection; exit code 1 on failure."""
    _, repositories = _context()
    repositories.ping()
    typer.echo("ok")


@worker_app.command("run")
@exits_on_error
def worker_run() -> None:
    """Run the job loop until stopped."""
    from radarauditor.jobs.worker import run_forever

    asyncio.run(run_forever(load_settings()))


@worker_app.command("alive")
@exits_on_error
def worker_alive() -> None:
    """Exit 1 unless the worker stored a heartbeat in the last three minutes (health check)."""
    from radarauditor.jobs.heartbeat import ALIVE_MAX_AGE, alive_file_fresh

    alive_file = load_settings().worker_alive_file
    if not alive_file_fresh(alive_file, datetime.now(UTC)):
        minutes = int(ALIVE_MAX_AGE.total_seconds() // 60)
        typer.echo(f"no heartbeat in {alive_file} within {minutes} minutes", err=True)
        raise typer.Exit(code=1)
    typer.echo("ok")


@audit_app.command("run")
@exits_on_error
def audit_run(customer_id: str, with_access: bool = False) -> None:
    """Create an audit for a customer and enqueue collection."""
    customer_uuid = _parse_uuid(customer_id, "customer-id")
    _, repositories = _context()
    customer = repositories.customer(customer_uuid)
    kind = AuditKind.WITH_ACCESS if with_access else AuditKind.REMOTE
    audit = repositories.create_audit(customer.id, kind)
    repositories.enqueue(JobKind.COLLECT, customer.id, audit.id)
    typer.echo(str(audit.id))


@jobs_app.command("pull")
@exits_on_error
def jobs_pull() -> None:
    """Write pending analysis jobs into the work directory for Claude Code."""
    from radarauditor.jobs.handoff import pull

    settings, repositories = _context()
    for folder in pull(repositories, settings):
        typer.echo(str(folder))


@jobs_app.command("push")
@exits_on_error
def jobs_push() -> None:
    """Upload finished analyses from the work directory."""
    from radarauditor.jobs.handoff import push

    settings, repositories = _context()
    for folder in push(repositories, settings):
        typer.echo(str(folder))


@report_app.command("sales")
@exits_on_error
def report_sales(audit_id: str) -> None:
    """Render the sales report PDF."""
    from radarauditor.reporting.report import build_sales_report

    audit_uuid = _parse_uuid(audit_id, "audit-id")
    settings, repositories = _context()
    typer.echo(str(build_sales_report(audit_uuid, repositories, settings)))


@report_app.command("monthly")
@exits_on_error
def report_monthly(audit_id: str) -> None:
    """Render the before-after report PDF."""
    from radarauditor.reporting.report import build_monthly_report

    audit_uuid = _parse_uuid(audit_id, "audit-id")
    settings, repositories = _context()
    typer.echo(str(build_monthly_report(audit_uuid, repositories, settings)))


@document_app.command("build")
@exits_on_error
def document_build(kind: str, customer_id: str, data_file: str) -> None:
    """Render a business document from a JSON data file."""
    from radarauditor.documents.builder import build_document

    document_kind = _parse_document_kind(kind)
    customer_uuid = _parse_uuid(customer_id, "customer-id")
    data = _read_data_file(Path(data_file))
    settings, repositories = _context()
    document = build_document(document_kind, customer_uuid, data, repositories, settings)
    typer.echo(document.file_path or str(document.id))


def _parse_document_kind(value: str) -> DocumentKind:
    try:
        return DocumentKind(value)
    except ValueError as exc:
        choices = ", ".join(kind.value for kind in DocumentKind)
        raise typer.BadParameter(f"{value!r} is not one of {choices}", param_hint="kind") from exc


def _read_data_file(path: Path) -> dict[str, object]:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"{path}: {exc}", param_hint="data-file") from exc
    if not isinstance(content, dict):
        raise typer.BadParameter(f"{path}: must contain a JSON object", param_hint="data-file")
    return content


@google_app.command("auth")
@exits_on_error
def google_auth(manual: bool = False, port: int = 0) -> None:
    """Authorise the operator's Google account for audits with customer access.

    Prints the consent address and waits for Google's redirect on a local port; with --manual
    the address the browser ends on is pasted instead. The refresh token is stored in Supabase.
    """
    from radarauditor.collectors.google_auth import authorise

    settings, repositories = _context()
    grant = authorise(
        settings,
        repositories,
        manual=manual,
        port=port,
        show=typer.echo,
        read_line=lambda: typer.prompt("redirect address", prompt_suffix=": "),
    )
    typer.echo(f"stored Google authorisation for {len(grant.granted_scopes)} scopes")
    for scope in grant.missing_scopes():
        typer.echo(f"not granted: {scope}", err=True)


def main() -> None:
    """Configure logging and run the Typer app."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    app()


if __name__ == "__main__":
    main()
