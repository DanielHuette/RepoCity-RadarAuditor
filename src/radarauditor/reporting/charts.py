"""Draw the report charts with matplotlib in the RepoCity palette.

Context: Static images embedded in the PDF; the app draws its own interactive charts. Figures are
built without pyplot on the Agg canvas, so drawing keeps no global state between charts. Titles and
labels come from the caller, who reads them from templates/report/labels.yaml.
Input: Scores, rankings, competitor data.
Output: PNG files.
"""

import logging
import math
import re
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import matplotlib as mpl
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from radarauditor.db.models import Ranking
from radarauditor.errors import RenderError
from radarauditor.rendering import MISSING_VALUE, german_number

logger = logging.getLogger(__name__)

NIGHT = "#04060B"
NIGHT_RAISED = "#0B1222"
TEXT = "#F3F1EA"
TEXT_MUTED = "#A9A79F"
GOLD = "#DCB25C"
OK = "#4FBF7A"
WARN = "#E0A83A"
BAD = "#D8584D"
COMPETITOR_SHADES = ("#A9A79F", "#7D7B75", "#5A5955")
FONT_FAMILY = "DejaVu Sans"
BASE_DPI = 96
DPI = 2 * BASE_DPI
WIDTH_INCHES = 7.0
SCORE_MAX = 100
BAD_BELOW = 40
WARN_BELOW = 70
TOP_POSITION_MAX = 3
GOOD_POSITION_MAX = 10
MAX_SUBJECTS = 4
CUSTOMER_SUBJECT = "customer"
TITLE_SIZE = 13
KEYWORD_COLUMN_SHARE = 0.36
HEADER_LINES = 2
TABLE_LINE_INCHES = 0.36
HEADING_WRAP = 14
PHRASE_WIDTH = 34
GRID_INCHES = 4.4
LABEL_SIZE = 9
GRID_KEY_PATTERN = re.compile(r"r(\d+)c(\d+)")


def score_colour(score: Decimal | float) -> str:
    """Traffic-light colour of a 0-100 score: below 40 bad, below 70 warn, else ok."""
    if score < BAD_BELOW:
        return BAD
    if score < WARN_BELOW:
        return WARN
    return OK


def position_colour(position: int | None) -> str:
    """Colour of a search position: 1-3 ok, 4-10 warn, worse or missing bad."""
    if position is None or position > GOOD_POSITION_MAX:
        return BAD
    if position > TOP_POSITION_MAX:
        return WARN
    return OK


@contextmanager
def _figure(
    height_inches: float, output: Path, width_inches: float = WIDTH_INCHES
) -> Iterator[Figure]:
    """Yield a styled figure and save it as PNG when the block ends."""
    with mpl.rc_context(
        {
            "font.family": FONT_FAMILY,
            "text.color": TEXT,
            "axes.labelcolor": TEXT,
            "xtick.color": TEXT_MUTED,
            "ytick.color": TEXT,
            "axes.edgecolor": TEXT_MUTED,
        }
    ):
        figure = Figure(
            figsize=(width_inches, height_inches), facecolor=NIGHT, layout="constrained"
        )
        FigureCanvasAgg(figure)
        yield figure
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            figure.savefig(output, format="png", dpi=DPI, facecolor=NIGHT, bbox_inches="tight")
        except OSError as exc:
            raise RenderError(f"write chart {output} failed: {exc}") from exc
    logger.debug("Chart written to %s", output)


def _plain_axes(axes: Axes) -> None:
    """Remove frame and ticks that carry no information."""
    axes.set_facecolor(NIGHT)
    for spine in axes.spines.values():
        spine.set_visible(False)
    axes.tick_params(length=0)


def _title(figure: Figure, title: str) -> None:
    figure.suptitle(title, x=0.02, ha="left", fontsize=TITLE_SIZE, fontweight="bold", color=TEXT)


