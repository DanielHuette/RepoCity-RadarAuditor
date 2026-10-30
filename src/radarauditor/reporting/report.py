"""Render the sales report and the monthly before-after report as PDF.

Context: Jinja2 HTML templates in templates/report rendered with WeasyPrint. Chart wording and the
names of the profile metrics come from templates/report/labels.yaml, report prose from
texte.json; without texte.json the templates fall back to neutral standard sentences. Profile
figures appear only for audits with customer access, the keyword gap only with paid data.
Input: Audit, results, findings, forecast, charts.
Output: PDF path.
"""

import logging
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, Field, ValidationError

from radarauditor.collectors.snapshot import GapKeyword, PlaceSnapshot, RawAudit
from radarauditor.config import Settings
from radarauditor.db.models import (
    Audit,
    AuditStatus,
    CheckOutcome,
    CheckResult,
    Customer,
    Finding,
    Keyword,
    Location,
    Ranking,
)
from radarauditor.db.repositories import Repositories
from radarauditor.errors import RenderError, StorageError
from radarauditor.rendering import filename_slug, german_date, render_template, write_pdf
from radarauditor.reporting import charts
from radarauditor.reporting.forecast import Forecast, best_positions, report_forecast
from radarauditor.reporting.texts import ReportTexts, parse_texts
from radarauditor.scoring.catalog import Catalogue, load_catalogue
from radarauditor.scoring.evaluator import POINTS, area_scores, total_score

logger = logging.getLogger(__name__)

REPORT_DIR = "berichte"
REPORT_TEMPLATE_DIR = "report"
SALES_TEMPLATE = "sales.html"
MONTHLY_TEMPLATE = "monthly.html"
LABELS_FILE = "labels.yaml"
SALES_FILE_PREFIX = "Sichtbarkeitsbericht"
MONTHLY_FILE_PREFIX = "Monatsbericht"
RAW_BUCKET = "raw"
RAW_FILE = "raw.json"
TEXTS_FILE = "texte.json"
TOP_FINDINGS = 3
MAX_COMPETITORS = 3
MAX_GAP_KEYWORDS = 10
PERCENT = 100
CUSTOMER_SUBJECT = "customer"
BAD_BELOW = Decimal(charts.BAD_BELOW)
WARN_BELOW = Decimal(charts.WARN_BELOW)

Trend = Literal["up", "down", "same"]
Level = Literal["ok", "warn", "bad"]


class ChartTitles(BaseModel):
    """Titles and legend lines of the charts."""

    area_traffic_lights: str
    competitor_comparison: str
    grid_map: str
    score_trend: str
    ranking_table: str
    ranking_legend: str


class SubjectLabels(BaseModel):
    """How the customer is named in charts."""

    customer: str


class MetricLabels(BaseModel):
    """Names of the compared Google profile metrics."""

    reviews: str
    rating: str
    photos: str


class ReportLabels(BaseModel):
    """Content of templates/report/labels.yaml; profile_metrics names the profile figures shown."""

    charts: ChartTitles
    subjects: SubjectLabels
    metrics: MetricLabels
    profile_metrics: dict[str, str] = Field(default_factory=dict)


class ProfileMetricLine(BaseModel):
    """One Google profile figure of the last 28 days against the 28 days before."""

    label: str
    value: int
    previous: int
    change_percent: float | None
    trend: Literal["up", "down", "same"]


class AreaLine(BaseModel):
    """Score of one catalogue area."""

    area_id: str
    name: str
    score: Decimal
    level: Level


class FindingLine(BaseModel):
    """A finding with the name of its area."""

    area_name: str
    check_id: str
    severity: int
    title: str
    explanation: str
    customer_impact: str
    fix: str


class FindingGroup(BaseModel):
    """Findings of one area in catalogue order."""

    area_name: str
    findings: list[FindingLine]


class ChartFiles(BaseModel):
    """File URIs of the rendered charts; None where data was missing."""

    areas: str | None = None
    competitors: str | None = None
    grid: str | None = None
    rankings: str | None = None
    trend: str | None = None


