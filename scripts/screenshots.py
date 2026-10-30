"""Capture the screenshots of the operating manual from the built app with demo data.

Context: Builds the app against a demo Supabase address, serves it locally and answers every call
to that address with German demo data, so the pictures show real screens without a database.
Input: The app sources in app/, the check catalogue in config/pruefkatalog.yaml.
Output: PNG files <screen>-handy.png and <screen>-desktop.png in Dokumentation/bilder/.

Usage:
    uv run python scripts/screenshots.py
    uv run python scripts/screenshots.py --font path/to/Archivo-Variable.woff2
"""

import argparse
import base64
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

import yaml
from playwright.sync_api import Browser, BrowserContext, Page, Route, sync_playwright

logger = logging.getLogger(__name__)

Row = dict[str, Any]

REPOSITORY = Path(__file__).resolve().parent.parent
APP_DIR = REPOSITORY / "app"
CATALOGUE_PATH = REPOSITORY / "config" / "pruefkatalog.yaml"
DEFAULT_OUTPUT = REPOSITORY / "Dokumentation" / "bilder"
DEMO_SUPABASE_URL = "https://demo-radarauditor.supabase.co"
DEMO_ANON_KEY = "demo-anon-key"
DEMO_CRM_ORIGIN = "https://crm.example.de"
FONT_PATH = "/fonts/Archivo-Variable.woff2"
OPERATOR_EMAIL = "betrieb@example.de"
OPERATOR_PASSWORD = "beliebig"
CHART_SETTLE_MS = 1500
SCREEN_SETTLE_MS = 400
BUILD_TIMEOUT_S = 300

CUSTOMER_ROOFER = "0b1e2c3d-0000-4000-8000-000000000001"
CUSTOMER_PROSPECT = "0b1e2c3d-0000-4000-8000-000000000002"
AUDIT_FIRST = "0a0d0000-0000-4000-8000-000000000001"
AUDIT_SECOND = "0a0d0000-0000-4000-8000-000000000002"
AUDIT_PROSPECT = "0a0d0000-0000-4000-8000-000000000003"
KEYWORD_ROOFER = "0c0e0000-0000-4000-8000-000000000001"
KEYWORD_RENOVATION = "0c0e0000-0000-4000-8000-000000000002"
KEYWORD_FLAT_ROOF = "0c0e0000-0000-4000-8000-000000000003"
CRM_CUSTOMER_ROOFER = "0e0c0000-0000-4000-8000-000000000001"

ROOFER_NAME = "Dachdeckerei Muster GmbH"
COMPETITOR_NAMES = ("Dachtechnik Aasee GmbH", "Bedachungen Kröger")

EMBEDDED_KEYS = {"customers": "customer_id"}
UNMEASURED_SOURCES = frozenset({"paid", "access", "manual"})


@dataclass(frozen=True)
class Viewport:
    """One device the screens are captured on."""

    suffix: str
    width: int
    height: int
    scale: int
    mobile: bool


VIEWPORTS = (
    Viewport("handy", 390, 844, 2, True),
    Viewport("desktop", 1280, 800, 1, False),
)

ROOFER_FAILED = {
    "a5", "b3", "b9", "c1", "c3", "c10", "d4", "e1", "e3", "e5", "e8", "f3", "f8", "f9", "f10",
    "g1", "g3", "h2", "h4", "h7", "j4", "k1", "k2", "k3", "l2",
}  # fmt: skip
ROOFER_PARTIAL = {
    "a2", "a7", "b2", "c4", "c5", "d3", "f12", "g4", "g6", "h3", "h5", "i4", "j1", "j2", "j6",
    "k4", "l3",
}  # fmt: skip
PROSPECT_FAILED = {
    "a1", "a2", "a3", "a5", "a6", "a7", "b2", "b3", "b5", "b6", "b9", "c1", "c3", "c5", "c7",
    "c8", "c9", "c10", "d2", "d3", "d4", "d5", "e1", "e2", "e3", "e4", "e5", "e8", "f2", "f3",
    "f8", "f9", "f10", "g1", "g3", "g4", "g6", "h1", "h2", "h4", "h5", "h7", "i4", "j1", "j4",
    "j7", "j8", "k1", "k2", "k4", "l2", "l3",
}  # fmt: skip
PROSPECT_PARTIAL = {"a4", "b1", "b4", "c4", "c6", "d1", "f4", "f7", "g2", "h3", "j2", "l1"}
DEMO_PROSPECT_WEBSITE = "elektro-beispiel.de"
# Row id of check g4 in "Alle Prüfungen", as checkAnchor in app/src/lib/evaluation.ts builds it.
REVIEWS_CHECK = "#pruefung-g4"
OPEN_OUTCOMES = frozenset({"failed", "partial"})