def area_traffic_lights(
    area_scores: dict[str, Decimal],
    output: Path,
    *,
    area_names: dict[str, str],
    title: str,
) -> Path:
    """Horizontal bars per area coloured red, amber, green by score.

    Args:
        area_scores: Score 0-100 per area id, in display order.
        output: PNG path.
        area_names: German area name per area id; the id is shown where a name is missing.
        title: Chart title.

    Returns:
        The output path.

    Raises:
        RenderError: If there are no scores or the file cannot be written.
    """
    if not area_scores:
        raise RenderError("area traffic lights need at least one area score")
    labels = [area_names.get(area_id, area_id) for area_id in area_scores]
    scores = [float(score) for score in area_scores.values()]
    rows = range(len(scores))
    with _figure(0.42 * len(scores) + 0.9, output) as figure:
        axes = figure.add_subplot()
        _plain_axes(axes)
        axes.barh(
            rows, [SCORE_MAX] * len(scores), color=NIGHT_RAISED, height=0.62, edgecolor="none"
        )
        axes.barh(
            rows, scores, color=[score_colour(s) for s in scores], height=0.62, edgecolor="none"
        )
        for row, score in zip(rows, scores, strict=True):
            axes.text(SCORE_MAX + 2, row, german_number(score, 0), va="center", fontsize=LABEL_SIZE)
        axes.set_yticks(list(rows), labels, fontsize=LABEL_SIZE)
        axes.set_xticks([])
        axes.set_xlim(0, SCORE_MAX + 10)
        axes.invert_yaxis()
        _title(figure, title)
    return output


def competitor_comparison(values: dict[str, dict[str, float]], output: Path, *, title: str) -> Path:
    """Grouped bars: customer against up to three competitors on reviews, rating, photos.

    Each metric gets its own panel with its own scale, because review counts and star ratings
    cannot share an axis.

    Args:
        values: Metric values per subject; the first subject is the customer and drawn in gold.
            Only the first four subjects are drawn.
        output: PNG path.
        title: Chart title.

    Returns:
        The output path.

    Raises:
        RenderError: If no subject or no metric is given, or the file cannot be written.
    """
    subjects = list(values)[:MAX_SUBJECTS]
    metrics = list(values[subjects[0]]) if subjects else []
    if not metrics:
        raise RenderError("competitor comparison needs at least one subject and one metric")
    colours = [GOLD, *COMPETITOR_SHADES][: len(subjects)]
    with _figure(0.5 * len(subjects) + 1.6, output) as figure:
        panels = figure.subplots(1, len(metrics), squeeze=False)[0]
        for index, (axes, metric) in enumerate(zip(panels, metrics, strict=True)):
            _plain_axes(axes)
            numbers = [values[subject].get(metric, 0.0) for subject in subjects]
            rows = range(len(subjects))
            axes.barh(rows, numbers, color=colours, height=0.62, edgecolor="none")
            peak = max(numbers) or 1.0
            for row, number in zip(rows, numbers, strict=True):
                places = 0 if float(number).is_integer() else 1
                axes.text(
                    number + peak * 0.03,
                    row,
                    german_number(number, places),
                    va="center",
                    fontsize=LABEL_SIZE,
                )
            axes.set_xlim(0, peak * 1.3)
            axes.set_xticks([])
            axes.set_yticks(list(rows), subjects if index == 0 else [], fontsize=LABEL_SIZE)
            axes.set_title(metric, fontsize=LABEL_SIZE + 1, color=TEXT, loc="left")
            axes.invert_yaxis()
        _title(figure, title)
    return output