class SalesReport(BaseModel):
    """Everything sales.html shows."""

    customer_name: str
    customer_address: str
    report_date: date
    total_score: Decimal | None
    texts: ReportTexts | None
    areas: list[AreaLine]
    strongest_area: AreaLine | None
    weakest_area: AreaLine | None
    charts: ChartFiles
    top_findings: list[FindingLine]
    finding_groups: list[FindingGroup]
    forecast: Forecast
    profile: list[ProfileMetricLine] = Field(default_factory=list)
    keyword_gap: list[GapKeyword] = Field(default_factory=list)


class ScoreChange(BaseModel):
    """A score before and after."""

    label: str
    before: Decimal | None
    after: Decimal | None
    trend: Trend


class RankingChange(BaseModel):
    """Positions of one keyword before and after, organic and in the local pack."""

    phrase: str
    organic_before: int | None
    organic_after: int | None
    organic_trend: Trend
    local_before: int | None
    local_after: int | None
    local_trend: Trend


class MonthlyReport(BaseModel):
    """Everything monthly.html shows."""

    customer_name: str
    report_date: date
    previous_date: date
    total: ScoreChange
    areas: list[ScoreChange]
    rankings: list[RankingChange]
    done: list[FindingLine]
    next_steps: list[FindingLine]
    texts: ReportTexts | None
    charts: ChartFiles
    profile: list[ProfileMetricLine] = Field(default_factory=list)


class AuditBundle(BaseModel):
    """One audit with everything stored about it."""

    audit: Audit
    results: list[CheckResult]
    findings: list[Finding]
    rankings: list[Ranking]


def _today() -> date:
    return datetime.now(UTC).date()


def load_labels(templates_dir: Path) -> ReportLabels:
    """Read the chart wording.

    Args:
        templates_dir: Root template folder holding report/labels.yaml.

    Returns:
        Parsed labels.

    Raises:
        RenderError: If the file is missing or malformed.
    """
    path = templates_dir / REPORT_TEMPLATE_DIR / LABELS_FILE
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ReportLabels.model_validate(content)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise RenderError(f"report labels {path} cannot be read: {exc}") from exc


def audit_rankings(repositories: Repositories, audit_id: UUID) -> list[Ranking]:
    """Rankings of an audit, or none while the repositories offer no ranking reader."""
    reader: Callable[[UUID], list[Ranking]] | None = getattr(repositories, "rankings", None)
    if reader is None:
        logger.warning("Repositories cannot read rankings; report shows none")
        return []
    return list(reader(audit_id))


def _bundle(repositories: Repositories, audit: Audit) -> AuditBundle:
    return AuditBundle(
        audit=audit,
        results=repositories.check_results(audit.id),
        findings=repositories.findings_of_audit(audit.id),
        rankings=audit_rankings(repositories, audit.id),
    )


def _download_text(
    repositories: Repositories, remote_path: str, scratch: Path, name: str
) -> str | None:
    try:
        local = repositories.download(RAW_BUCKET, remote_path, scratch / name)
        return local.read_text(encoding="utf-8")
    except (StorageError, OSError) as exc:
        logger.warning("%s/%s not available: %s", RAW_BUCKET, remote_path, exc)
        return None


def load_texts(repositories: Repositories, audit_id: UUID, scratch: Path) -> ReportTexts | None:
    """Download and validate texte.json; a missing or invalid file yields None.

    Args:
        repositories: Data access.
        audit_id: Audit whose texts are read from bucket raw.
        scratch: Folder for the downloaded file.

    Returns:
        The texts, or None so the template uses its standard sentences.
    """
    content = _download_text(repositories, f"{audit_id}/{TEXTS_FILE}", scratch, TEXTS_FILE)
    if content is None:
        return None
    try:
        return parse_texts(content)
    except RenderError as exc:
        logger.warning("Audit %s: %s; standard sentences are used", audit_id, exc)
        return None


def load_raw(repositories: Repositories, audit: Audit, scratch: Path) -> RawAudit | None:
    """Download the raw audit data; missing or invalid data yields None."""
    if audit.raw_data_path is None:
        return None
    content = _download_text(repositories, audit.raw_data_path, scratch, RAW_FILE)
    if content is None:
        return None
    try:
        return RawAudit.model_validate_json(content)
    except ValidationError as exc:
        logger.warning("Audit %s has invalid raw data: %s", audit.id, exc.errors()[0]["msg"])
        return None