UNANSWERED_REVIEWS: list[Row] = [
    {"stars": 2, "date": "2026-09-22", "text": "Termin wurde zweimal verschoben."},
    {"stars": 5, "date": "2026-09-10", "text": "Schnell da gewesen, sauber gearbeitet."},
    {"stars": 4, "date": "2026-08-28", "text": ""},
]


def iso(day: str, time: str = "10:00") -> str:
    """Timestamp in the format Supabase returns.

    Args:
        day: Date as YYYY-MM-DD.
        time: Clock time as HH:MM, local German summer time.

    Returns:
        ISO timestamp with offset.
    """
    return f"{day}T{time}:00+02:00"


def catalogue_checks() -> list[tuple[str, str, float, str]]:
    """Read check id, area id, area weight and source from the check catalogue.

    Returns:
        One tuple per check in catalogue order.
    """
    catalogue = yaml.safe_load(CATALOGUE_PATH.read_text(encoding="utf-8"))
    return [
        (check["id"], area["id"], float(area["weight"]), str(check["source"]))
        for area in catalogue["areas"]
        for check in area["checks"]
    ]


def check_results(audit_id: str, failed: set[str], partial_ids: set[str]) -> list[Row]:
    """Check results of one audit; paid, access and manual checks are unmeasured, as in the worker.

    Args:
        audit_id: Audit the results belong to.
        failed: Check ids that failed.
        partial_ids: Check ids that passed partially.

    Returns:
        Rows of table check_results.
    """
    rows: list[Row] = []
    for index, (check_id, area, weight, source) in enumerate(catalogue_checks()):
        if source in UNMEASURED_SOURCES:
            outcome = "not_measurable"
        elif check_id in failed:
            outcome = "failed"
        elif check_id in partial_ids:
            outcome = "partial"
        else:
            outcome = "passed"
        rows.append(
            {
                "id": f"{audit_id[:-6]}{index:06d}",
                "audit_id": audit_id,
                "check_id": check_id,
                "area": area,
                "outcome": outcome,
                "measured": demo_measured(check_id, outcome),
                "weight": weight,
                "potential_points": demo_potential(index, weight, outcome),
                "uplift_low": None,
                "uplift_high": None,
            }
        )
    return rows


def demo_potential(index: int, weight: float, outcome: str) -> float | None:
    """Varied open potential so the levers and the potential sort show real differences.

    Args:
        index: Position of the check in the catalogue.
        weight: Weight of the check's area.
        outcome: Outcome of the check.

    Returns:
        Points gained by passing the check, None for checks without potential.
    """
    if outcome not in OPEN_OUTCOMES:
        return None
    divisor = 10 if outcome == "failed" else 20
    return round(weight * (3 - index % 3) / divisor, 1)


def demo_measured(check_id: str, outcome: str) -> Row:
    """Measured values the app shows inside an opened check.

    Args:
        check_id: Catalogue id of the check.
        outcome: Outcome of the check.

    Returns:
        The reviews without an owner reply for an open g4, else nothing.
    """
    if check_id == "g4" and outcome in OPEN_OUTCOMES:
        return {"unanswered_examples": UNANSWERED_REVIEWS}
    return {}


LOCAL_BUSINESS_SCHEMA = {
    "@context": "https://schema.org",
    "@type": "RoofingContractor",
    "name": ROOFER_NAME,
    "url": "https://dachdeckerei-muster.de/",
    "telephone": "+49 251 987650",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "Hafenweg 12",
        "postalCode": "48155",
        "addressLocality": "Münster",
        "addressCountry": "DE",
    },
    "geo": {"@type": "GeoCoordinates", "latitude": 51.9526, "longitude": 7.6418},
    "openingHours": ["Mo-Fr 07:00-17:00"],
    "areaServed": ["Münster", "Telgte", "Greven", "Nottuln"],
}


