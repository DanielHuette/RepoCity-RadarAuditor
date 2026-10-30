"""Hand ANALYSE jobs to Claude Code and take its results back.

Context: `radarauditor jobs pull` writes one folder per job with raw data, check results, rankings
of this and the previous audit and the catalogue wording; Claude Code writes findings.json and
texts; `radarauditor jobs push` validates and uploads them. The worker runs the same steps itself
(jobs/analysis.py). auswertung.json records whether the worker or a manual push delivered the
files and is uploaded next to texte.json.
Input: Work directory.
Output: Findings, texts, audit status.
"""

import json
import logging
import shutil
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from uuid import UUID

import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from radarauditor.config import Settings
from radarauditor.db.models import (
    Audit,
    AuditStatus,
    CheckResult,
    Competitor,
    Customer,
    Finding,
    Job,
    JobKind,
    JobStatus,
    Keyword,
    Location,
    Ranking,
)
from radarauditor.db.repositories import Repositories
from radarauditor.errors import HandoffError, RenderError, StorageError
from radarauditor.reporting.texts import parse_texts

logger = logging.getLogger(__name__)

PULL_LIMIT = 20
ANALYSE_DIR = "analyse"
DONE_DIR = "erledigt"
RAW_BUCKET = "raw"
AUDIT_FILE = "audit.json"
RAW_FILE = "raw.json"
CHECK_RESULTS_FILE = "check_results.json"
CATALOGUE_FILE = "katalog.json"
PREVIOUS_AUDIT_FILE = "vorheriges_audit.json"
RANKINGS_FILE = "rankings.json"
FINDINGS_FILE = "findings.json"
TEXTS_FILE = "texte.json"
ANALYSIS_FILE = "auswertung.json"

_FINDINGS_ADAPTER = TypeAdapter(list[Finding])
_CHECK_RESULTS_ADAPTER = TypeAdapter(list[CheckResult])


class AnalysisPath(StrEnum):
    """Who delivered findings.json and texte.json: the worker's Claude Code run or `jobs push`."""

    CLAUDE_CODE = "claude_code"
    MANUAL = "manual"


class AnalysisRecord(BaseModel):
    """Content of auswertung.json: the path that delivered the analysis."""

    path: AnalysisPath


class AnalysisInput(BaseModel):
    """Content of audit.json: the audit and everything known about its customer."""

    audit: Audit
    customer: Customer
    locations: list[Location]
    competitors: list[Competitor]
    keywords: list[Keyword]


class PreviousAudit(BaseModel):
    """Content of vorheriges_audit.json: the last reported audit for comparison."""

    audit: Audit
    total_score: Decimal | None
    check_results: list[CheckResult]


class RankingHistory(BaseModel):
    """Content of rankings.json: positions of this audit and of the previous one, for j9."""

    audit_id: UUID
    rankings: list[Ranking]
    previous_audit_id: UUID | None
    previous_rankings: list[Ranking]


def pull(repositories: Repositories, settings: Settings) -> list[Path]:
    """Claim ANALYSE jobs and materialise them as folders; return the folder paths."""
    catalogue_json = load_catalogue_json(settings.catalogue_path)
    folders: list[Path] = []
    for job in repositories.claim_jobs(JobKind.ANALYSE, PULL_LIMIT):
        folder = settings.radar_work_dir / ANALYSE_DIR / str(job.id)
        try:
            materialise(job, folder, catalogue_json, repositories)
        except (HandoffError, StorageError) as exc:
            logger.error("Analyse job %s could not be prepared: %s", job.id, exc)
            shutil.rmtree(folder, ignore_errors=True)
            repositories.finish_job(job.id, JobStatus.FAILED, str(exc))
            continue
        logger.info("Analyse job %s prepared in %s", job.id, folder)
        folders.append(folder)
    return folders


def load_catalogue_json(path: Path) -> str:
    """Read the YAML catalogue and serialise it unchanged as JSON.

    Args:
        path: Location of pruefkatalog.yaml.

    Returns:
        Indented JSON text.

    Raises:
        HandoffError: If the file cannot be read or is not valid YAML.
    """
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise HandoffError(f"{path}: catalogue cannot be read: {exc}") from exc
    return json.dumps(content, ensure_ascii=False, indent=2)