def _level(score: Decimal) -> Level:
    if score < BAD_BELOW:
        return "bad"
    if score < WARN_BELOW:
        return "warn"
    return "ok"


def area_lines(results: list[CheckResult], catalogue: Catalogue) -> list[AreaLine]:
    """Score per scored area in catalogue order, with the German area name."""
    names = {area.id: area.name for area in catalogue.areas}
    return [
        AreaLine(area_id=area_id, name=names[area_id], score=score, level=_level(score))
        for area_id, score in area_scores(results, catalogue).items()
    ]


def check_order(check_id: str) -> tuple[str, int]:
    """Sort key that puts b2 before b10."""
    digits = check_id[1:]
    return check_id[:1], int(digits) if digits.isdigit() else 0


def finding_lines(
    findings: list[Finding], results: list[CheckResult], catalogue: Catalogue
) -> list[FindingLine]:
    """Findings with area names, most severe first, then by check id."""
    names = {area.id: area.name for area in catalogue.areas}
    area_of = {result.check_id: result.area for result in results}
    ordered = sorted(
        findings, key=lambda finding: (-finding.severity, check_order(finding.check_id))
    )
    return [
        FindingLine(
            area_name=names.get(area_of.get(finding.check_id, finding.check_id[:1]), ""),
            **finding.model_dump(
                include={"check_id", "severity", "title", "explanation", "customer_impact", "fix"}
            ),
        )
        for finding in ordered
    ]


def group_findings(lines: list[FindingLine], catalogue: Catalogue) -> list[FindingGroup]:
    """Group findings by area in catalogue order; areas without findings are left out."""
    groups: list[FindingGroup] = []
    for area in catalogue.areas:
        members = sorted(
            (line for line in lines if line.area_name == area.name),
            key=lambda line: check_order(line.check_id),
        )
        if members:
            groups.append(FindingGroup(area_name=area.name, findings=members))
    return groups


def competitor_values(
    raw: RawAudit | None, labels: ReportLabels
) -> dict[str, dict[str, float]] | None:
    """Reviews, rating and photos of the customer and up to three competitors."""
    if raw is None or raw.place is None or not raw.competitor_places:
        return None
    metrics = labels.metrics

    def row(place: PlaceSnapshot) -> dict[str, float]:
        return {
            metrics.reviews: float(place.review_count),
            metrics.rating: place.rating or 0.0,
            metrics.photos: float(place.photo_count),
        }

    values = {labels.subjects.customer: row(raw.place)}
    for place in raw.competitor_places[:MAX_COMPETITORS]:
        values[place.name] = row(place)
    return values


def profile_lines(raw: RawAudit | None, labels: ReportLabels) -> list[ProfileMetricLine]:
    """Profile figures named in labels.yaml, in that order; empty without access data."""
    if raw is None or raw.access is None or not raw.access.profile_metrics:
        return []
    now = raw.access.profile_metrics
    before = raw.access.profile_metrics_previous
    lines: list[ProfileMetricLine] = []
    for metric, label in labels.profile_metrics.items():
        if metric not in now:
            continue
        value, previous = now[metric], before.get(metric, 0)
        change = round((value - previous) / previous * PERCENT, 1) if previous else None
        trend: Trend = "same" if value == previous else ("up" if value > previous else "down")
        lines.append(
            ProfileMetricLine(
                label=label, value=value, previous=previous, change_percent=change, trend=trend
            )
        )
    return lines


def gap_keywords(raw: RawAudit | None) -> list[GapKeyword]:
    """The highest-volume phrases competitors rank for and the customer does not."""
    if raw is None or raw.paid is None or not raw.paid.keyword_gap:
        return []
    ordered = sorted(raw.paid.keyword_gap, key=lambda keyword: -keyword.volume)
    return ordered[:MAX_GAP_KEYWORDS]


def grid_points(keywords: list[Keyword], rankings: list[Ranking]) -> dict[str, int | None]:
    """Local pack positions of the customer on the grid of the primary keyword."""
    gridded = [r for r in rankings if r.subject == CUSTOMER_SUBJECT and r.grid_point is not None]
    if not gridded:
        return {}
    measured = {ranking.keyword_id for ranking in gridded}
    candidates = sorted(
        (keyword for keyword in keywords if keyword.id in measured),
        key=lambda keyword: not keyword.is_primary,
    )
    chosen = candidates[0].id if candidates else gridded[0].keyword_id
    return {
        r.grid_point: r.local_pack_position
        for r in gridded
        if r.keyword_id == chosen and r.grid_point
    }