def findings() -> list[Row]:
    """The six findings of the second roofer audit as /auswerten would write them.

    Returns:
        Rows of table findings.
    """
    entries = [
        (
            "e1",
            3,
            "Google und KI-Assistenten finden auf Ihrer Startseite keine maschinenlesbaren "
            "Firmendaten.",
            "Auf der Startseite fehlt ein LocalBusiness-Eintrag (strukturierte Daten). "
            "Beide Wettbewerber haben einen.",
            "Ohne diese Angaben verknüpft Google Ihre Website schwerer mit Ihrem Profil auf "
            "Google Maps; Sie erscheinen seltener im Kartenblock.",
            "Den fertigen Code unten in den Kopfbereich der Startseite einfügen.",
            {
                "code": '<script type="application/ld+json">\n'
                + json.dumps(LOCAL_BUSINESS_SCHEMA, ensure_ascii=False, indent=2)
                + "\n</script>"
            },
        ),
        (
            "g3",
            3,
            "Es kommen zu wenige neue Bewertungen dazu.",
            "In den letzten 90 Tagen kamen 2 neue Bewertungen dazu, also weniger als eine im "
            "Monat. Dachtechnik Aasee GmbH bekam im selben Zeitraum 9.",
            "Google zeigt Betriebe mit frischen Bewertungen weiter oben; Suchende rufen eher "
            "dort an.",
            "Nach jedem abgeschlossenen Auftrag den Bewertungslink per SMS schicken.",
            {
                "text": "Guten Tag, danke für Ihren Auftrag! Wenn Sie zufrieden waren, freuen "
                "wir uns über eine kurze Bewertung: g.page/r/dachdeckerei-muster/review – "
                "Ihre Dachdeckerei Muster"
            },
        ),
        (
            "b2",
            2,
            "Die Seitentitel nennen weder Leistung noch Ort.",
            "4 von 9 Seiten heißen nur „Dachdeckerei Muster“; der Titel der Startseite hat 21 "
            "Zeichen.",
            "Wer „Dachdecker Münster“ sucht, sieht in der Trefferliste nicht, dass Sie genau "
            "das anbieten.",
            "Die Titel aus dem Kasten im WordPress-Feld „SEO-Titel“ der jeweiligen Seite "
            "eintragen.",
            {
                "text": "Startseite: Dachdecker in Münster – Dachdeckerei Muster GmbH\n"
                "Dachsanierung: Dachsanierung in Münster mit Festpreis | Muster\n"
                "Flachdach: Flachdach abdichten in Münster | Dachdeckerei Muster"
            },
        ),
        (
            "f8",
            2,
            "Im Google-Profil fehlen aktuelle Fotos.",
            "Das Profil zeigt 6 Fotos, das letzte ist vom 12.03.2026. Dachtechnik Aasee GmbH "
            "zeigt 48.",
            "Suchende entscheiden auf der Karte nach Bildern; ein Profil ohne aktuelle Fotos "
            "wirkt verlassen.",
            "Zehn Fotos von aktuellen Baustellen hochladen, dazu Logo und Titelbild.",
            None,
        ),
        (
            "h2",
            2,
            "Der Betrieb fehlt bei Bing Places.",
            "Bing Places hat keinen Eintrag für Dachdeckerei Muster GmbH; Bing liefert die "
            "Daten auch an Copilot und ChatGPT.",
            "Fragt jemand einen KI-Assistenten nach einem Dachdecker in Münster, fehlen Sie "
            "in dessen Quellen.",
            "Bing Places mit dem Google-Profil verbinden und die Daten übernehmen lassen.",
            None,
        ),
        (
            "a7",
            1,
            "Viele Bilder sind größer als nötig.",
            "14 Bilder sind zusammen 6,2 Megabyte groß; 9 davon haben keinen Alternativtext.",
            "Die Seite lädt auf dem Handy langsamer; Suchmaschinen verstehen die Bilder nicht.",
            "Bilder auf höchstens 1600 Pixel Breite verkleinern und jedem Bild einen kurzen "
            "beschreibenden Text geben.",
            None,
        ),
    ]
    return [
        {
            "id": f"0f0f0000-0000-4000-8000-{index:012d}",
            "audit_id": AUDIT_SECOND,
            "check_id": check_id,
            "severity": severity,
            "title": title,
            "explanation": explanation,
            "customer_impact": impact,
            "fix": fix,
            "fix_payload": payload,
        }
        for index, (check_id, severity, title, explanation, impact, fix, payload) in enumerate(
            entries, start=1
        )
    ]


def rankings() -> list[Row]:
    """Positions of the roofer and two competitors for three keywords in both audits.

    Returns:
        Rows of table rankings.
    """
    positions: dict[str, dict[str, tuple[tuple[int | None, int | None], ...]]] = {
        AUDIT_SECOND: {
            ROOFER_NAME: ((7, 3), (4, 2), (12, None)),
            COMPETITOR_NAMES[0]: ((2, 1), (6, 3), (3, 2)),
            COMPETITOR_NAMES[1]: ((5, 2), (None, 5), (9, 6)),
        },
        AUDIT_FIRST: {
            ROOFER_NAME: ((14, None), (9, 6), (None, None)),
            COMPETITOR_NAMES[0]: ((2, 1), (5, 2), (3, 2)),
            COMPETITOR_NAMES[1]: ((4, 2), (11, 4), (8, 5)),
        },
    }
    keywords = (KEYWORD_ROOFER, KEYWORD_RENOVATION, KEYWORD_FLAT_ROOF)
    measured = {AUDIT_SECOND: iso("2026-10-01", "09:20"), AUDIT_FIRST: iso("2026-08-04", "10:05")}
    rows: list[Row] = []
    for audit_id, subjects in positions.items():
        for subject, per_keyword in subjects.items():
            for keyword_id, (organic, local) in zip(keywords, per_keyword, strict=True):
                rows.append(
                    {
                        "id": f"0e0e{len(rows):04d}-0000-4000-8000-000000000000",
                        "audit_id": audit_id,
                        "keyword_id": keyword_id,
                        "subject": subject,
                        "organic_position": organic,
                        "local_pack_position": local,
                        "grid_point": None,
                        "measured_at": measured[audit_id],
                    }
                )
    return rows


