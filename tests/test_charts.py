"""Tests for the report charts.

Context: Real matplotlib rendering into a temporary folder. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from conftest import REPORT_SECOND_KEYWORD_ID
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

from radarauditor.db.models import Ranking
from radarauditor.errors import RenderError
from radarauditor.reporting import charts

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
KEYWORD_ID = UUID("00000000-0000-4000-8000-000000000101")


def is_png(path: Path) -> bool:
    """Whether the file exists and starts with the PNG signature."""
    return path.is_file() and path.read_bytes().startswith(PNG_SIGNATURE)


def test_area_traffic_lights_writes_png(tmp_path: Path) -> None:
    """One bar per area, written as PNG into a new folder."""
    output = tmp_path / "neu" / "bereiche.png"

    result = charts.area_traffic_lights(
        {"a": Decimal("85"), "b": Decimal("55"), "c": Decimal("12")},
        output,
        area_names={"a": "Technik", "b": "Struktur"},
        title="Punkte je Bereich",
    )

    assert result == output
    assert is_png(output)


def test_area_traffic_lights_need_scores(tmp_path: Path) -> None:
    """No scores, no chart."""
    with pytest.raises(RenderError, match="at least one area"):
        charts.area_traffic_lights({}, tmp_path / "x.png", area_names={}, title="T")


@pytest.mark.parametrize(
    ("score", "colour"),
    [
        (Decimal("39.99"), charts.BAD),
        (Decimal(40), charts.WARN),
        (69.9, charts.WARN),
        (70, charts.OK),
    ],
)
def test_score_colour_thresholds(score: Decimal | float, colour: str) -> None:
    """Below 40 bad, below 70 warn, else ok."""
    assert charts.score_colour(score) == colour


@pytest.mark.parametrize(
    ("position", "colour"),
    [(1, charts.OK), (3, charts.OK), (4, charts.WARN), (10, charts.WARN), (11, charts.BAD)],
)
def test_position_colour_thresholds(position: int, colour: str) -> None:
    """1-3 ok, 4-10 warn, worse bad."""
    assert charts.position_colour(position) == colour


def test_missing_position_is_bad() -> None:
    """Not listed counts as bad."""
    assert charts.position_colour(None) == charts.BAD


def test_competitor_comparison_draws_at_most_four_subjects(tmp_path: Path) -> None:
    """Five subjects are accepted; the chart is written."""
    values = {
        name: {"Bewertungen": float(index * 10), "Sterne": 4.5, "Fotos": 3.0}
        for index, name in enumerate(["Sie", "A", "B", "C", "D"])
    }

    assert is_png(charts.competitor_comparison(values, tmp_path / "w.png", title="Vergleich"))


def test_competitor_comparison_needs_metrics(tmp_path: Path) -> None:
    """Subjects without metrics cannot be compared."""
    with pytest.raises(RenderError, match="at least one subject"):
        charts.competitor_comparison({"Sie": {}}, tmp_path / "w.png", title="Vergleich")
    with pytest.raises(RenderError, match="at least one subject"):
        charts.competitor_comparison({}, tmp_path / "w.png", title="Vergleich")


def test_grid_map_with_coordinates(tmp_path: Path) -> None:
    """A 2x2 grid keyed by coordinates is drawn."""
    points = {"51.95,7.62": 1, "51.95,7.63": None, "51.96,7.62": 5, "51.96,7.63": 14}

    assert is_png(charts.grid_map(points, tmp_path / "karte.png", title="Karte"))


def test_grid_map_with_plain_keys_keeps_order(tmp_path: Path) -> None:
    """Keys that are not coordinates are drawn in the given order."""
    assert charts._grid_order({"x": 1, "y": 2, "z": 3, "w": None}) == [1, 2, 3, None]
    assert is_png(charts.grid_map({"1": 1}, tmp_path / "karte.png", title="Karte"))


def test_grid_order_puts_north_west_first() -> None:
    """Rows run from north to south, columns from west to east."""
    points = {"51.0,7.0": 3, "51.0,7.1": 4, "51.1,7.0": 1, "51.1,7.1": 2}

    assert charts._grid_order(points) == [1, 2, 3, 4]


def test_grid_order_sorts_row_and_column_keys() -> None:
    """Keys r{row}c{column} are ordered by row (north first), then column, numerically."""
    points = {"r2c1": 3, "r1c10": 9, "r1c2": 2, "r2c2": 4, "r1c1": 1}

    assert charts._grid_order(points) == [1, 2, 9, 3, 4]


def test_grid_map_needs_square_number_of_points(tmp_path: Path) -> None:
    """Three points cannot form a square."""
    with pytest.raises(RenderError, match="square number"):
        charts.grid_map({"a": 1, "b": 2, "c": 3}, tmp_path / "karte.png", title="Karte")


def test_score_trend_writes_png(tmp_path: Path) -> None:
    """A line over three audits."""
    history = [
        ("01.08.2026", Decimal(45)),
        ("01.09.2026", Decimal(52)),
        ("01.10.2026", Decimal(61)),
    ]

    assert is_png(charts.score_trend(history, tmp_path / "verlauf.png", title="Verlauf"))


def test_score_trend_needs_history(tmp_path: Path) -> None:
    """No audits, no trend."""
    with pytest.raises(RenderError, match="at least one audit"):
        charts.score_trend([], tmp_path / "verlauf.png", title="Verlauf")


def test_ranking_table_writes_png(tmp_path: Path, report_rankings: list[Ranking]) -> None:
    """Customer and competitor columns for the known keywords."""
    output = charts.ranking_table_image(
        report_rankings,
        tmp_path / "plaetze.png",
        keyword_phrases={KEYWORD_ID: "Dachdecker Münster"},
        subject_labels={"customer": "Sie"},
        title="Plätze",
        legend="Suche / Karte",
    )

    assert is_png(output)


def test_ranking_table_needs_known_keywords(
    tmp_path: Path, report_make_ranking: Callable[..., Ranking]
) -> None:
    """Grid rows and unknown keywords are not enough for a table."""
    rows = [report_make_ranking(KEYWORD_ID, "customer", 1, 1, "51.9,7.6")]

    with pytest.raises(RenderError, match="known keyword"):
        charts.ranking_table_image(
            rows,
            tmp_path / "plaetze.png",
            keyword_phrases={KEYWORD_ID: "Dachdecker Münster"},
            subject_labels={},
            title="Plätze",
            legend="Suche / Karte",
        )


def test_unwritable_target_is_a_render_error(tmp_path: Path) -> None:
    """A file in place of the folder makes saving fail with RenderError."""
    blocker = tmp_path / "blockiert"
    blocker.write_text("x", encoding="utf-8")

    with pytest.raises(RenderError, match="write chart"):
        charts.score_trend([("01.10.2026", Decimal(1))], blocker / "verlauf.png", title="T")


@pytest.fixture
def drawn(monkeypatch: pytest.MonkeyPatch) -> list[Figure]:
    """Figures handed to the title step, so a test can read what was drawn."""
    figures: list[Figure] = []
    add_title = charts._title

    def capture(figure: Figure, title: str) -> None:
        figures.append(figure)
        add_title(figure, title)

    monkeypatch.setattr(charts, "_title", capture)
    return figures


def test_ranking_table_draws_headings_phrases_and_coloured_cells(
    tmp_path: Path,
    report_rankings: list[Ranking],
    report_make_ranking: Callable[..., Ranking],
    drawn: list[Figure],
) -> None:
    """Customer first, long headings wrapped, long phrases shortened, missing cells as dashes."""
    rows = [
        *report_rankings,
        report_make_ranking(REPORT_SECOND_KEYWORD_ID, "Dach Weber", None, None),
    ]

    charts.ranking_table_image(
        rows,
        tmp_path / "plaetze.png",
        keyword_phrases={
            KEYWORD_ID: "Dachdecker Münster",
            REPORT_SECOND_KEYWORD_ID: "Dachdecker Notdienst Münster und Umgebung rund um die Uhr",
        },
        subject_labels={
            "customer": "Ihr Betrieb",
            "Dach Schmidt": "Dachdeckerei Schmidt und Söhne GmbH",
        },
        title="Plätze",
        legend="Suche / Karte",
    )

    axes = drawn[0].axes[0]
    assert [(text.get_text(), text.get_color(), text.get_fontweight()) for text in axes.texts] == [
        ("Suche / Karte", charts.TEXT_MUTED, "normal"),
        ("Ihr Betrieb", charts.GOLD, "normal"),
        ("Dachdeckerei\nSchmidt und\nSöhne GmbH", charts.TEXT, "normal"),
        ("Dach Weber", charts.TEXT, "normal"),
        ("Dachdecker Münster", charts.TEXT, "normal"),
        ("2 / 1", charts.OK, "bold"),
        ("4 / 2", charts.OK, "bold"),
        ("– / –", charts.BAD, "bold"),
        ("Dachdecker Notdienst Münster und…", charts.TEXT, "normal"),
        ("5 / –", charts.WARN, "bold"),
        ("1 / 1", charts.OK, "bold"),
        ("– / –", charts.BAD, "bold"),
    ]
    centres = [0, 0.4667, 0.68, 0.8933]
    assert [(round(text.get_position()[0], 4), text.get_position()[1]) for text in axes.texts] == [
        (0, 0),
        *((centre, 1.1) for centre in centres[1:]),
        *((centre, 2) for centre in centres),
        *((centre, 3) for centre in centres),
    ]
    assert [line.get_ydata()[0] for line in axes.get_lines()] == [1.5, 2.5]
    assert (axes.get_xlim(), axes.get_ylim()) == ((0, 1), (3.5, -0.5))


def test_grid_map_draws_one_coloured_cell_per_point(tmp_path: Path, drawn: list[Figure]) -> None:
    """Row 1 on top, a dash for not visible, cell colour by position."""
    points = {"r1c1": 1, "r1c2": None, "r2c1": 5, "r2c2": 14}

    charts.grid_map(points, tmp_path / "karte.png", title="Karte")

    axes = drawn[0].axes[0]
    assert [(text.get_text(), text.get_position()) for text in axes.texts] == [
        ("1", (0.5, 1.5)),
        ("–", (1.5, 1.5)),
        ("5", (0.5, 0.5)),
        ("14", (1.5, 0.5)),
    ]
    assert [(patch.get_xy(), to_hex(patch.get_facecolor())) for patch in axes.patches] == [
        ((0.06, 1.06), charts.OK.lower()),
        ((1.06, 1.06), charts.BAD.lower()),
        ((0.06, 0.06), charts.WARN.lower()),
        ((1.06, 0.06), charts.BAD.lower()),
    ]
    assert (axes.get_xlim(), axes.get_ylim(), axes.get_aspect()) == ((0, 2), (0, 2), 1.0)