def _draw_optional(draw: Callable[[], Path], name: str) -> str | None:
    """Draw a chart that the report can do without; failures are logged."""
    try:
        return draw().as_uri()
    except RenderError as exc:
        logger.warning("Chart %s left out: %s", name, exc)
        return None


def _sales_charts(
    lines: list[AreaLine],
    raw: RawAudit | None,
    keywords: list[Keyword],
    rankings: list[Ranking],
    labels: ReportLabels,
    chart_dir: Path,
) -> ChartFiles:
    """Draw the charts of the sales report into chart_dir.

    Args:
        lines: Scored areas; without them the traffic lights are left out.
        raw: Collected raw data for the competitor comparison, if any.
        keywords: Tracked keywords; the primary one picks the grid.
        rankings: Rankings of the audit.
        labels: Chart titles and the customer's column heading.
        chart_dir: Folder the PNG files are written to.

    Returns:
        File URIs of the drawn charts; grid map and ranking table stay empty when they fail.

    Raises:
        RenderError: If the traffic lights or the competitor comparison cannot be written.
    """
    titles = labels.charts
    files = ChartFiles()
    if lines:
        files.areas = charts.area_traffic_lights(
            {line.area_id: line.score for line in lines},
            chart_dir / "bereiche.png",
            area_names={line.area_id: line.name for line in lines},
            title=titles.area_traffic_lights,
        ).as_uri()
    values = competitor_values(raw, labels)
    if values:
        files.competitors = charts.competitor_comparison(
            values, chart_dir / "wettbewerber.png", title=titles.competitor_comparison
        ).as_uri()
    points = grid_points(keywords, rankings)
    if points:
        files.grid = _draw_optional(
            lambda: charts.grid_map(points, chart_dir / "karte.png", title=titles.grid_map),
            "grid map",
        )
    files.rankings = _ranking_chart(keywords, rankings, labels, chart_dir)
    return files


def _ranking_chart(
    keywords: list[Keyword], rankings: list[Ranking], labels: ReportLabels, chart_dir: Path
) -> str | None:
    phrases = {keyword.id: keyword.phrase for keyword in keywords}
    return _draw_optional(
        lambda: charts.ranking_table_image(
            rankings,
            chart_dir / "plaetze.png",
            keyword_phrases=phrases,
            subject_labels={CUSTOMER_SUBJECT: labels.subjects.customer},
            title=labels.charts.ranking_table,
            legend=labels.charts.ranking_legend,
        ),
        "ranking table",
    )


def _forecast(
    customer: Customer, keywords: list[Keyword], rankings: list[Ranking], settings: Settings
) -> Forecast:
    return report_forecast(
        customer.industry, keywords, rankings, settings.forecast_path, settings.industries_path
    )


def _address(location: Location) -> str:
    return f"{location.street}, {location.postal_code} {location.city}"


def _total(audit: Audit, results: list[CheckResult], catalogue: Catalogue) -> Decimal | None:
    if audit.total_score is not None:
        return audit.total_score
    if not any(result.outcome in POINTS for result in results):
        return None
    return total_score(results, catalogue)


