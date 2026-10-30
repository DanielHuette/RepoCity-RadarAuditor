"""Shared Jinja2 environment, German number formats and HTML-to-PDF conversion.

Context: Reports and business documents render the same way, so both use this module.
Input: Template directory, template name, context values.
Output: HTML text, PDF files, file-name-safe strings.
"""

import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateError,
    select_autoescape,
)
from weasyprint import HTML  # type: ignore[import-untyped]

from radarauditor.errors import RenderError

logger = logging.getLogger(__name__)

MISSING_VALUE = "–"
EURO_SIGN = " €"
TRANSLITERATION = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)
UNSAFE_FILENAME_CHARACTERS = re.compile(r"[^A-Za-z0-9-]+")

Number = Decimal | float | int


def german_number(value: Number | None, places: int = 2) -> str:
    """Format a number the German way: dot for thousands, comma for decimals.

    Args:
        value: Number to format; None yields a dash.
        places: Decimal places after rounding half up.

    Returns:
        Text such as "1.234,56".

    Raises:
        RenderError: If the value is not a finite number or places is negative.
    """
    if value is None:
        return MISSING_VALUE
    if places < 0:
        raise RenderError(f"decimal places must not be negative, got {places}")
    try:
        quantized = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise RenderError(f"{value!r} is not a finite number") from exc
    english = f"{quantized:,.{places}f}"
    return english.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def german_currency(value: Number | None) -> str:
    """Format an amount in euro, e.g. "1.234,56 €".

    Args:
        value: Amount; None yields a dash.

    Returns:
        Amount with two decimals and a non-breaking space before the euro sign.
    """
    if value is None:
        return MISSING_VALUE
    return german_number(value, 2) + EURO_SIGN


def german_date(value: date | datetime | None) -> str:
    """Format a date as DD.MM.YYYY.

    Args:
        value: Date or datetime; None yields a dash.

    Returns:
        Date text.
    """
    if value is None:
        return MISSING_VALUE
    return value.strftime("%d.%m.%Y")


def filename_slug(text: str) -> str:
    """Turn a name into an ASCII file-name part that storage keys accept.

    Args:
        text: Any name, e.g. "Dachdeckerei Müller & Söhne".

    Returns:
        Text such as "Dachdeckerei_Mueller_Soehne"; a name without usable characters becomes
        "x".
    """
    transliterated = unicodedata.normalize("NFKD", text.translate(TRANSLITERATION))
    ascii_text = transliterated.encode("ascii", "ignore").decode("ascii")
    slug = UNSAFE_FILENAME_CHARACTERS.sub("_", ascii_text).strip("_")
    return slug or "x"


def template_environment(directory: Path) -> Environment:
    """Build a Jinja2 environment with autoescaping, strict variables and German filters.

    Args:
        directory: Folder holding the templates.

    Returns:
        Environment with the filters number, currency and date.

    Raises:
        RenderError: If the folder does not exist.
    """
    if not directory.is_dir():
        raise RenderError(f"template folder {directory} does not exist")
    environment = Environment(
        loader=FileSystemLoader(directory),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["number"] = german_number
    environment.filters["currency"] = german_currency
    environment.filters["date"] = german_date
    return environment


def render_template(directory: Path, template_name: str, context: Mapping[str, object]) -> str:
    """Render one template of a folder to HTML text.

    Args:
        directory: Template folder.
        template_name: File name inside the folder.
        context: Values the template reads.

    Returns:
        Rendered HTML.

    Raises:
        RenderError: If the template is missing or uses an undefined value.
    """
    environment = template_environment(directory)
    try:
        return environment.get_template(template_name).render(context)
    except TemplateError as exc:
        raise RenderError(f"template {template_name} failed: {exc}") from exc


def write_pdf(html: str, output: Path, base_url: Path | None = None) -> Path:
    """Convert HTML to PDF with WeasyPrint and write it atomically.

    Args:
        html: Complete HTML document.
        output: Target PDF path; parent folders are created.
        base_url: Folder that relative links in the HTML resolve against.

    Returns:
        The output path.

    Raises:
        RenderError: If the file cannot be written.
    """
    partial = output.with_name(f".{output.name}.part")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RenderError(f"write {output} failed: {exc}") from exc
    try:
        document = HTML(string=html, base_url=str(base_url) if base_url else None)
        document.write_pdf(partial)
        partial.replace(output)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise RenderError(f"write {output} failed: {exc}") from exc
    logger.info("Rendered %s", output.name)
    return output


def write_combined_pdf(htmls: Sequence[str], output: Path) -> Path:
    """Render several HTML documents and write their pages, in order, into one PDF.

    Each document keeps its own page styles (margins, running headers and footers).

    Args:
        htmls: Complete HTML documents; at least one.
        output: Target PDF path; parent folders are created.

    Returns:
        The output path.

    Raises:
        RenderError: If no document is given or the file cannot be written.
    """
    if not htmls:
        raise RenderError(f"write {output} failed: nothing to render")
    partial = output.with_name(f".{output.name}.part")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        rendered = [HTML(string=html).render() for html in htmls]
        pages = [page for document in rendered for page in document.pages]
        rendered[0].copy(pages).write_pdf(partial)
        partial.replace(output)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise RenderError(f"write {output} failed: {exc}") from exc
    logger.info("Rendered %s from %d documents", output.name, len(htmls))
    return output