def materialise(job: Job, folder: Path, catalogue_json: str, repositories: Repositories) -> None:
    """Write every input file of one ANALYSE job into its folder.

    Args:
        job: Claimed ANALYSE job.
        folder: Target folder, created when missing.
        catalogue_json: Catalogue as JSON text (load_catalogue_json).
        repositories: Database and storage access.

    Raises:
        HandoffError: If the job has no audit or the audit has no raw data.
        StorageError: If a database read or the raw data download fails.
    """
    if job.audit_id is None:
        raise HandoffError(f"job {job.id} has no audit id")
    audit = repositories.audit(job.audit_id)
    if audit.raw_data_path is None:
        raise HandoffError(f"audit {audit.id} has no raw data path")
    folder.mkdir(parents=True, exist_ok=True)
    write_atomic(folder / AUDIT_FILE, analysis_input(audit, repositories).model_dump_json(indent=2))
    repositories.download(RAW_BUCKET, audit.raw_data_path, folder / RAW_FILE)
    results = repositories.check_results(audit.id)
    write_atomic(
        folder / CHECK_RESULTS_FILE,
        _CHECK_RESULTS_ADAPTER.dump_json(results, indent=2).decode(),
    )
    write_atomic(folder / CATALOGUE_FILE, catalogue_json)
    previous = repositories.previous_audit(audit)
    if previous is not None:
        previous_audit = PreviousAudit(
            audit=previous,
            total_score=previous.total_score,
            check_results=repositories.check_results(previous.id),
        )
        write_atomic(folder / PREVIOUS_AUDIT_FILE, previous_audit.model_dump_json(indent=2))
    history = _ranking_history(audit, previous, repositories)
    write_atomic(folder / RANKINGS_FILE, history.model_dump_json(indent=2))


def analysis_input(audit: Audit, repositories: Repositories) -> AnalysisInput:
    """Load the audit's customer, locations, competitors and keywords.

    Args:
        audit: Audit the job works on.
        repositories: Database access.

    Returns:
        The audit with everything stored about its customer.

    Raises:
        StorageError: If a database read fails.
    """
    return AnalysisInput(
        audit=audit,
        customer=repositories.customer(audit.customer_id),
        locations=repositories.locations(audit.customer_id),
        competitors=repositories.competitors(audit.customer_id),
        keywords=repositories.keywords(audit.customer_id),
    )


def _ranking_history(
    audit: Audit, previous: Audit | None, repositories: Repositories
) -> RankingHistory:
    return RankingHistory(
        audit_id=audit.id,
        rankings=repositories.rankings(audit.id),
        previous_audit_id=previous.id if previous is not None else None,
        previous_rankings=repositories.rankings(previous.id) if previous is not None else [],
    )


def write_atomic(target: Path, text: str) -> None:
    """Write text through a temporary file so readers never see a half-written file."""
    partial = target.with_name(f".{target.name}.part")
    partial.write_text(text, encoding="utf-8")
    partial.replace(target)


def push(repositories: Repositories, settings: Settings) -> list[Path]:
    """Read every folder with a findings.json, validate, upload, mark the job done.

    Valid folders are processed even when others fail; invalid folders stay in place.

    Returns:
        The folders moved to the done directory.

    Raises:
        HandoffError: After processing, if at least one folder was invalid.
    """
    analyse_root = settings.radar_work_dir / ANALYSE_DIR
    if not analyse_root.is_dir():
        return []
    done: list[Path] = []
    problems: list[str] = []
    for folder in sorted(path for path in analyse_root.iterdir() if path.is_dir()):
        if not (folder / FINDINGS_FILE).exists() or not (folder / TEXTS_FILE).exists():
            continue
        try:
            done.append(_push_folder(folder, repositories, settings.radar_work_dir / DONE_DIR))
        except HandoffError as exc:
            logger.error("Hand-off folder %s rejected: %s", folder.name, exc)
            problems.append(str(exc))
    if problems:
        raise HandoffError("; ".join(problems))
    return done


def _push_folder(folder: Path, repositories: Repositories, done_root: Path) -> Path:
    job_id = _job_id(folder)
    target = deliver(folder, repositories, done_root, AnalysisRecord(path=AnalysisPath.MANUAL))
    repositories.finish_job(job_id, JobStatus.DONE)
    return target