def sales_report_context(
    audit_id: UUID, repositories: Repositories, settings: Settings, work_dir: Path
) -> SalesReport:
    """Load everything the sales report shows and draw its charts.

    Args:
        audit_id: Audit to report.
        repositories: Data access.
        settings: Paths of catalogue, forecast curves and templates.
        work_dir: Scratch folder for downloads and chart images; must outlive the rendering.

    Returns:
        The filled report model.

    Raises:
        StorageError: If audit, customer or results cannot be loaded.
        RenderError: If labels are malformed or a required chart cannot be written.
        ConfigurationError: If catalogue or forecast curves are malformed.
    """
    audit = repositories.audit(audit_id)
    customer = repositories.customer(audit.customer_id)
    keywords = repositories.keywords(audit.customer_id)
    locations = repositories.locations(audit.customer_id)
    bundle = _bundle(repositories, audit)
    catalogue = load_catalogue(settings.catalogue_path)
    labels = load_labels(settings.templates_dir)
    lines = area_lines(bundle.results, catalogue)
    findings = finding_lines(bundle.findings, bundle.results, catalogue)
    ranked = sorted(lines, key=lambda line: line.score)
    raw = load_raw(repositories, audit, work_dir)
    return SalesReport(
        customer_name=customer.name,
        customer_address=_address(locations[0]) if locations else customer.city,
        report_date=_today(),
        total_score=_total(audit, bundle.results, catalogue),
        texts=load_texts(repositories, audit_id, work_dir),
        areas=lines,
        strongest_area=ranked[-1] if ranked else None,
        weakest_area=ranked[0] if ranked else None,
        charts=_sales_charts(lines, raw, keywords, bundle.rankings, labels, work_dir),
        top_findings=findings[:TOP_FINDINGS],
        finding_groups=group_findings(findings, catalogue),
        forecast=_forecast(customer, keywords, bundle.rankings, settings),
        profile=profile_lines(raw, labels),
        keyword_gap=gap_keywords(raw),
    )


def _report_path(settings: Settings, audit_id: UUID, prefix: str, name: str, day: date) -> Path:
    file_name = f"{prefix}_{filename_slug(name)}_{day.isoformat()}.pdf"
    return settings.radar_work_dir / REPORT_DIR / str(audit_id) / file_name


def _render(settings: Settings, template: str, report: BaseModel, output: Path) -> Path:
    html = render_template(
        settings.templates_dir / REPORT_TEMPLATE_DIR, template, {"report": report}
    )
    return write_pdf(html, output)


def build_sales_report(audit_id: UUID, repositories: Repositories, settings: Settings) -> Path:
    """Traffic lights, competitor comparison, top findings in plain language, forecast, offer.

    Args:
        audit_id: Audit to report.
        repositories: Data access.
        settings: Work directory, catalogue, forecast curves and template paths.

    Returns:
        PDF at <work_dir>/berichte/<audit_id>/Sichtbarkeitsbericht_<customer>_<date>.pdf.

    Raises:
        StorageError: If audit data cannot be loaded.
        RenderError: If a template or chart fails.
        ConfigurationError: If catalogue or forecast curves are malformed.
    """
    with tempfile.TemporaryDirectory(prefix="radar-report-") as scratch:
        report = sales_report_context(audit_id, repositories, settings, Path(scratch))
        output = _report_path(
            settings, audit_id, SALES_FILE_PREFIX, report.customer_name, report.report_date
        )
        return _render(settings, SALES_TEMPLATE, report, output)


def _score_trend(before: Decimal | None, after: Decimal | None) -> Trend:
    if before is None or after is None or before == after:
        return "same"
    return "up" if after > before else "down"


def _position_trend(before: int | None, after: int | None) -> Trend:
    if before == after:
        return "same"
    if after is None:
        return "down"
    if before is None or after < before:
        return "up"
    return "down"


def score_changes(
    before: list[CheckResult], after: list[CheckResult], catalogue: Catalogue
) -> list[ScoreChange]:
    """Area scores before and after for every area scored in either audit."""
    old = area_scores(before, catalogue)
    new = area_scores(after, catalogue)
    return [
        ScoreChange(
            label=area.name,
            before=old.get(area.id),
            after=new.get(area.id),
            trend=_score_trend(old.get(area.id), new.get(area.id)),
        )
        for area in catalogue.areas
        if area.id in old or area.id in new
    ]


def ranking_changes(
    keywords: list[Keyword], before: list[Ranking], after: list[Ranking]
) -> list[RankingChange]:
    """Customer positions per keyword before and after; unmeasured keywords are left out."""
    old = best_positions(before)
    new = best_positions(after)
    changes: list[RankingChange] = []
    for keyword in keywords:
        if keyword.id not in old and keyword.id not in new:
            continue
        organic_before, local_before = old.get(keyword.id, (None, None))
        organic_after, local_after = new.get(keyword.id, (None, None))
        changes.append(
            RankingChange(
                phrase=keyword.phrase,
                organic_before=organic_before,
                organic_after=organic_after,
                organic_trend=_position_trend(organic_before, organic_after),
                local_before=local_before,
                local_after=local_after,
                local_trend=_position_trend(local_before, local_after),
            )
        )
    return changes