def _grid_cell(key: str) -> tuple[int, int] | None:
    match = GRID_KEY_PATTERN.fullmatch(key)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _grid_order(points: dict[str, int | None]) -> list[int | None]:
    """Positions row by row from north-west.

    Keys "r{row}c{column}" (row 1 north) and "lat,lng" are sorted; other keys keep their order.
    """
    cells = {key: _grid_cell(key) for key in points}
    if all(cell is not None for cell in cells.values()):
        return [points[key] for key in sorted(points, key=lambda key: cells[key] or (0, 0))]
    try:
        coordinates = {
            key: tuple(float(part) for part in key.split(",", maxsplit=1)) for key in points
        }
    except ValueError:
        return list(points.values())
    if any(len(pair) != 2 for pair in coordinates.values()):
        return list(points.values())
    ordered = sorted(points, key=lambda key: (-coordinates[key][0], coordinates[key][1]))
    return [points[key] for key in ordered]


def grid_map(points: dict[str, int | None], output: Path, *, title: str) -> Path:
    """Grid of map positions around the location; None drawn as not visible.

    Args:
        points: Local pack position per grid point, keyed "r{row}c{column}" as the ranking
            rows store them or "lat,lng"; other keys are drawn in the given order, row by row.
        output: PNG path.
        title: Chart title.

    Returns:
        The output path.

    Raises:
        RenderError: If the points do not form a square grid or the file cannot be written.
    """
    side = math.isqrt(len(points))
    if side == 0 or side * side != len(points):
        raise RenderError(f"grid map needs a square number of points, got {len(points)}")
    positions = _grid_order(points)
    with _figure(GRID_INCHES + 0.4, output, width_inches=GRID_INCHES) as figure:
        axes = figure.add_subplot()
        _plain_axes(axes)
        for index, position in enumerate(positions):
            row, column = divmod(index, side)
            _grid_cell_patch(axes, column, side - 1 - row, position)
        axes.set_xlim(0, side)
        axes.set_ylim(0, side)
        axes.set_aspect("equal")
        axes.set_xticks([])
        axes.set_yticks([])
        _title(figure, title)
    return output


def _grid_cell_patch(axes: Axes, column: int, y: int, position: int | None) -> None:
    """One square coloured by the position, labelled with it or with the missing mark."""
    axes.add_patch(
        Rectangle(
            (column + 0.06, y + 0.06),
            0.88,
            0.88,
            facecolor=position_colour(position),
            edgecolor="none",
        )
    )
    label = MISSING_VALUE if position is None else str(position)
    axes.text(
        column + 0.5,
        y + 0.5,
        label,
        ha="center",
        va="center",
        fontsize=LABEL_SIZE + 2,
        fontweight="bold",
        color=NIGHT,
    )


def score_trend(history: list[tuple[str, Decimal]], output: Path, *, title: str) -> Path:
    """Total score over past audits.

    Args:
        history: Date label and total score per audit, oldest first.
        output: PNG path.
        title: Chart title.

    Returns:
        The output path.

    Raises:
        RenderError: If the history is empty or the file cannot be written.
    """
    if not history:
        raise RenderError("score trend needs at least one audit")
    labels = [label for label, _ in history]
    scores = [float(score) for _, score in history]
    steps = list(range(len(scores)))
    with _figure(2.8, output) as figure:
        axes = figure.add_subplot()
        _plain_axes(axes)
        axes.plot(steps, scores, color=GOLD, linewidth=2, marker="o", markersize=7)
        for step, score in zip(steps, scores, strict=True):
            axes.annotate(
                german_number(score, 0),
                (step, score),
                textcoords="offset points",
                xytext=(0, 9),
                ha="center",
                fontsize=LABEL_SIZE,
            )
        axes.set_ylim(0, SCORE_MAX + 12)
        axes.set_yticks([])
        axes.set_xticks(steps, labels, fontsize=LABEL_SIZE)
        axes.set_xlim(-0.5, len(steps) - 0.5)
        axes.axhline(0, color=TEXT_MUTED, linewidth=0.6)
        _title(figure, title)
    return output


def _cell_text(organic: int | None, local: int | None) -> str:
    first = MISSING_VALUE if organic is None else str(organic)
    second = MISSING_VALUE if local is None else str(local)
    return f"{first} / {second}"


