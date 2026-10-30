"""Tests for the shared rendering helpers and the texte.json schema.

Context: Pure functions plus one real WeasyPrint call. | Input: Literals. | Output: Assertions.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from radarauditor.errors import RenderError
from radarauditor.rendering import (
    filename_slug,
    german_currency,
    german_date,
    german_number,
    render_template,
    template_environment,
    write_pdf,
)
from radarauditor.reporting.texts import ReportTexts, parse_texts

SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "radar-auswerter" / "SKILL.md"


@pytest.mark.parametrize(
    ("value", "places", "expected"),
    [
        (Decimal("1234.567"), 2, "1.234,57"),
        (1234567, 0, "1.234.567"),
        (0.5, 1, "0,5"),
        (Decimal("-238"), 2, "-238,00"),
        (None, 2, "–"),
    ],
)
def test_german_number(value: Decimal | float | None, places: int, expected: str) -> None:
    """Dot for thousands, comma for decimals, half up."""
    assert german_number(value, places) == expected


def test_german_number_rejects_bad_input() -> None:
    """Infinity and negative places are errors."""
    with pytest.raises(RenderError, match="finite"):
        german_number(float("inf"))
    with pytest.raises(RenderError, match="places"):
        german_number(1, -1)


def test_german_currency_and_date() -> None:
    """Euro sign after a non-breaking space, dates as DD.MM.YYYY."""
    assert german_currency(Decimal("1234.5")) == "1.234,50 €"
    assert german_currency(None) == "–"
    assert german_date(date(2026, 3, 1)) == "01.03.2026"
    assert german_date(datetime(2026, 12, 24, 18, 0)) == "24.12.2026"
    assert german_date(None) == "–"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Dachdeckerei Müller & Söhne", "Dachdeckerei_Mueller_Soehne"),
        ("Straßenbau Ägir", "Strassenbau_Aegir"),
        ("Café Élan", "Cafe_Elan"),
        ("RA-2026-0001", "RA-2026-0001"),
        ("***", "x"),
    ],
)
def test_filename_slug(name: str, expected: str) -> None:
    """ASCII only, words joined by underscores."""
    assert filename_slug(name) == expected


def test_template_environment_needs_folder(tmp_path: Path) -> None:
    """A missing template folder is reported."""
    with pytest.raises(RenderError, match="does not exist"):
        template_environment(tmp_path / "fehlt")


def test_render_template_escapes_and_formats(tmp_path: Path) -> None:
    """Autoescaping and the German filters are active."""
    (tmp_path / "t.html").write_text("{{ name }} {{ amount|currency }}", encoding="utf-8")

    html = render_template(tmp_path, "t.html", {"name": "<b>", "amount": Decimal(5)})

    assert html == "&lt;b&gt; 5,00 €"


def test_render_template_rejects_undefined_values(tmp_path: Path) -> None:
    """A typo in a template is an error, not an empty string."""
    (tmp_path / "t.html").write_text("{{ missing }}", encoding="utf-8")

    with pytest.raises(RenderError, match="t.html"):
        render_template(tmp_path, "t.html", {})


def test_write_pdf(tmp_path: Path) -> None:
    """A PDF is written atomically into a new folder."""
    output = write_pdf("<p>Hallo</p>", tmp_path / "neu" / "x.pdf")

    assert output.read_bytes().startswith(b"%PDF")
    assert [path.name for path in output.parent.iterdir()] == ["x.pdf"]


def test_write_pdf_reports_unwritable_target(tmp_path: Path) -> None:
    """A file in place of the folder is a render error."""
    blocker = tmp_path / "blockiert"
    blocker.write_text("x", encoding="utf-8")

    with pytest.raises(RenderError, match="write"):
        write_pdf("<p>x</p>", blocker / "x.pdf")


def test_parse_texts(report_texts: ReportTexts) -> None:
    """The schema round-trips; before_after is optional, lists default to empty."""
    parsed = parse_texts(report_texts.model_dump_json())
    minimal = parse_texts(
        '{"introduction": "a", "summary": "b", "forecast_note": "c",'
        ' "subscription_reason": "d", "offer_text": "e", "talk_text": "f"}'
    )

    assert parsed == report_texts
    assert minimal.before_after is None
    assert minimal.post_suggestions == []
    assert minimal.review_replies == []
    assert minimal.talk_text == "f"


def test_parse_texts_of_earlier_evaluations_keeps_their_texts() -> None:
    """A texte.json written before talk_text existed still gives its own texts to the report."""
    texts = parse_texts(
        '{"introduction": "a", "summary": "b", "forecast_note": "c",'
        ' "subscription_reason": "d", "offer_text": "e"}'
    )
    assert texts.summary == "b"
    assert texts.talk_text is None


def test_parse_texts_rejects_empty_talk_text() -> None:
    """An empty conversation text is invalid."""
    with pytest.raises(RenderError, match="talk_text"):
        parse_texts(
            '{"introduction": "a", "summary": "b", "forecast_note": "c",'
            ' "subscription_reason": "d", "offer_text": "e", "talk_text": ""}'
        )


@pytest.mark.parametrize(
    "content",
    ["kein json", '{"introduction": ""}', '{"review_replies": [{"reply": "x"}]}'],
)
def test_parse_texts_rejects_invalid_content(content: str) -> None:
    """Malformed JSON and missing or empty fields are render errors."""
    with pytest.raises(RenderError, match="texte.json invalid"):
        parse_texts(content)


def test_skill_documents_every_texts_field() -> None:
    """Claude is told about every field the report reads."""
    skill = SKILL_PATH.read_text(encoding="utf-8")

    for field in ReportTexts.model_fields:
        assert f"`{field}`" in skill
    assert "`review_excerpt`" in skill