def customers_and_setup() -> dict[str, list[Row]]:
    """Customers with locations, competitors and keywords.

    Returns:
        Rows per table.
    """
    return {
        "customers": [
            {
                "id": CUSTOMER_ROOFER,
                "name": ROOFER_NAME,
                "website": "https://dachdeckerei-muster.de/",
                "industry": "Handwerk",
                "city": "Münster",
                "order_value_eur": 4460,
                "crm_customer_id": CRM_CUSTOMER_ROOFER,
                "created_at": iso("2026-07-28"),
            },
            {
                "id": CUSTOMER_PROSPECT,
                "name": "Elektro Beispiel e.K.",
                "website": "https://elektro-beispiel.de/",
                "industry": "Handwerk",
                "city": "Münster",
                "order_value_eur": None,
                "crm_customer_id": None,
                "created_at": iso("2026-10-15"),
            },
        ],
        "locations": [
            {
                "id": "01010000-0000-4000-8000-000000000001",
                "customer_id": CUSTOMER_ROOFER,
                "name": ROOFER_NAME,
                "street": "Hafenweg 12",
                "postal_code": "48155",
                "city": "Münster",
                "phone": "0251 98765-0",
                "google_place_id": "ChIJ-demo-dachdeckerei-muster",
                "latitude": 51.9526,
                "longitude": 7.6418,
            },
            {
                "id": "01010000-0000-4000-8000-000000000002",
                "customer_id": CUSTOMER_PROSPECT,
                "name": "Elektro Beispiel e.K.",
                "street": "Wolbecker Straße 140",
                "postal_code": "48155",
                "city": "Münster",
                "phone": "0251 445566",
                "google_place_id": None,
                "latitude": None,
                "longitude": None,
            },
        ],
        "competitors": [
            {
                "id": f"02020000-0000-4000-8000-00000000000{index}",
                "customer_id": CUSTOMER_ROOFER,
                "name": name,
                "google_place_id": None,
                "website": website,
            }
            for index, (name, website) in enumerate(
                zip(
                    COMPETITOR_NAMES,
                    ("https://dachtechnik-aasee.de/", "https://bedachungen-kroeger.de/"),
                    strict=True,
                ),
                start=1,
            )
        ],
        "keywords": [
            {
                "id": keyword_id,
                "customer_id": CUSTOMER_ROOFER,
                "phrase": phrase,
                "city": "Münster",
                "monthly_volume": volume,
                "is_primary": primary,
            }
            for keyword_id, phrase, volume, primary in (
                (KEYWORD_ROOFER, "Dachdecker", 880, True),
                (KEYWORD_RENOVATION, "Dachsanierung", 320, True),
                (KEYWORD_FLAT_ROOF, "Flachdach abdichten", None, False),
            )
        ],
    }


def audits() -> list[Row]:
    """Two reported audits of the roofer and one audit of the prospect waiting for its analysis.

    Returns:
        Rows of table audits.
    """
    return [
        {
            "id": AUDIT_FIRST,
            "customer_id": CUSTOMER_ROOFER,
            "kind": "remote",
            "status": "reported",
            "requested_at": iso("2026-08-04", "09:12"),
            "finished_at": iso("2026-08-04", "10:40"),
            "total_score": 41,
            "report_path": f"{AUDIT_FIRST}/Verkaufsbericht.pdf",
            "error": None,
            "disabled_checks": [],
        },
        {
            "id": AUDIT_SECOND,
            "customer_id": CUSTOMER_ROOFER,
            "kind": "with_access",
            "status": "reported",
            "requested_at": iso("2026-10-01", "08:30"),
            "finished_at": iso("2026-10-01", "09:55"),
            "total_score": 63,
            "report_path": f"{AUDIT_SECOND}/Monatsbericht.pdf",
            "error": None,
            "disabled_checks": [],
        },
        {
            "id": AUDIT_PROSPECT,
            "customer_id": CUSTOMER_PROSPECT,
            "kind": "remote",
            "status": "awaiting_analysis",
            "requested_at": iso("2026-10-15", "16:20"),
            "finished_at": None,
            "total_score": 36,
            "report_path": None,
            "error": None,
            "disabled_checks": [],
        },
    ]


def demo_tables() -> dict[str, list[Row]]:
    """All tables the app reads, filled with the demo data.

    Returns:
        Rows per table name.
    """
    tables = customers_and_setup()
    tables["audits"] = audits()
    tables["check_results"] = check_results(
        AUDIT_SECOND, ROOFER_FAILED, ROOFER_PARTIAL
    ) + check_results(AUDIT_PROSPECT, PROSPECT_FAILED, PROSPECT_PARTIAL)
    tables["findings"] = findings()
    tables["rankings"] = rankings()
    tables["settings"] = []
    return tables