def _best_of(organic: int | None, local: int | None) -> int | None:
    present = [position for position in (organic, local) if position is not None]
    return min(present) if present else None


def ranking_table_image(
    rankings: list[Ranking],
    output: Path,
    *,
    keyword_phrases: dict[UUID, str],
    subject_labels: dict[str, str],
    title: str,
    legend: str,
) -> Path:
    """Positions per keyword for customer and competitors as a compact table.

    Each cell shows "organic / local pack" and is coloured by the better of the two. Grid
    measurements are left out; they belong to the grid map.

    Args:
        rankings: Rankings of one audit.
        output: PNG path.
        keyword_phrases: Phrase per keyword id; rankings of other keywords are ignored.
        subject_labels: Column heading per subject; "customer" is the first column.
        title: Chart title.
        legend: One line under the title explaining the cell format.

    Returns:
        The output path.

    Raises:
        RenderError: If no ranking matches a known keyword or the file cannot be written.
    """
    rows = [r for r in rankings if r.grid_point is None and r.keyword_id in keyword_phrases]
    if not rows:
        raise RenderError("ranking table needs at least one ranking of a known keyword")
    subjects = sorted({row.subject for row in rows}, key=lambda s: (s != CUSTOMER_SUBJECT, s))
    keywords = list(dict.fromkeys(row.keyword_id for row in rows))
    cells = {(row.keyword_id, row.subject): row for row in rows}
    column_width = (1 - KEYWORD_COLUMN_SHARE) / len(subjects)
    centres = [KEYWORD_COLUMN_SHARE + column_width * (i + 0.5) for i in range(len(subjects))]
    columns = list(zip(centres, subjects, strict=True))
    lines = len(keywords) + HEADER_LINES
    with _figure(TABLE_LINE_INCHES * lines + 0.6, output) as figure:
        axes = figure.add_subplot()
        _table_axes(axes, lines)
        axes.text(0, 0, legend, fontsize=LABEL_SIZE - 1, color=TEXT_MUTED, va="center")
        _table_headings(axes, columns, subject_labels)
        for line, keyword_id in enumerate(keywords, start=HEADER_LINES):
            row_cells = [(centre, cells.get((keyword_id, subject))) for centre, subject in columns]
            _table_row(axes, line, keyword_phrases[keyword_id], row_cells)
        _title(figure, title)
    return output


def _table_axes(axes: Axes, lines: int) -> None:
    _plain_axes(axes)
    axes.set_xlim(0, 1)
    axes.set_ylim(lines - 0.5, -0.5)
    axes.set_xticks([])
    axes.set_yticks([])


def _table_headings(
    axes: Axes, columns: list[tuple[float, str]], subject_labels: dict[str, str]
) -> None:
    for centre, subject in columns:
        heading = textwrap.fill(subject_labels.get(subject, subject), HEADING_WRAP)
        colour = GOLD if subject == CUSTOMER_SUBJECT else TEXT
        axes.text(centre, 1.1, heading, ha="center", va="center", fontsize=LABEL_SIZE, color=colour)


def _table_row(
    axes: Axes, line: int, phrase: str, cells: list[tuple[float, Ranking | None]]
) -> None:
    """One keyword line: separator, shortened phrase, one coloured cell per subject."""
    axes.axhline(line - 0.5, color=NIGHT_RAISED, linewidth=1)
    axes.text(
        0,
        line,
        textwrap.shorten(phrase, PHRASE_WIDTH, placeholder="…"),
        fontsize=LABEL_SIZE,
        va="center",
    )
    for centre, found in cells:
        organic = found.organic_position if found else None
        local = found.local_pack_position if found else None
        axes.text(
            centre,
            line,
            _cell_text(organic, local),
            ha="center",
            va="center",
            fontsize=LABEL_SIZE,
            fontweight="bold",
            color=position_colour(_best_of(organic, local)),
        )