def deliver(
    folder: Path, repositories: Repositories, done_root: Path, record: AnalysisRecord
) -> Path:
    """Validate a finished folder, upload texts and record, save findings, queue the report.

    The job itself is not marked done; `jobs push` and the worker do that.

    Args:
        folder: Folder with input files, findings.json and texte.json.
        repositories: Database and storage access.
        done_root: Directory the folder is moved into afterwards.
        record: Path that produced the analysis, written to auswertung.json after validation.

    Returns:
        The folder's new location under done_root.

    Raises:
        HandoffError: If a file is missing or invalid.
        StorageError: If an upload or database write fails.
    """
    job_id = _job_id(folder)
    audit = read_analysis_input(folder).audit
    findings = validate_outputs(folder)
    write_atomic(folder / ANALYSIS_FILE, record.model_dump_json(indent=2))
    repositories.upload(RAW_BUCKET, f"{audit.id}/{TEXTS_FILE}", folder / TEXTS_FILE)
    repositories.upload(RAW_BUCKET, f"{audit.id}/{ANALYSIS_FILE}", folder / ANALYSIS_FILE)
    repositories.save_findings(findings)
    repositories.set_audit_status(audit.id, AuditStatus.ANALYSED)
    repositories.enqueue(JobKind.REPORT, audit.customer_id, audit.id)
    target = done_root / folder.name
    done_root.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.move(folder, target)
    logger.info("Analyse job %s pushed with %d findings", job_id, len(findings))
    return target


def _job_id(folder: Path) -> UUID:
    try:
        return UUID(folder.name)
    except ValueError as exc:
        raise HandoffError(f"{folder}: folder name is not a job id") from exc


def read_analysis_input(folder: Path) -> AnalysisInput:
    """Parse audit.json of a hand-off folder.

    Raises:
        HandoffError: If the file is missing or invalid.
    """
    path = folder / AUDIT_FILE
    try:
        return AnalysisInput.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HandoffError(f"{path}: cannot be read: {exc}") from exc
    except ValidationError as exc:
        raise HandoffError(f"{path}: invalid: {_first_error(exc)}") from exc


def validate_outputs(folder: Path) -> list[Finding]:
    """Check findings.json and texte.json exactly as `jobs push` does.

    Returns:
        The validated findings.

    Raises:
        HandoffError: If either file is missing or invalid.
    """
    findings = validate_findings(folder)
    _validate_texts(folder)
    return findings


def _validate_texts(folder: Path) -> None:
    path = folder / TEXTS_FILE
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HandoffError(f"{path}: cannot be read: {exc}") from exc
    try:
        texts = parse_texts(content)
    except RenderError as exc:
        raise HandoffError(f"{path}: {exc}") from exc
    # Optional only for texte.json files of earlier evaluations; every new one carries it.
    if texts.talk_text is None:
        raise HandoffError(f"{path}: texte.json invalid at talk_text: Field required")


def validate_findings(folder: Path) -> list[Finding]:
    """Parse findings.json against the Finding model and the audit's check ids.

    Raises:
        HandoffError: If the file is missing, malformed or references unknown checks.
    """
    path = folder / FINDINGS_FILE
    audit_id = read_analysis_input(folder).audit.id
    known_check_ids = _known_check_ids(folder)
    try:
        findings = _FINDINGS_ADAPTER.validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HandoffError(f"{path}: cannot be read: {exc}") from exc
    except ValidationError as exc:
        raise HandoffError(f"{path}: invalid: {_first_error(exc)}") from exc
    seen: set[str] = set()
    for index, finding in enumerate(findings):
        location = f"{path}: entry {index} ({finding.check_id})"
        if finding.audit_id != audit_id:
            raise HandoffError(f"{location}: audit_id {finding.audit_id} is not {audit_id}")
        if finding.check_id not in known_check_ids:
            raise HandoffError(f"{location}: unknown check id")
        if finding.check_id in seen:
            raise HandoffError(f"{location}: duplicate check id")
        if not 1 <= finding.severity <= 3:
            raise HandoffError(f"{location}: severity {finding.severity} is not between 1 and 3")
        seen.add(finding.check_id)
    return findings


def _known_check_ids(folder: Path) -> set[str]:
    return {result.check_id for result in read_check_results(folder)}


def read_check_results(folder: Path) -> list[CheckResult]:
    """Parse check_results.json of a hand-off folder.

    Raises:
        HandoffError: If the file is missing or invalid.
    """
    path = folder / CHECK_RESULTS_FILE
    try:
        return _CHECK_RESULTS_ADAPTER.validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HandoffError(f"{path}: cannot be read: {exc}") from exc
    except ValidationError as exc:
        raise HandoffError(f"{path}: invalid: {_first_error(exc)}") from exc


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "<root>"
    return f"{location}: {error['msg']} ({exc.error_count()} error(s) in total)"
