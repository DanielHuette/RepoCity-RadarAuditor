"""Tests for handoff.

Context: Unit tests with mocked external calls. | Input: Fixtures. | Output: Assertions.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call
from uuid import UUID, uuid4

import pytest

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.config import Settings
from radarauditor.db.models import (
    Audit,
    AuditStatus,
    CheckResult,
    Job,
    JobKind,
    JobStatus,
    Ranking,
)
from radarauditor.errors import HandoffError, StorageError
from radarauditor.jobs import handoff
from radarauditor.jobs.handoff import (
    RankingHistory,
    load_catalogue_json,
    pull,
    push,
    validate_findings,
)

JobFactory = Callable[..., Job]
VALID_TEXTS: dict[str, object] = {
    "introduction": "Sie werden gefunden, aber nicht überall.",
    "summary": "Die Website ist solide. Das Profil ist lückenhaft. Das kostet Anfragen.",
    "forecast_note": "Die Spanne ist eine Schätzung.",
    "subscription_reason": "Ihre Wettbewerber holen auf.",
    "offer_text": "Erstoptimierung und laufende Betreuung.",
    "talk_text": "Sie stehen auf der Karte hinter drei Nachbarn.",
}


@pytest.fixture
def raw_download(fake_repositories: MagicMock, sample_raw_audit: RawAudit) -> MagicMock:
    """Make the fake download write the sample RawAudit to the requested file."""

    def write(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(sample_raw_audit.model_dump_json(), encoding="utf-8")
        return local_file

    fake_repositories.download.side_effect = write
    return fake_repositories


def finding_entry(
    check_id: str = "a1", audit_id: UUID | None = None, **overrides: Any
) -> dict[str, Any]:
    """One findings.json entry for the sample audit."""
    entry: dict[str, Any] = {
        "id": str(uuid4()),
        "audit_id": str(audit_id or UUID("00000000-0000-4000-8000-000000000002")),
        "check_id": check_id,
        "severity": 3,
        "title": "Die Seite ist nicht verschlüsselt.",
        "explanation": "Die Startseite lädt ohne HTTPS.",
        "customer_impact": "Browser warnen Besucher.",
        "fix": "Zertifikat einrichten.",
        "fix_payload": None,
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def pulled_folder(raw_download: MagicMock, settings: Settings, make_job: JobFactory) -> Path:
    """A folder produced by pull for one ANALYSE job."""
    raw_download.claim_jobs.return_value = [make_job(JobKind.ANALYSE)]
    folders = pull(raw_download, settings)
    raw_download.reset_mock(return_value=False, side_effect=False)
    return folders[0]


def write_results(folder: Path, findings: object, texts: object) -> None:
    """Write Claude Code's output files."""
    (folder / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
    (folder / "texte.json").write_text(json.dumps(texts), encoding="utf-8")


def test_pull_materialises_job_folder(
    raw_download: MagicMock, settings: Settings, make_job: JobFactory
) -> None:
    """Pull writes audit, raw data, check results and catalogue into one folder per job."""
    job = make_job(JobKind.ANALYSE)
    raw_download.claim_jobs.return_value = [job]

    folders = pull(raw_download, settings)

    assert folders == [settings.radar_work_dir / "analyse" / str(job.id)]
    folder = folders[0]
    assert sorted(path.name for path in folder.iterdir()) == [
        "audit.json",
        "check_results.json",
        "katalog.json",
        "rankings.json",
        "raw.json",
    ]
    audit_json = json.loads((folder / "audit.json").read_text(encoding="utf-8"))
    assert set(audit_json) == {"audit", "customer", "locations", "competitors", "keywords"}
    assert audit_json["customer"]["name"] == "Dachdeckerei Muster"
    catalogue = json.loads((folder / "katalog.json").read_text(encoding="utf-8"))
    assert [check["id"] for check in catalogue["areas"][0]["checks"]] == ["a1", "a2"]
    results = json.loads((folder / "check_results.json").read_text(encoding="utf-8"))
    assert [result["check_id"] for result in results] == ["a1", "a2"]
    raw_download.claim_jobs.assert_called_once_with(JobKind.ANALYSE, handoff.PULL_LIMIT)
    raw_download.download.assert_called_once_with(
        "raw", f"{job.audit_id}/raw.json", folder / "raw.json"
    )


def test_pull_adds_previous_audit(
    raw_download: MagicMock,
    settings: Settings,
    make_job: JobFactory,
    sample_audit: Audit,
    sample_check_results: list[CheckResult],
) -> None:
    """A previous reported audit is written with its total score and check results."""
    previous = sample_audit.model_copy(
        update={"id": uuid4(), "status": AuditStatus.REPORTED, "total_score": Decimal("40")}
    )
    raw_download.previous_audit.return_value = previous
    raw_download.claim_jobs.return_value = [make_job(JobKind.ANALYSE)]

    (folder,) = pull(raw_download, settings)

    content = json.loads((folder / "vorheriges_audit.json").read_text(encoding="utf-8"))
    assert content["audit"]["id"] == str(previous.id)
    assert content["total_score"] == "40"
    assert len(content["check_results"]) == len(sample_check_results)
    raw_download.check_results.assert_any_call(previous.id)


def _ranking(audit_id: UUID, position: int) -> Ranking:
    return Ranking(
        id=uuid4(),
        audit_id=audit_id,
        keyword_id=UUID("00000000-0000-4000-8000-0000000000b1"),
        subject="customer",
        organic_position=position,
        local_pack_position=None,
        grid_point=None,
        measured_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )


def test_pull_writes_rankings_of_this_and_the_previous_audit(
    raw_download: MagicMock, settings: Settings, make_job: JobFactory, sample_audit: Audit
) -> None:
    """rankings.json holds both audits' positions so the trend can be described."""
    previous = sample_audit.model_copy(update={"id": uuid4(), "status": AuditStatus.REPORTED})
    current = _ranking(sample_audit.id, 3)
    earlier = _ranking(previous.id, 9)
    raw_download.previous_audit.return_value = previous
    raw_download.rankings.side_effect = lambda audit_id: (
        [current] if audit_id == sample_audit.id else [earlier]
    )
    raw_download.claim_jobs.return_value = [make_job(JobKind.ANALYSE)]

    (folder,) = pull(raw_download, settings)

    history = RankingHistory.model_validate_json(
        (folder / "rankings.json").read_text(encoding="utf-8")
    )
    assert history.audit_id == sample_audit.id
    assert history.rankings == [current]
    assert history.previous_audit_id == previous.id
    assert history.previous_rankings == [earlier]


def test_pull_writes_rankings_without_previous_audit(pulled_folder: Path) -> None:
    """Without a previous audit the previous side stays empty."""
    content = json.loads((pulled_folder / "rankings.json").read_text(encoding="utf-8"))
    assert content["previous_audit_id"] is None
    assert content["rankings"] == []
    assert content["previous_rankings"] == []


def test_pull_without_jobs_returns_nothing(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    """No queued jobs means no folders."""
    assert pull(fake_repositories, settings) == []


def test_pull_marks_unpreparable_job_failed(
    fake_repositories: MagicMock, settings: Settings, make_job: JobFactory, sample_audit: Audit
) -> None:
    """A job whose audit has no raw data fails and leaves no folder behind."""
    job = make_job(JobKind.ANALYSE)
    fake_repositories.claim_jobs.return_value = [job]
    fake_repositories.audit.return_value = sample_audit.model_copy(update={"raw_data_path": None})

    assert pull(fake_repositories, settings) == []
    fake_repositories.finish_job.assert_called_once()
    assert fake_repositories.finish_job.call_args.args[:2] == (job.id, JobStatus.FAILED)
    assert not (settings.radar_work_dir / "analyse" / str(job.id)).exists()


def test_pull_marks_job_failed_on_storage_error(
    fake_repositories: MagicMock, settings: Settings, make_job: JobFactory
) -> None:
    """A failed download fails the job and removes the partial folder."""
    job = make_job(JobKind.ANALYSE)
    fake_repositories.claim_jobs.return_value = [job]
    fake_repositories.download.side_effect = StorageError("download failed")

    assert pull(fake_repositories, settings) == []
    fake_repositories.finish_job.assert_called_once_with(
        job.id, JobStatus.FAILED, "download failed"
    )
    assert not (settings.radar_work_dir / "analyse" / str(job.id)).exists()


def test_pull_rejects_job_without_audit(
    fake_repositories: MagicMock, settings: Settings, make_job: JobFactory
) -> None:
    """An ANALYSE job must reference an audit."""
    job = make_job(JobKind.ANALYSE, audit_id=None)
    fake_repositories.claim_jobs.return_value = [job]

    assert pull(fake_repositories, settings) == []
    assert "no audit id" in fake_repositories.finish_job.call_args.args[2]


def test_catalogue_json_errors(tmp_path: Path) -> None:
    """Missing or broken YAML raises HandoffError naming the file."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("areas: [unclosed", encoding="utf-8")

    with pytest.raises(HandoffError, match="missing.yaml"):
        load_catalogue_json(tmp_path / "missing.yaml")
    with pytest.raises(HandoffError, match="broken.yaml"):
        load_catalogue_json(broken)


def test_push_uploads_and_advances(
    pulled_folder: Path, fake_repositories: MagicMock, settings: Settings, sample_audit: Audit
) -> None:
    """A valid folder is uploaded, saved, marked done, queued for the report and moved."""
    write_results(
        pulled_folder,
        [finding_entry("a1"), finding_entry("a2", severity=1)],
        VALID_TEXTS,
    )
    job_id = UUID(pulled_folder.name)

    done = push(fake_repositories, settings)

    target = settings.radar_work_dir / "erledigt" / pulled_folder.name
    assert done == [target]
    assert target.is_dir() and not pulled_folder.exists()
    assert fake_repositories.upload.call_args_list == [
        call("raw", f"{sample_audit.id}/texte.json", pulled_folder / "texte.json"),
        call("raw", f"{sample_audit.id}/auswertung.json", pulled_folder / "auswertung.json"),
    ]
    record = json.loads((target / "auswertung.json").read_text(encoding="utf-8"))
    assert record == {"path": "manual"}
    saved = fake_repositories.save_findings.call_args.args[0]
    assert [finding.check_id for finding in saved] == ["a1", "a2"]
    fake_repositories.set_audit_status.assert_called_once_with(
        sample_audit.id, AuditStatus.ANALYSED
    )
    fake_repositories.finish_job.assert_called_once_with(job_id, JobStatus.DONE)
    fake_repositories.enqueue.assert_called_once_with(
        JobKind.REPORT, sample_audit.customer_id, sample_audit.id
    )


def test_push_replaces_earlier_done_folder(
    pulled_folder: Path, fake_repositories: MagicMock, settings: Settings
) -> None:
    """Pushing the same job twice keeps only the latest folder in the done directory."""
    stale = settings.radar_work_dir / "erledigt" / pulled_folder.name
    stale.mkdir(parents=True)
    (stale / "old.txt").write_text("old", encoding="utf-8")
    write_results(pulled_folder, [], VALID_TEXTS)

    push(fake_repositories, settings)

    assert not (stale / "old.txt").exists()
    assert (stale / "findings.json").exists()


def test_push_skips_unfinished_folders(
    pulled_folder: Path, fake_repositories: MagicMock, settings: Settings
) -> None:
    """Folders without both output files are left alone."""
    (pulled_folder / "findings.json").write_text("[]", encoding="utf-8")

    assert push(fake_repositories, settings) == []
    assert pulled_folder.exists()
    fake_repositories.save_findings.assert_not_called()


def test_push_without_work_dir(fake_repositories: MagicMock, settings: Settings) -> None:
    """A missing analyse directory means nothing to push."""
    assert push(fake_repositories, settings) == []


@pytest.mark.parametrize(
    ("findings", "texts", "problem"),
    [
        ([finding_entry("z9")], VALID_TEXTS, "unknown check id"),
        ([finding_entry("a1"), finding_entry("a1")], VALID_TEXTS, "duplicate check id"),
        ([finding_entry("a1", severity=5)], VALID_TEXTS, "severity 5"),
        ([finding_entry("a1", audit_id=uuid4())], VALID_TEXTS, "audit_id"),
        ([{"check_id": "a1"}], VALID_TEXTS, "findings.json: invalid"),
        ({"not": "a list"}, VALID_TEXTS, "findings.json: invalid"),
        ([finding_entry("a1")], [], "texte.json invalid at <root>"),
        (
            [finding_entry("a1")],
            {key: value for key, value in VALID_TEXTS.items() if key != "summary"},
            "texte.json invalid at summary: Field required",
        ),
        (
            [finding_entry("a1")],
            {**VALID_TEXTS, "offer_text": ""},
            "texte.json invalid at offer_text",
        ),
        (
            [finding_entry("a1")],
            {key: value for key, value in VALID_TEXTS.items() if key != "talk_text"},
            "texte.json invalid at talk_text: Field required",
        ),
    ],
)
def test_push_rejects_invalid_folder(
    pulled_folder: Path,
    fake_repositories: MagicMock,
    settings: Settings,
    findings: object,
    texts: object,
    problem: str,
) -> None:
    """Invalid output raises HandoffError naming file and problem; the folder stays in place."""
    write_results(pulled_folder, findings, texts)

    with pytest.raises(HandoffError, match=problem):
        push(fake_repositories, settings)

    assert pulled_folder.exists()
    fake_repositories.save_findings.assert_not_called()
    fake_repositories.finish_job.assert_not_called()


def test_push_processes_valid_folders_before_reporting_invalid(
    raw_download: MagicMock, settings: Settings, make_job: JobFactory
) -> None:
    """One bad folder does not block the others."""
    good_job = make_job(JobKind.ANALYSE).model_copy(update={"id": uuid4()})
    bad_job = make_job(JobKind.ANALYSE).model_copy(update={"id": uuid4()})
    raw_download.claim_jobs.return_value = [good_job, bad_job]
    good_folder, bad_folder = pull(raw_download, settings)
    write_results(good_folder, [finding_entry("a1")], VALID_TEXTS)
    write_results(bad_folder, [finding_entry("z9")], VALID_TEXTS)

    with pytest.raises(HandoffError, match=str(bad_job.id)):
        push(raw_download, settings)

    assert bad_folder.exists()
    assert (settings.radar_work_dir / "erledigt" / str(good_job.id)).exists()


def test_push_rejects_folder_not_named_by_job(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    """Folder names must be job ids."""
    folder = settings.radar_work_dir / "analyse" / "kein-auftrag"
    folder.mkdir(parents=True)
    write_results(folder, [], VALID_TEXTS)

    with pytest.raises(HandoffError, match="not a job id"):
        push(fake_repositories, settings)


def test_validate_findings_returns_models(pulled_folder: Path) -> None:
    """Valid findings come back as Finding models."""
    (pulled_folder / "findings.json").write_text(
        json.dumps([finding_entry("a2")]), encoding="utf-8"
    )

    findings = validate_findings(pulled_folder)

    assert [finding.check_id for finding in findings] == ["a2"]


@pytest.mark.parametrize("missing", ["findings.json", "audit.json", "check_results.json"])
def test_validate_findings_names_missing_file(pulled_folder: Path, missing: str) -> None:
    """Each required input file is named when it is missing."""
    (pulled_folder / "findings.json").write_text("[]", encoding="utf-8")
    (pulled_folder / missing).unlink()

    with pytest.raises(HandoffError, match=missing):
        validate_findings(pulled_folder)


def test_validate_findings_rejects_broken_json(pulled_folder: Path) -> None:
    """Malformed JSON is reported as invalid."""
    (pulled_folder / "findings.json").write_text("[{", encoding="utf-8")

    with pytest.raises(HandoffError, match="findings.json: invalid"):
        validate_findings(pulled_folder)


def test_validate_findings_rejects_broken_check_results(pulled_folder: Path) -> None:
    """A tampered check_results.json is reported."""
    (pulled_folder / "findings.json").write_text("[]", encoding="utf-8")
    (pulled_folder / "check_results.json").write_text("{}", encoding="utf-8")

    with pytest.raises(HandoffError, match="check_results.json: invalid"):
        validate_findings(pulled_folder)


def test_validate_findings_rejects_broken_audit_file(pulled_folder: Path) -> None:
    """A tampered audit.json is reported."""
    (pulled_folder / "findings.json").write_text("[]", encoding="utf-8")
    (pulled_folder / "audit.json").write_text("{}", encoding="utf-8")

    with pytest.raises(HandoffError, match="audit.json: invalid"):
        validate_findings(pulled_folder)


def test_validate_texts_rejects_broken_json(
    pulled_folder: Path, fake_repositories: MagicMock, settings: Settings
) -> None:
    """Malformed texte.json is reported as invalid JSON."""
    (pulled_folder / "findings.json").write_text("[]", encoding="utf-8")
    (pulled_folder / "texte.json").write_text("{", encoding="utf-8")

    with pytest.raises(HandoffError, match="texte.json invalid at <root>: Invalid JSON"):
        push(fake_repositories, settings)