RAW_FILES: dict[str, Row] = {
    f"{AUDIT_SECOND}/texte.json": {
        "talk_text": "Ihr Betrieb wird bei Google gut gefunden, solange jemand Ihren Namen kennt."
        " Wer nur nach einem Dachdecker in Münster sucht, sieht zuerst zwei Wettbewerber."
        " Der größte Hebel ist Ihr Google-Profil: Leistungen, Fotos und Antworten auf Bewertungen"
        " fehlen dort. Danach kommen eigene Seiten für Dachsanierung und Flachdach."
        " Beides ist in wenigen Wochen erledigt, und den Effekt messen wir im nächsten Monat.",
        "post_suggestions": [
            "Sturm, Hagel, undichte Stelle? Wir sind in Münster und Umgebung innerhalb"
            " von 24 Stunden vor Ort und sichern Ihr Dach. Rufen Sie uns an: 0251 123456.",
            "Vor dem Winter lohnt ein Blick aufs Dach: Wir prüfen Ziegel, Rinnen und Anschlüsse"
            " für einen festen Preis von 149 Euro. Termine im Oktober sind noch frei.",
        ],
        "review_replies": [
            {
                "review_excerpt": "Schnell da gewesen, sauber gearbeitet, Preis wie abgesprochen.",
                "reply": "Vielen Dank für Ihre freundliche Bewertung! Es freut uns, dass alles wie"
                " abgesprochen geklappt hat. Wir sind gern wieder für Sie da.",
            },
            {
                "review_excerpt": "Termin wurde zweimal verschoben.",
                "reply": "Es tut uns leid, dass wir den Termin verschieben mussten. Nach dem Sturm"
                " hatten Notfälle Vorrang. Wir haben unsere Terminplanung seitdem umgestellt und"
                " melden uns künftig früher.",
            },
        ],
    }
}


class PostgrestEmulator:
    """Answers the PostgREST calls of the app from in-memory tables."""

    def __init__(self, tables: dict[str, list[Row]]) -> None:
        """Keep the tables.

        Args:
            tables: Rows per table name.
        """
        self.tables = tables

    def select(self, table: str, params: list[tuple[str, str]]) -> list[Row]:
        """Filter, order, limit and project rows like PostgREST.

        Args:
            table: Table name.
            params: Query parameters of the request.

        Returns:
            Matching rows.
        """
        rows = list(self.tables.get(table, []))
        selection = "*"
        order = ""
        limit: int | None = None
        for key, value in params:
            if key == "select":
                selection = value
            elif key == "order":
                order = value
            elif key == "limit":
                limit = int(value)
            else:
                rows = [row for row in rows if matches(row, key, value)]
        rows = sort_rows(rows, order)
        if limit is not None:
            rows = rows[:limit]
        return [self.project(row, selection) for row in rows]

    def project(self, row: Row, selection: str) -> Row:
        """Keep the selected columns and attach embedded customers.

        Args:
            row: Source row.
            selection: PostgREST select expression.

        Returns:
            Projected row.
        """
        result: Row = {}
        for part in split_selection(selection):
            embed = re.fullmatch(r"(\w+)\(([^)]*)\)", part)
            if embed is None:
                result |= dict(row) if part == "*" else {part: row.get(part)}
                continue
            table, columns = embed.group(1), embed.group(2)
            key = row.get(EMBEDDED_KEYS.get(table, ""))
            related = next((item for item in self.tables.get(table, []) if item["id"] == key), None)
            result[table] = (
                None
                if related is None
                else {column.strip(): related.get(column.strip()) for column in columns.split(",")}
            )
        return result


def split_selection(selection: str) -> list[str]:
    """Split a select expression at top-level commas.

    Args:
        selection: PostgREST select expression such as "*, customers(name)".

    Returns:
        Its parts without surrounding blanks.
    """
    parts: list[str] = []
    depth = 0
    current = ""
    for character in selection:
        depth += {"(": 1, ")": -1}.get(character, 0)
        if character == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += character
    parts.append(current.strip())
    return [part for part in parts if part]


def matches(row: Row, column: str, condition: str) -> bool:
    """Apply one PostgREST filter such as eq.value or neq.value.

    Args:
        row: Row to test.
        column: Column name.
        condition: Operator and value.

    Returns:
        Whether the row passes; unknown operators pass everything.
    """
    operator, _, expected = condition.partition(".")
    actual = row.get(column)
    text = (
        "null"
        if actual is None
        else str(actual).lower()
        if isinstance(actual, bool)
        else str(actual)
    )
    if operator == "eq":
        return text == expected
    if operator == "neq":
        return text != expected
    if operator == "is":
        return text == expected
    return True


