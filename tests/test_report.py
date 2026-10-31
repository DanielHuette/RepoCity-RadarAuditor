"""Tests for the sales and monthly reports.

Context: Real Jinja2 and WeasyPrint rendering against a mocked repository. | Input: Fixtures. |
Output: Assertions.
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.config import Settings
from radarauditor.db.models import (
    Audit,
    AuditStatus,
    CheckOutcome,
    CheckResult,
    Finding,
    Keyword,
    Ranking,
)
from radarauditor.errors import RenderError
from radarauditor.rendering import render_template
from radarauditor.reporting import report
from radarauditor.reporting.texts import ReportTexts
from radarauditor.scoring.catalog import Catalogue

AUDIT_ID = UUID("00000000-0000-4000-8000-000000000002")
PREVIOUS_AUDIT_ID = UUID("00000000-0000-4000-8000-000000000004")
SALES_HEADINGS = [
    "So sichtbar sind Sie heute",
    "Ihre Wettbewerber im Vergleich",
    "Die drei Befunde, die Sie am meisten kosten",
    "Alle Befunde",
    "Was eine Optimierung bringen kann",
    "Warum eine laufende Betreuung",
    "Ihr Angebot",
]


def sales_html(repositories: MagicMock, settings: Settings, scratch: Path) -> str:
    """Render the sales template the way build_sales_report does."""
    context = report.sales_report_context(AUDIT_ID, repositories, settings, scratch)
    return render_template(settings.templates_dir / "report", "sales.html", {"report": context})


def test_sales_report_is_a_pdf_named_after_customer_and_date(
    report_repositories: MagicMock, report_settings: Settings
) -> None:
    """The PDF lands in berichte/<audit_id>/ and is a real PDF."""
    pdf = report.build_sales_report(AUDIT_ID, report_repositories, report_settings)

    assert pdf.parent == report_settings.radar_work_dir / "berichte" / str(AUDIT_ID)
    assert pdf.name.startswith("Sichtbarkeitsbericht_Dachdeckerei_Muster_")
    assert pdf.read_bytes().startswith(b"%PDF")


def test_sales_html_has_every_section_in_order(
    report_repositories: MagicMock,
    report_settings: Settings,
    report_texts: ReportTexts,
    tmp_path: Path,
) -> None:
    """Sections follow the agreed order and the prose from texte.json is used."""
    html = sales_html(report_repositories, report_settings, tmp_path)

    positions = [html.index(heading) for heading in SALES_HEADINGS]
    assert positions == sorted(positions)
    assert report_texts.offer_text in html
    assert report_texts.subscription_reason in html
    assert "RepoCity · Daniel Hütte · Münster · kontakt@speedofthespirit.dev" in html
    assert "keine Zusage" in html


def test_sales_context_draws_every_chart(
    report_repositories: MagicMock, report_settings: Settings, tmp_path: Path
) -> None:
    """All four sales charts exist as PNG files."""
    context = report.sales_report_context(AUDIT_ID, report_repositories, report_settings, tmp_path)

    uris = [context.charts.areas, context.charts.competitors, context.charts.grid]
    uris.append(context.charts.rankings)
    assert all(uri and uri.startswith("file://") for uri in uris)
    assert all(Path(uri.removeprefix("file://")).read_bytes()[:4] == b"\x89PNG" for uri in uris)


def test_top_findings_put_severity_three_first(
    report_repositories: MagicMock, report_settings: Settings, tmp_path: Path
) -> None:
    """At most three findings, most severe first."""
    context = report.sales_report_context(AUDIT_ID, report_repositories, report_settings, tmp_path)

    severities = [finding.severity for finding in context.top_findings]
    assert len(severities) <= 3
    assert severities == sorted(severities, reverse=True)
    assert severities[0] == 3


def test_missing_texts_fall_back_to_standard_sentences(
    report_repositories: MagicMock,
    report_settings: Settings,
    report_storage: dict[str, bytes],
    tmp_path: Path,
) -> None:
    """Without texte.json the report still renders with neutral sentences."""
    del report_storage[f"raw/{AUDIT_ID}/texte.json"]

    html = sales_html(report_repositories, report_settings, tmp_path)

    assert "Wir haben" in html
    assert "Ein schriftliches Angebot mit festen Preisen" in html


def test_invalid_texts_fall_back_to_standard_sentences(
    report_repositories: MagicMock,
    report_settings: Settings,
    report_storage: dict[str, bytes],
    tmp_path: Path,
) -> None:
    """A texte.json without required fields is ignored, not fatal."""
    report_storage[f"raw/{AUDIT_ID}/texte.json"] = b'{"summary": ""}'

    context = report.sales_report_context(AUDIT_ID, report_repositories, report_settings, tmp_path)

    assert context.texts is None


def test_report_renders_without_raw_data_and_rankings(
    report_repositories: MagicMock,
    report_settings: Settings,
    report_storage: dict[str, bytes],
    tmp_path: Path,
) -> None:
    """No raw data and no ranking reader: comparison text instead of charts, still a PDF."""
    del report_storage[f"raw/{AUDIT_ID}/raw.json"]
    del report_repositories.rankings

    context = report.sales_report_context(AUDIT_ID, report_repositories, report_settings, tmp_path)
    pdf = report.build_sales_report(AUDIT_ID, report_repositories, report_settings)

    assert context.charts.competitors is None
    assert context.charts.rankings is None
    assert context.forecast.keywords_counted == 0
    assert pdf.read_bytes().startswith(b"%PDF")


def test_sales_report_shows_profile_figures_and_keyword_gap(
    report_repositories: MagicMock, report_settings: Settings, tmp_path: Path
) -> None:
    """With access and paid data the profile table and the gap table are in the report."""
    context = report.sales_report_context(AUDIT_ID, report_repositories, report_settings, tmp_path)
    html = render_template(
        report_settings.templates_dir / "report", "sales.html", {"report": context}
    )

    labels = [line.label for line in context.profile]
    assert labels[:2] == [
        "Aufrufe in der Google-Suche auf dem Handy",
        "Aufrufe in der Google-Suche am Computer",
    ]
    calls = next(line for line in context.profile if line.label == "Anrufe")
    assert (calls.value, calls.previous, calls.change_percent, calls.trend) == (42, 35, 20.0, "up")
    bookings = next(line for line in context.profile if line.label == "Buchungen")
    assert (bookings.change_percent, bookings.trend) == (None, "same")
    assert html.index("So sichtbar sind Sie heute") < html.index(
        "Was Ihr Google-Profil heute bringt"
    )
    assert "+20 %" in html
    assert [keyword.phrase for keyword in context.keyword_gap] == [
        "dachrinne reinigen münster",
        "gaubenverkleidung münster",
    ]
    assert "Suchbegriffe, bei denen Ihre Wettbewerber gefunden werden und Sie nicht" in html
    assert "dach-schmidt.de" in html


def test_sales_report_leaves_out_profile_and_gap_without_data(
    report_repositories: MagicMock,
    report_settings: Settings,
    report_storage: dict[str, bytes],
    raw_audit_full: RawAudit,
    tmp_path: Path,
) -> None:
    """A remote audit without paid data renders without both sections."""
    plain = raw_audit_full.model_copy(update={"access": None, "paid": None})
    report_storage[f"raw/{AUDIT_ID}/raw.json"] = plain.model_dump_json().encode()

    html = sales_html(report_repositories, report_settings, tmp_path)
    pdf = report.build_sales_report(AUDIT_ID, report_repositories, report_settings)

    assert "Was Ihr Google-Profil heute bringt" not in html
    assert "und Sie nicht" not in html
    assert pdf.read_bytes().startswith(b"%PDF")


def test_profile_lines_follow_the_labels(raw_audit_full: RawAudit) -> None:
    """Only labelled metrics are shown, a figure new since the last period has no change."""
    labels = report.load_labels(Path(__file__).resolve().parents[1] / "templates")
    labels = labels.model_copy(update={"profile_metrics": {"CALL_CLICKS": "Anrufe", "X": "Fehlt"}})
    assert raw_audit_full.access is not None
    access = raw_audit_full.access.model_copy(
        update={"profile_metrics_previous": {"CALL_CLICKS": 0}}
    )
    raw = raw_audit_full.model_copy(update={"access": access})

    lines = report.profile_lines(raw, labels)

    assert [(line.label, line.change_percent, line.trend) for line in lines] == [
        ("Anrufe", None, "up")
    ]
    assert report.profile_lines(None, labels) == []
    assert report.gap_keywords(raw_audit_full.model_copy(update={"paid": None})) == []


def test_audit_rankings_uses_the_reader_when_present(
    report_repositories: MagicMock, report_rankings: list[Ranking]
) -> None:
    """The ranking reader is called with the audit id."""
    assert report.audit_rankings(report_repositories, AUDIT_ID) == report_rankings
    report_repositories.rankings.assert_called_once_with(AUDIT_ID)


def test_load_labels_rejects_missing_file(tmp_path: Path) -> None:
    """A template folder without labels.yaml is a render error."""
    with pytest.raises(RenderError, match=r"labels\.yaml"):
        report.load_labels(tmp_path)


def test_grid_points_prefer_the_primary_keyword(
    report_keywords: list[Keyword], report_rankings: list[Ranking]
) -> None:
    """Nine grid points of the primary keyword are returned."""
    points = report.grid_points(report_keywords, report_rankings)

    assert len(points) == 9
    assert None in points.values()


def test_group_findings_follows_catalogue_order(
    report_findings: list[Finding],
    report_check_results: list[CheckResult],
    catalogue: Catalogue,
) -> None:
    """Groups appear in catalogue order and hold every finding once."""
    lines = report.finding_lines(report_findings, report_check_results, catalogue)
    groups = report.group_findings(lines, catalogue)
    area_order = [area.name for area in catalogue.areas]

    assert [area_order.index(group.area_name) for group in groups] == sorted(
        area_order.index(group.area_name) for group in groups
    )
    assert sum(len(group.findings) for group in groups) == len(report_findings)


def _previous_audit(sample_audit: Audit) -> Audit:
    return sample_audit.model_copy(
        update={
            "id": PREVIOUS_AUDIT_ID,
            "status": AuditStatus.REPORTED,
            "requested_at": sample_audit.requested_at - timedelta(days=30),
            "total_score": Decimal("48.00"),
        }
    )


@pytest.fixture
def monthly_repositories(
    report_repositories: MagicMock,
    sample_audit: Audit,
    report_check_results: list[CheckResult],
    report_findings: list[Finding],
    report_rankings: list[Ranking],
    report_make_ranking: object,
) -> MagicMock:
    """The report repositories with a previous audit that failed one check now passing."""
    previous = _previous_audit(sample_audit)
    passed = next(r for r in report_check_results if r.outcome is CheckOutcome.PASSED)
    previous_results = [
        result.model_copy(update={"audit_id": PREVIOUS_AUDIT_ID, "outcome": CheckOutcome.FAILED})
        if result.check_id == passed.check_id
        else result.model_copy(update={"audit_id": PREVIOUS_AUDIT_ID})
        for result in report_check_results
    ]
    previous_finding = Finding(
        id=uuid4(),
        audit_id=PREVIOUS_AUDIT_ID,
        check_id=passed.check_id,
        severity=3,
        title="Behobener Befund",
        explanation="Gemessen vorher",
        customer_impact="Kostete Anfragen",
        fix="Behobene Maßnahme",
        fix_payload=None,
    )
    previous_rankings = [
        ranking.model_copy(update={"organic_position": 7, "audit_id": PREVIOUS_AUDIT_ID})
        for ranking in report_rankings
        if ranking.grid_point is None
    ]
    by_audit = {
        AUDIT_ID: (report_check_results, report_findings, report_rankings),
        PREVIOUS_AUDIT_ID: (previous_results, [previous_finding], previous_rankings),
    }
    report_repositories.previous_audit.return_value = previous
    report_repositories.audits_of_customer.return_value = [previous, sample_audit]
    report_repositories.check_results.side_effect = lambda audit_id: by_audit[audit_id][0]
    report_repositories.findings_of_audit.side_effect = lambda audit_id: by_audit[audit_id][1]
    report_repositories.rankings = MagicMock(side_effect=lambda audit_id: by_audit[audit_id][2])
    return report_repositories


def test_monthly_report_is_a_pdf(
    monthly_repositories: MagicMock, report_settings: Settings
) -> None:
    """The monthly PDF is written next to the sales report."""
    pdf = report.build_monthly_report(AUDIT_ID, monthly_repositories, report_settings)

    assert pdf.name.startswith("Monatsbericht_Dachdeckerei_Muster_")
    assert pdf.read_bytes().startswith(b"%PDF")


def test_monthly_context_compares_both_audits(
    monthly_repositories: MagicMock, report_settings: Settings, tmp_path: Path
) -> None:
    """Total rises, the fixed finding counts as done, rankings improved, trend chart drawn."""
    context = report.monthly_report_context(
        AUDIT_ID, monthly_repositories, report_settings, tmp_path
    )
    html = render_template(
        report_settings.templates_dir / "report", "monthly.html", {"report": context}
    )

    assert context.total.before == Decimal("48.00")
    assert context.total.trend == "up"
    assert [finding.title for finding in context.done] == ["Behobener Befund"]
    assert {change.organic_trend for change in context.rankings} == {"up"}
    assert context.charts.trend is not None
    assert "Das haben wir für Sie erledigt" in html
    assert "Das nehmen wir uns als Nächstes vor" in html
    assert "Wir versprechen diese Arbeit, keine bestimmte Platzierung" in html
    assert "▲" in html
    assert "Was Ihr Google-Profil heute bringt" in html
    assert len(context.profile) == 9


def test_monthly_report_needs_a_previous_audit(
    report_repositories: MagicMock, report_settings: Settings
) -> None:
    """Without a reported predecessor there is nothing to compare."""
    with pytest.raises(RenderError, match="no previous reported audit"):
        report.build_monthly_report(AUDIT_ID, report_repositories, report_settings)


def test_ranking_changes_cover_lost_and_new_positions(
    report_keywords: list[Keyword], report_make_ranking: object
) -> None:
    """A lost position points down, a new one up, an unchanged one sideways."""
    make = report_make_ranking
    assert callable(make)
    primary, second, _ = report_keywords
    before = [make(primary.id, "customer", 3, None), make(second.id, "customer", None, 2)]
    after = [make(primary.id, "customer", None, None), make(second.id, "customer", 4, 2)]

    changes = report.ranking_changes(report_keywords, before, after)

    assert [(c.organic_trend, c.local_trend) for c in changes] == [
        ("down", "same"),
        ("up", "same"),
    ]


def test_score_history_keeps_reported_audits_and_the_current_one(sample_audit: Audit) -> None:
    """Failed audits and audits without score are left out."""
    previous = _previous_audit(sample_audit)
    failed = previous.model_copy(update={"id": uuid4(), "status": AuditStatus.FAILED})

    history = report.score_history([failed, previous], sample_audit)

    assert [score for _, score in history] == [Decimal("48.00"), Decimal("61.50")]
    assert history[0][0] == "01.09.2026"


def test_check_order_is_numeric_within_an_area() -> None:
    """b2 sorts before b10, areas stay alphabetical."""
    assert sorted(["b10", "a3", "b2", "a12"], key=report.check_order) == ["a3", "a12", "b2", "b10"]