def resolved_findings(previous: list[Finding], current: list[CheckResult]) -> list[Finding]:
    """Findings of the previous audit whose check passes now."""
    passed = {r.check_id for r in current if r.outcome is CheckOutcome.PASSED}
    return [finding for finding in previous if finding.check_id in passed]


def score_history(audits: list[Audit], current: Audit) -> list[tuple[str, Decimal]]:
    """Total score of every reported audit plus the current one, oldest first."""
    shown = {audit.id: audit for audit in audits if audit.status is AuditStatus.REPORTED}
    shown[current.id] = current
    ordered = sorted(shown.values(), key=lambda audit: audit.requested_at)
    return [
        (german_date(audit.requested_at), audit.total_score)
        for audit in ordered
        if audit.total_score is not None
    ]


def monthly_report_context(
    audit_id: UUID, repositories: Repositories, settings: Settings, work_dir: Path
) -> MonthlyReport:
    """Load the current and the previous audit and draw the comparison charts.

    Args:
        audit_id: Current audit.
        repositories: Data access.
        settings: Catalogue and template paths.
        work_dir: Scratch folder for downloads and chart images.

    Returns:
        The filled report model.

    Raises:
        RenderError: If there is no previous reported audit or a chart cannot be written.
        StorageError: If audit data cannot be loaded.
    """
    audit = repositories.audit(audit_id)
    previous_audit = repositories.previous_audit(audit)
    if previous_audit is None:
        raise RenderError(f"audit {audit_id} has no previous reported audit to compare with")
    customer = repositories.customer(audit.customer_id)
    keywords = repositories.keywords(audit.customer_id)
    catalogue = load_catalogue(settings.catalogue_path)
    labels = load_labels(settings.templates_dir)
    current = _bundle(repositories, audit)
    previous = _bundle(repositories, previous_audit)
    history = score_history(repositories.audits_of_customer(audit.customer_id), audit)
    files = _trend_charts(history, work_dir, labels)
    total = _total_change(previous, current, catalogue)
    raw = load_raw(repositories, audit, work_dir)
    return MonthlyReport(
        customer_name=customer.name,
        report_date=_today(),
        previous_date=previous_audit.requested_at.date(),
        total=total,
        areas=score_changes(previous.results, current.results, catalogue),
        rankings=ranking_changes(keywords, previous.rankings, current.rankings),
        done=finding_lines(
            resolved_findings(previous.findings, current.results), previous.results, catalogue
        ),
        next_steps=finding_lines(current.findings, current.results, catalogue),
        texts=load_texts(repositories, audit_id, work_dir),
        charts=files,
        profile=profile_lines(raw, labels),
    )


def _trend_charts(
    history: list[tuple[str, Decimal]], work_dir: Path, labels: ReportLabels
) -> ChartFiles:
    files = ChartFiles()
    if history:
        files.trend = charts.score_trend(
            history, work_dir / "verlauf.png", title=labels.charts.score_trend
        ).as_uri()
    return files


def _total_change(previous: AuditBundle, current: AuditBundle, catalogue: Catalogue) -> ScoreChange:
    before = _total(previous.audit, previous.results, catalogue)
    after = _total(current.audit, current.results, catalogue)
    return ScoreChange(label="", before=before, after=after, trend=_score_trend(before, after))


def build_monthly_report(audit_id: UUID, repositories: Repositories, settings: Settings) -> Path:
    """Compare with the previous audit: what improved, what was done, what comes next.

    Args:
        audit_id: Current audit.
        repositories: Data access.
        settings: Work directory, catalogue and template paths.

    Returns:
        PDF at <work_dir>/berichte/<audit_id>/Monatsbericht_<customer>_<date>.pdf.

    Raises:
        RenderError: If there is no previous audit or a template or chart fails.
        StorageError: If audit data cannot be loaded.
    """
    with tempfile.TemporaryDirectory(prefix="radar-report-") as scratch:
        report = monthly_report_context(audit_id, repositories, settings, Path(scratch))
        output = _report_path(
            settings, audit_id, MONTHLY_FILE_PREFIX, report.customer_name, report.report_date
        )
        return _render(settings, MONTHLY_TEMPLATE, report, output)