def sort_rows(rows: list[Row], order: str) -> list[Row]:
    """Sort by a PostgREST order expression such as "is_primary.desc,phrase.asc".

    Args:
        rows: Rows to sort.
        order: Comma-separated column.direction[.nullsfirst|nullslast] terms.

    Returns:
        Sorted copy.
    """
    result = list(rows)
    for term in reversed([term for term in order.split(",") if term]):
        column, *modifiers = term.split(".")
        descending = "desc" in modifiers
        nulls_first = "nullsfirst" in modifiers or ("nullslast" not in modifiers and descending)
        present = [row for row in result if row.get(column) is not None]
        missing = [row for row in result if row.get(column) is None]
        present.sort(key=lambda row: row[column], reverse=descending)
        result = missing + present if nulls_first else present + missing
    return result


def fake_jwt() -> str:
    """Unsigned token shaped like a Supabase access token that expires in a day.

    Returns:
        Header, payload and signature joined by dots.
    """

    def encode(content: Row) -> str:
        raw = json.dumps(content, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    expires = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
    payload = {
        "sub": "demo-operator",
        "role": "authenticated",
        "email": OPERATOR_EMAIL,
        "exp": expires,
    }
    return f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode(payload)}.demo-signature"


DEMO_USER: Row = {
    "id": "05050000-0000-4000-8000-000000000001",
    "aud": "authenticated",
    "role": "authenticated",
    "email": OPERATOR_EMAIL,
    "app_metadata": {"provider": "email", "providers": ["email"]},
    "user_metadata": {},
    "created_at": iso("2026-07-01"),
}


def session_body() -> Row:
    """Answer of /auth/v1/token for any e-mail and password.

    Returns:
        Supabase session.
    """
    expires_in = 86400
    return {
        "access_token": fake_jwt(),
        "token_type": "bearer",
        "expires_in": expires_in,
        "expires_at": int(datetime.now(UTC).timestamp()) + expires_in,
        "refresh_token": "demo-refresh-token",
        "user": DEMO_USER,
    }


# Function calls the screens need answered; the operator is administrator, so nothing is read-only.
RPC_ANSWERS: dict[str, object] = {"rpc/is_admin": True}

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
    "Access-Control-Expose-Headers": "Content-Range",
}


def storage_body(method_path: str, request_body: Row) -> object:
    """Answer storage signing calls with dummy links.

    Args:
        method_path: Path below /storage/v1/.
        request_body: Parsed JSON body of the request.

    Returns:
        JSON-serialisable answer.
    """
    bucket_and_path = method_path.removeprefix("object/sign/")
    if "/" in bucket_and_path:
        return {"signedURL": f"/object/sign/{bucket_and_path}?token=demo"}
    paths = request_body.get("paths", [])
    return [
        {
            "error": None,
            "path": path,
            "signedURL": f"/object/sign/{bucket_and_path}/{path}?token=demo",
        }
        for path in paths
    ]


def make_supabase_handler(emulator: PostgrestEmulator) -> Callable[[Route], None]:
    """Route handler that plays Supabase for every request to the demo address.

    Args:
        emulator: Table data.

    Returns:
        Function for BrowserContext.route.
    """

    def handle(route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            route.fulfill(status=204, headers=CORS_HEADERS)
            return
        url = urlsplit(request.url)
        path = unquote(url.path)
        body: object
        status = 200
        if path.startswith("/auth/v1/token"):
            body = session_body()
        elif path.startswith("/auth/v1/user"):
            body = DEMO_USER
        elif path.startswith("/auth/v1/logout"):
            route.fulfill(status=204, headers=CORS_HEADERS)
            return
        elif request.method == "GET" and path.startswith("/storage/v1/object/sign/"):
            stored = RAW_FILES.get(path.removeprefix("/storage/v1/object/sign/raw/"))
            body, status = (
                (stored, 200) if stored is not None else ({"message": "Object not found"}, 404)
            )
        elif path.startswith("/storage/v1/"):
            body = storage_body(
                path.removeprefix("/storage/v1/"), json.loads(request.post_data or "{}")
            )
        elif path.startswith("/rest/v1/"):
            body, status = rest_body(emulator, request.method, path, url.query, request)
        else:
            body, status = {"message": f"not emulated: {path}"}, 404
        route.fulfill(
            status=status,
            headers={**CORS_HEADERS, "Content-Type": "application/json"},
            body=json.dumps(body),
        )

    return handle


def rest_body(
    emulator: PostgrestEmulator, method: str, path: str, query: str, request: Any
) -> tuple[object, int]:
    """Answer a PostgREST read or echo a write.

    Args:
        emulator: Table data.
        method: HTTP method.
        path: Request path below the host.
        query: Raw query string.
        request: Playwright request, for headers and body.

    Returns:
        JSON body and status code.
    """
    table = path.removeprefix("/rest/v1/").strip("/")
    if table in RPC_ANSWERS:
        return RPC_ANSWERS[table], 200
    params = parse_qsl(query, keep_blank_values=True)
    single = "vnd.pgrst.object" in str(request.headers.get("accept", ""))
    if method == "GET":
        rows: list[Row] = emulator.select(table, params)
    else:
        payload = json.loads(request.post_data or "{}")
        written = payload if isinstance(payload, list) else [payload]
        rows = [{"id": "06060000-0000-4000-8000-000000000001", **item} for item in written]
    if single:
        return (rows[0], 200) if rows else ({"message": "no rows"}, 406)
    return rows, 201 if method == "POST" else 200


class SinglePageHandler(SimpleHTTPRequestHandler):
    """Static file server that answers unknown paths with index.html, like Cloudflare Pages."""

    font_file: Path | None = None

    def send_head(self) -> Any:
        """Serve the font override, the file, or index.html for app routes."""
        path = urlsplit(self.path).path
        if path == FONT_PATH and self.font_file is not None:
            return self._send_font(self.font_file)
        if not Path(self.translate_path(path)).exists():
            self.path = "/index.html"
        return super().send_head()

    def _send_font(self, font: Path) -> Any:
        content = font.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "font/woff2")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        return io.BytesIO(content)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Send access lines to the debug log instead of stderr."""
        logger.debug(format, *args)


def build_app(output: Path) -> None:
    """Run the production build against the demo Supabase and CRM addresses.

    Args:
        output: Folder that receives the built files.

    Raises:
        subprocess.CalledProcessError: If the build fails.
    """
    environment = {
        **os.environ,
        "VITE_SUPABASE_URL": DEMO_SUPABASE_URL,
        "VITE_SUPABASE_ANON_KEY": DEMO_ANON_KEY,
        "VITE_CRM_ORIGIN": DEMO_CRM_ORIGIN,
    }
    command = ["npm", "run", "build", "--", "--outDir", str(output), "--emptyOutDir"]
    logger.info("Building the app into %s", output)
    subprocess.run(
        command, cwd=APP_DIR, env=environment, check=True, timeout=BUILD_TIMEOUT_S,
        stdout=subprocess.DEVNULL,
    )  # fmt: skip


@contextmanager
def serve(folder: Path, font: Path | None) -> Iterator[str]:
    """Serve the built app on a free local port.

    Args:
        folder: Built app.
        font: Optional woff2 file served as the Archivo font.

    Yields:
        Base address of the server.
    """
    handler = type("DemoHandler", (SinglePageHandler,), {"font_file": font})
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(folder)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


class ScreenCapture:
    """Opens app screens on one viewport and writes the pictures."""

    def __init__(self, page: Page, base: str, output: Path, viewport: Viewport) -> None:
        """Keep page, server address, output folder and viewport.

        Args:
            page: Signed-out page of a fresh context.
            base: Address of the local server.
            output: Folder for the PNG files.
            viewport: Device the page emulates.
        """
        self.page = page
        self.base = base
        self.output = output
        self.viewport = viewport

    def target(self, screen: str) -> Path:
        """File name of one screen on this viewport."""
        return self.output / f"{screen}-{self.viewport.suffix}.png"

    def open(self, path: str, ready_text: str) -> None:
        """Navigate and wait until the text is visible and every spinner is gone.

        Args:
            path: App path such as /auswertung.
            ready_text: Text that proves the data arrived.
        """
        self.page.goto(f"{self.base}{path}")
        self.page.get_by_text(ready_text, exact=False).first.wait_for(state="visible")
        self.page.wait_for_function("document.querySelectorAll('.spinner').length === 0")
        self.page.wait_for_timeout(SCREEN_SETTLE_MS)

    def shoot(self, screen: str, scroll_to: str | None = None, settle_ms: int = 0) -> None:
        """Write the visible part of the page, optionally scrolled to an element first.

        Args:
            screen: Screen name used in the file name.
            scroll_to: CSS selector of the element shown at the top.
            settle_ms: Extra wait, for chart animations.
        """
        if scroll_to is None:
            self.page.evaluate("window.scrollTo(0, 0)")
        else:
            self.page.locator(scroll_to).first.evaluate(
                "element => window.scrollTo(0, element.getBoundingClientRect().top"
                " + window.scrollY - 16)"
            )
        self.page.wait_for_timeout(settle_ms + SCREEN_SETTLE_MS)
        self.page.screenshot(path=str(self.target(screen)))
        logger.info("Wrote %s", self.target(screen).name)

    def shoot_element(self, screen: str, selector: str) -> None:
        """Write one element only.

        Args:
            screen: Screen name used in the file name.
            selector: CSS selector of the element.
        """
        self.page.locator(selector).first.screenshot(path=str(self.target(screen)))
        logger.info("Wrote %s", self.target(screen).name)

    def open_sheet(self, button: str, screen: str, fill: dict[str, str] | None = None) -> None:
        """Press a button that opens a sheet, fill fields, capture it and close it again.

        Args:
            button: Visible label of the button.
            screen: Screen name used in the file name.
            fill: Values per field label typed before the picture.
        """
        self.page.get_by_role("button", name=button, exact=True).first.click()
        sheet = self.page.locator("dialog.sheet[open]")
        sheet.wait_for(state="visible")
        for label, value in (fill or {}).items():
            sheet.get_by_label(label, exact=True).fill(value)
        self.page.wait_for_timeout(SCREEN_SETTLE_MS)
        self.page.screenshot(path=str(self.target(screen)))
        logger.info("Wrote %s", self.target(screen).name)
        self.page.keyboard.press("Escape")


def sign_in(capture: ScreenCapture) -> None:
    """Capture the sign-in screen with filled fields and sign in."""
    page = capture.page
    page.goto(f"{capture.base}/anmelden")
    page.get_by_label("E-Mail").fill(OPERATOR_EMAIL)
    page.get_by_label("Passwort").fill(OPERATOR_PASSWORD)
    capture.shoot("anmelden")
    page.get_by_role("button", name="Anmelden").click()
    page.get_by_label("Website des Betriebs").wait_for(state="visible")


def capture_check(capture: ScreenCapture) -> None:
    """Prüfen, where sign-in lands: the address, the checks with one area open, the progress."""
    page = capture.page
    page.get_by_label("Website des Betriebs").fill(DEMO_PROSPECT_WEBSITE)
    capture.shoot("pruefen")
    page.get_by_role("button", name="Prüfen", exact=True).click()
    page.get_by_text("Alle Prüfungen").first.wait_for(state="visible")
    page.locator(".sel-row__open").first.click()
    capture.shoot("pruefen-auswahl")
    capture.open(f"/?pruefung={AUDIT_PROSPECT}", "gestartet")
    capture.shoot("pruefen-fortschritt")


def capture_evaluation(capture: ScreenCapture) -> None:
    """Auswertung of the roofer: top, one opened check, texts, competitors and trend."""
    capture.open(f"/auswertung/{AUDIT_SECOND}", "Bericht öffnen")
    capture.shoot("auswertung", settle_ms=CHART_SETTLE_MS)
    capture.shoot("auswertung-hebel", scroll_to="#levers-title")
    capture.page.locator(f"{REVIEWS_CHECK} summary").click()
    capture.shoot("auswertung-pruefung", scroll_to=REVIEWS_CHECK)
    capture.shoot("auswertung-texte", scroll_to="#posts")
    capture.shoot("auswertung-wettbewerber", scroll_to="#competitors", settle_ms=CHART_SETTLE_MS)
    capture.shoot("auswertung-verlauf", scroll_to="#trend", settle_ms=CHART_SETTLE_MS)


def capture_offer_and_settings(capture: ScreenCapture) -> None:
    """Angebot with its audit choice, the settings page on desktop, the install hint on phone."""
    capture.open("/angebot", "Letzte Prüfungen")
    capture.shoot("angebot")
    capture.open("/einstellungen", "Home-Bildschirm")
    if capture.viewport.mobile:
        capture.shoot_element("einstellungen-installieren", 'section[aria-labelledby="install"]')
    else:
        capture.shoot("einstellungen")


def capture_viewport(browser: Browser, base: str, output: Path, viewport: Viewport) -> None:
    """Capture every screen on one viewport in a fresh, signed-out context."""
    context: BrowserContext = browser.new_context(
        viewport={"width": viewport.width, "height": viewport.height},
        device_scale_factor=viewport.scale,
        is_mobile=viewport.mobile,
        has_touch=viewport.mobile,
        locale="de-DE",
        timezone_id="Europe/Berlin",
        service_workers="block",
    )
    context.route(
        f"{DEMO_SUPABASE_URL}/**", make_supabase_handler(PostgrestEmulator(demo_tables()))
    )
    page = context.new_page()
    page.on("pageerror", lambda error: logger.warning("Page error: %s", error))
    capture = ScreenCapture(page, base, output, viewport)
    try:
        sign_in(capture)
        capture_check(capture)
        capture_evaluation(capture)
        capture_offer_and_settings(capture)
    finally:
        context.close()


def parse_arguments() -> argparse.Namespace:
    """Read the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="folder for the PNGs")
    parser.add_argument(
        "--font",
        type=Path,
        default=None,
        help="woff2 file served as Archivo when app/public/fonts has none",
    )
    return parser.parse_args()


def main() -> None:
    """Build, serve and capture on phone and desktop."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = parse_arguments()
    if arguments.font is not None and not arguments.font.is_file():
        raise SystemExit(f"font file {arguments.font} does not exist")
    arguments.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="radarauditor-app-") as build_folder:
        build_app(Path(build_folder))
        with serve(Path(build_folder), arguments.font) as base, sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                args=["--lang=de-DE"],
                env={**os.environ, "LANG": "de_DE.UTF-8", "LANGUAGE": "de"},
            )
            try:
                for viewport in VIEWPORTS:
                    capture_viewport(browser, base, arguments.output, viewport)
            finally:
                browser.close()


if __name__ == "__main__":
    main()
