"""Shared fixtures: settings, catalogue, sample RawAudit, mocked repositories, fake browser.

Context: External services are never called in tests. | Input: Nothing. | Output: Fixtures.
"""

import random
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import HttpUrl, SecretStr
from tenacity import wait_none

from radarauditor.collectors import transport
from radarauditor.collectors.browser import BrowserService, ContextFactory, Pacing
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import (
    AccessSnapshot,
    AnalyticsSnapshot,
    AssistantAnswer,
    BacklinkSummary,
    DirectoryHit,
    GapKeyword,
    PageSnapshot,
    PageSpeedSnapshot,
    PaidSnapshot,
    PlaceSnapshot,
    RawAudit,
    SearchQuery,
    SerpResult,
    WebsiteSnapshot,
)
from radarauditor.config import Settings, load_settings
from radarauditor.db.app_settings import Source, SourcesSettings
from radarauditor.db.models import (
    Audit,
    AuditKind,
    AuditStatus,
    CheckOutcome,
    CheckResult,
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
from radarauditor.errors import StorageError
from radarauditor.reporting.texts import ReportTexts
from radarauditor.scoring.catalog import Catalogue, load_catalogue
from radarauditor.scoring.evaluator import evaluate

CUSTOMER_ID = UUID("00000000-0000-4000-8000-000000000001")
AUDIT_ID = UUID("00000000-0000-4000-8000-000000000002")
JOB_ID = UUID("00000000-0000-4000-8000-000000000003")
PREVIOUS_AUDIT_ID = UUID("00000000-0000-4000-8000-000000000004")
FIXED_TIME = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

MINIMAL_CATALOGUE = """\
areas:
  - id: a
    name: Technische Grundlage der Website
    weight: 100
    checks:
      - id: a1
        name: HTTPS und eine einzige Adresse
        why: Ohne Schloss warnt Google.
        source: crawler
        requires_paid: false
        requires_access: false
        thresholds: {}
        customer_question: Ist deine Seite verschlüsselt?
        customer_impact: Ohne Schloss im Browser warnt Google Besucher.
      - id: a2
        name: Ladezeit mobil und Desktop
        why: Warten kostet Anrufe.
        source: pagespeed
        requires_paid: false
        requires_access: false
        thresholds: {lcp_ms_max: 2500}
        customer_question: Lädt deine Seite schnell?
        customer_impact: Jede Sekunde mehr kostet Anrufe.
"""

FORECAST_FILE = Path("config") / "prognose.yaml"
INDUSTRIES_FILE = Path("config") / "branchen.yaml"
DUMMY_ENVIRONMENT = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_KEY": "service-key-for-tests",
    "GOOGLE_API_KEY": "google-key-for-tests",
}


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Set dummy environment variables, isolate from any .env file and return the work dir."""
    monkeypatch.chdir(tmp_path)
    for name, value in DUMMY_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    work_dir = tmp_path / "work"
    monkeypatch.setenv("RADAR_WORK_DIR", str(work_dir))
    catalogue_file = tmp_path / "pruefkatalog.yaml"
    catalogue_file.write_text(MINIMAL_CATALOGUE, encoding="utf-8")
    monkeypatch.setenv("CATALOGUE_PATH", str(catalogue_file))
    monkeypatch.setenv("FORECAST_PATH", str(Path(__file__).resolve().parents[1] / FORECAST_FILE))
    monkeypatch.setenv(
        "INDUSTRIES_PATH", str(Path(__file__).resolve().parents[1] / INDUSTRIES_FILE)
    )
    # Tests must never start the real Claude Code; tests of the headless run use a fake script.
    monkeypatch.setenv("ANALYSIS_CLAUDE_COMMAND", "claude-not-installed-in-tests")
    monkeypatch.delenv("EVENT_ENDPOINT", raising=False)
    return work_dir


@pytest.fixture
def settings(environment: Path) -> Settings:
    """Settings built from dummy environment values."""
    return load_settings()


@pytest.fixture
def sample_customer() -> Customer:
    """A business with a website in Münster."""
    return Customer(
        id=CUSTOMER_ID,
        name="Dachdeckerei Muster",
        website="https://dachdecker-muster.example",  # type: ignore[arg-type]
        industry="Dachdecker",
        city="Münster",
        created_at=FIXED_TIME,
    )


@pytest.fixture
def sample_audit() -> Audit:
    """A scored audit waiting for analysis, with raw data in storage."""
    return Audit(
        id=AUDIT_ID,
        customer_id=CUSTOMER_ID,
        kind=AuditKind.REMOTE,
        status=AuditStatus.AWAITING_ANALYSIS,
        requested_at=FIXED_TIME,
        finished_at=None,
        total_score=Decimal("61.50"),
        raw_data_path=f"{AUDIT_ID}/raw.json",
        report_path=None,
    )


@pytest.fixture
def sample_check_results() -> list[CheckResult]:
    """Two check results of the sample audit."""
    return [
        CheckResult(
            id=UUID("00000000-0000-4000-8000-0000000000a1"),
            audit_id=AUDIT_ID,
            check_id="a1",
            area="a",
            outcome=CheckOutcome.FAILED,
            measured={"uses_https": False},
            weight=Decimal("1.000"),
        ),
        CheckResult(
            id=UUID("00000000-0000-4000-8000-0000000000a2"),
            audit_id=AUDIT_ID,
            check_id="a2",
            area="a",
            outcome=CheckOutcome.PARTIAL,
            measured={"lcp_ms": 3100},
            weight=Decimal("1.000"),
        ),
    ]


@pytest.fixture
def sample_raw_audit() -> RawAudit:
    """A RawAudit without any collected source."""
    return RawAudit(
        website=None,
        pagespeed=None,
        place=None,
        competitor_places=[],
        serps=[],
        directories=[],
        assistant_answers=[],
        collected_at=FIXED_TIME,
        warnings=["website not reachable"],
    )


JobFactory = Callable[..., Job]


@pytest.fixture
def make_job() -> JobFactory:
    """Build running jobs of the sample customer from kind, attempts and audit id."""

    def build(kind: JobKind, attempts: int = 1, audit_id: UUID | None = AUDIT_ID) -> Job:
        return Job(
            id=JOB_ID,
            kind=kind,
            status=JobStatus.RUNNING,
            audit_id=audit_id,
            customer_id=CUSTOMER_ID,
            attempts=attempts,
            error=None,
            created_at=FIXED_TIME,
            updated_at=FIXED_TIME,
        )

    return build


@pytest.fixture
def fake_repositories(
    sample_customer: Customer,
    sample_audit: Audit,
    sample_check_results: list[CheckResult],
) -> MagicMock:
    """Repositories double with the sample customer, audit and results; no queued jobs.

    Every method of Repositories exists with its real signature (spec); override return values
    per test.
    """
    repositories = MagicMock(spec=Repositories)
    repositories.customer.return_value = sample_customer
    repositories.audit.return_value = sample_audit
    repositories.locations.return_value = []
    repositories.competitors.return_value = []
    repositories.keywords.return_value = []
    repositories.check_results.return_value = sample_check_results
    repositories.previous_audit.return_value = None
    repositories.rankings.return_value = []
    repositories.claim_jobs.return_value = []
    repositories.claim_mail_triage.return_value = []
    repositories.claim_assistant_request.return_value = None
    repositories.claim_prospect_search.return_value = None
    repositories.queue_monthly_service.return_value = None
    repositories.requeue_running_jobs.return_value = 0
    repositories.pending_events.return_value = []
    repositories.pending_documents.return_value = []
    repositories.settings_value.return_value = None
    repositories.settings_values.return_value = {}
    repositories.consume_source_quota.return_value = True
    repositories.customer_access.return_value = None
    repositories.upload.side_effect = lambda bucket, remote_path, local_file: remote_path
    repositories.download.side_effect = lambda bucket, remote_path, local_file: local_file
    return repositories


ALL_SOURCES_ON = SourcesSettings.model_validate(
    {source.value: {"enabled": True} for source in Source}
)


@pytest.fixture
def open_gate() -> SourceGate:
    """Gate with every source switched on and every request within its cap."""
    return SourceGate(ALL_SOURCES_ON, lambda source, cap: True)


FIXTURES_DIR = Path(__file__).parent / "fixtures"
QUESTIONS_PATH = Path(__file__).resolve().parents[1] / "config" / "fragen.yaml"


@pytest.fixture
def collector_settings() -> Settings:
    """Settings with dummy keys, no DataForSEO credentials and no assistant providers."""
    return Settings(
        _env_file=None,
        supabase_url="https://example.supabase.co",
        supabase_service_key=SecretStr("service-key"),
        google_api_key=SecretStr("google-key"),
        dataforseo_login=None,
        dataforseo_password=None,
        openai_api_key=None,
        gemini_api_key=None,
        perplexity_api_key=None,
        questions_path=QUESTIONS_PATH,
        industries_path=QUESTIONS_PATH.with_name("branchen.yaml"),
    )


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the backoff wait between retries of external calls."""
    monkeypatch.setattr(transport, "RETRY_WAIT", wait_none())


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory of saved HTML and JSON fixtures."""
    return FIXTURES_DIR


CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "config" / "pruefkatalog.yaml"
SITE = "https://www.dachdeckerei-muster.de"
GOOGLE_MAPS_URL = "https://maps.google.com/?cid=123"
COLLECTED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

PAGE_SPECS: list[dict[str, Any]] = [
    {
        "path": "/",
        "title": "Dachdecker Münster – Dachdeckerei Muster GmbH",
        "h1": "Dachdecker in Münster",
        "headings": [(2, "Unsere Leistungen"), (3, "Dachsanierung"), (3, "Dachfenster")],
        "words": 650,
        "text": "Ihr Dachdecker in Münster seit 1990, Stand 2026. Wir decken Dächer in Münster "
        "und Umgebung, Hauptstraße 5, 48143 Münster.",
    },
    {
        "path": "/dachsanierung/",
        "title": "Dachsanierung in Münster: Ablauf, Dauer, Preise",
        "h1": "Dachsanierung in Münster",
        "headings": [
            (2, "Ablauf der Sanierung"),
            (2, "Häufige Fragen"),
            (3, "Was kostet eine Dachsanierung?"),
            (3, "Wie lange dauert eine Dachsanierung?"),
        ],
        "words": 820,
        "text": "Eine Dachsanierung in Münster kostet je nach Fläche unterschiedlich viel und "
        "dauert meist zwei Wochen.",
    },
    {
        "path": "/kontakt/",
        "title": "Kontakt zur Dachdeckerei Muster in Münster",
        "h1": "Kontakt",
        "headings": [(2, "Anfahrt")],
        "words": 150,
        "text": "Rufen Sie uns an. Dachdeckerei Muster GmbH, Hauptstraße 5, 48143 Münster, "
        "Telefon 0251 123456.",
    },
    {
        "path": "/ueber-uns/",
        "title": "Über uns: das Team der Dachdeckerei Muster",
        "h1": "Über uns",
        "headings": [(2, "Unser Team")],
        "words": 400,
        "text": "Dachdeckermeister Max Muster führt den Betrieb mit sechs Gesellen.",
    },
    {
        "path": "/impressum/",
        "title": "Impressum der Dachdeckerei Muster GmbH",
        "h1": "Impressum",
        "headings": [],
        "words": 200,
        "text": "Dachdeckerei Muster GmbH, Hauptstraße 5, 48143 Münster, Telefon 0251 123456, "
        "Geschäftsführer Max Muster.",
    },
    {
        "path": "/datenschutz/",
        "title": "Datenschutzerklärung der Dachdeckerei Muster",
        "h1": "Datenschutz",
        "headings": [(2, "Verantwortlicher")],
        "words": 1500,
        "text": "Wir verarbeiten personenbezogene Daten nur nach den gesetzlichen Vorgaben. "
        "Auf der Kontaktseite binden wir Google Maps erst nach Ihrer Einwilligung ein. "
        "Cookies setzen wir nur, wenn sie technisch notwendig sind.",
    },
    {
        "path": "/ratgeber/",
        "title": "Ratgeber rund ums Dach in Münster und Umgebung",
        "h1": "Ratgeber",
        "headings": [],
        "words": 120,
        "text": "Tipps unserer Dachdecker für Hausbesitzer im Münsterland.",
    },
]
POST_TOPICS = ["dachrinne-reinigen", "sturmschaden", "dachbegruenung", "solar-dach", "dachdaemmung"]


def _filler(index: int) -> str:
    """Words unique to one page so the duplicate-content check sees distinct texts."""
    return " ".join(f"seite{index}wort{number}" for number in range(15))


def _page(index: int, spec: dict[str, Any], links: list[str]) -> PageSnapshot:
    url = f"{SITE}{spec['path']}"
    json_ld: list[dict[str, object]] = []
    if spec["path"] != "/":
        json_ld.append(
            {
                "@context": "https://schema.org",
                "@type": "BreadcrumbList",
                "itemListElement": [{"@type": "ListItem", "position": 1, "name": "Start"}],
            }
        )
    return PageSnapshot(
        url=url,
        status_code=200,
        title=spec["title"],
        meta_description=(
            f"{spec['title']}. Ihr Meisterbetrieb für Dacharbeiten in Münster, "
            f"schnell erreichbar und zuverlässig (Seite {index})."
        ),
        canonical=url,
        lang="de-DE",
        headings=[(1, spec["h1"]), *spec["headings"]],
        word_count=spec["words"],
        json_ld=json_ld,
        open_graph={"og:title": spec["title"], "og:description": "Dach", "og:image": "bild.jpg"},
        internal_links=links,
        images_without_alt=0,
        oversized_images=0,
        tel_links=["tel:+49251123456"],
        mailto_links=["mailto:info@dachdeckerei-muster.de"],
        text_excerpt=f"{spec['text']} {_filler(index)}",
        has_viewport_meta=True,
    )


def _start_page_schema() -> list[dict[str, object]]:
    return [
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "RoofingContractor",
                    "name": "Dachdeckerei Muster GmbH",
                    "url": f"{SITE}/",
                    "telephone": "+49 251 123456",
                    "logo": f"{SITE}/logo.png",
                    "address": {
                        "@type": "PostalAddress",
                        "streetAddress": "Hauptstraße 5",
                        "postalCode": "48143",
                        "addressLocality": "Münster",
                    },
                    "geo": {"@type": "GeoCoordinates", "latitude": 51.96, "longitude": 7.62},
                    "openingHoursSpecification": [{"@type": "OpeningHoursSpecification"}],
                    "sameAs": [
                        "https://maps.google.com/?cid=123",
                        "https://www.facebook.com/dachdeckereimuster",
                        "https://www.wikidata.org/wiki/Q1",
                    ],
                },
                {"@type": "Service", "name": "Dachdecker", "areaServed": "Münster"},
            ],
        }
    ]


def _service_schema() -> list[dict[str, object]]:
    return [
        {
            "@context": "https://schema.org",
            "@type": "Service",
            "name": "Dachsanierung",
            "areaServed": {"@type": "City", "name": "Münster"},
        },
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": "Was kostet eine Dachsanierung?"}],
        },
    ]


def build_website() -> WebsiteSnapshot:
    """A small, well-built roofer website that passes the crawler checks."""
    post_urls = [f"{SITE}/ratgeber/{topic}/" for topic in POST_TOPICS]
    top_urls = [f"{SITE}{spec['path']}" for spec in PAGE_SPECS]
    pages = []
    for index, spec in enumerate(PAGE_SPECS):
        links = top_urls if spec["path"] == "/" else [f"{SITE}/", f"{SITE}/impressum/"]
        if spec["path"] == "/ratgeber/":
            links = [*links, *post_urls]
        pages.append(_page(index, spec, links))
    pages[0] = pages[0].model_copy(
        update={
            "json_ld": _start_page_schema(),
            "external_links": [
                HttpUrl("https://www.hwk-muenster.de/"),
                HttpUrl("https://www.dachdecker.org/"),
                HttpUrl("https://www.facebook.com/dachdeckereimuster"),
                HttpUrl("https://wa.me/49251123456"),
            ],
            "script_sources": [
                "https://www.googletagmanager.com/gtag/js?id=G-MUSTER1234",
                "https://embed.tawk.to/muster/default",
            ],
            "inline_script_signatures": [],
            "google_tag_ids": ["G-MUSTER1234", "AW-123456789"],
            "ads_conversions": ["AW-123456789/AnfrageForm"],
        }
    )
    pages[2] = pages[2].model_copy(
        update={
            "external_links": [HttpUrl(GOOGLE_MAPS_URL)],
            "third_party_hosts": ["maps.googleapis.com", "maps.gstatic.com"],
        }
    )
    pages[1] = pages[1].model_copy(update={"json_ld": [*pages[1].json_ld, *_service_schema()]})
    for offset, topic in enumerate(POST_TOPICS):
        spec = {
            "path": f"/ratgeber/{topic}/",
            "title": f"Ratgeber {topic.replace('-', ' ').title()} in Münster",
            "h1": topic.replace("-", " ").title(),
            "headings": [(2, "Worauf es ankommt")],
            "words": 500,
            "text": f"Ein Beitrag aus Münster zum Thema {topic}.",
        }
        pages.append(_page(len(PAGE_SPECS) + offset, spec, [f"{SITE}/", f"{SITE}/ratgeber/"]))
    probe = pages[0].model_copy(
        update={"url": HttpUrl(f"{SITE}/radar-404-probe"), "status_code": 404, "json_ld": []}
    )
    pages.append(probe)
    return WebsiteSnapshot(
        start_url=f"{SITE}/",
        uses_https=True,
        redirects_to_single_host=True,
        robots_txt=f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n",
        sitemap_urls=[f"{SITE}/sitemap.xml"],
        legal_notice_url=f"{SITE}/impressum/",
        privacy_url=f"{SITE}/datenschutz/",
        footer_nap={
            "name": "Dachdeckerei Muster GmbH",
            "street": "Hauptstr. 5",
            "postal_code": "48143",
            "city": "Münster",
            "phone": "0251 123456",
        },
        opening_hours_text="Mo-Fr 7-17 Uhr",
        pages=pages,
        crawl_errors=[],
    )


PROFILE_DESCRIPTION = (
    "Dachdecker in Münster mit eigener Meisterwerkstatt: Wir übernehmen Dachsanierung, "
    "Steildach, Flachdach, Dachfenster und Reparaturen nach Sturmschäden für Privathaushalte, "
    "Hausverwaltungen und Gewerbe im ganzen Münsterland. Seit über dreißig Jahren arbeiten wir "
    "mit festen Teams, sauberen Baustellen und verbindlichen Terminen. Vor jedem Auftrag sehen "
    "wir uns das Dach vor Ort an, erklären verständlich, was wirklich nötig ist, und erstellen "
    "ein Festpreisangebot ohne versteckte Kosten. Wir dämmen nach aktuellem "
    "Gebäudeenergiegesetz, beraten zu Fördermitteln und kümmern uns um die Abstimmung mit "
    "Gerüstbau und Klempner. Notdienst bei Sturmschäden rund um die Uhr."
)


def build_place(name: str = "Dachdeckerei Muster GmbH", **overrides: Any) -> PlaceSnapshot:
    """The customer's Google profile with five recent, answered reviews."""
    reviews: list[dict[str, object]] = [
        {
            "publishTime": f"2026-{month:02d}-{day:02d}T10:00:00Z",
            "rating": rating,
            "text": {"text": text},
            "ownerReply": {
                "text": "Danke",
                "publishTime": f"2026-{month:02d}-{day + 1:02d}T10:00:00Z",
            },
        }
        for month, day, rating, text in [
            (9, 28, 5, "Super Dachdecker aus Münster, schnelle Hilfe."),
            (9, 15, 5, "Dachsanierung pünktlich erledigt."),
            (9, 2, 4, "Gute Arbeit am Dach in Münster."),
            (8, 20, 5, "Sehr freundlich."),
            (8, 10, 2, "Termin wurde verschoben."),
        ]
    ]
    values: dict[str, Any] = {
        "place_id": "place-customer",
        "name": name,
        "formatted_address": "Hauptstraße 5, 48143 Münster, Deutschland",
        "phone": "+49 251 123456",
        "website": f"{SITE}/",
        "primary_type": "roofing_contractor",
        "types": [
            "roofing_contractor",
            "general_contractor",
            "home_goods_store",
            "contractor",
            "point_of_interest",
            "establishment",
        ],
        "rating": 4.8,
        "review_count": 120,
        "has_opening_hours": True,
        "photo_count": 10,
        "recent_reviews": reviews,
        "business_status": "OPERATIONAL",
        "google_maps_uri": GOOGLE_MAPS_URL,
        "description": PROFILE_DESCRIPTION,
        "services": ["Dachsanierung", "Dachdecker Notdienst", "Flachdach", "Dachfenster"],
        "products": ["Dachfenster Velux", "Dachrinne Zink"],
        "attributes": ["Barzahlung", "Rollstuhlgerechter Eingang", "Termin erforderlich"],
        "service_areas": ["Münster", "Telgte", "Greven"],
        "post_ages_days": [12, 41],
        "question_count": 3,
        "photo_newest_date": date(2026, 9, 14),
        "photo_dated_count": 8,
    }
    values.update(overrides)
    return PlaceSnapshot(**values)


def build_competitor(name: str, reviews: int, primary_type: str) -> PlaceSnapshot:
    """A competing profile with only the fields comparisons use."""
    return build_place(
        name=name,
        place_id=f"place-{name}",
        review_count=reviews,
        primary_type=primary_type,
        website=None,
        recent_reviews=[],
    )


def build_serps() -> list[SerpResult]:
    """Two service searches, one brand search, one Bing search and one image search.

    Bing and image results hold bare domains, as the browser collectors record them.
    """
    own = f"{SITE}/"
    return [
        SerpResult(
            phrase="Dachdecker Münster",
            organic=[(1, "https://www.gelbeseiten.de/dachdecker"), (2, own)],
            local_pack=[(1, "Dachdeckerei Muster GmbH"), (2, "Dach Schmidt")],
            ai_overview_present=True,
            ai_overview_mentions=["Dachdeckerei Muster", "Dach Schmidt"],
            people_also_ask=["Was kostet eine Dachsanierung?"],
            measured_at=COLLECTED_AT,
        ),
        SerpResult(
            phrase="Dachsanierung Münster",
            organic=[(5, f"{SITE}/dachsanierung/")],
            local_pack=[(2, "Dachdeckerei Muster")],
            ai_overview_present=False,
            ai_overview_mentions=[],
            people_also_ask=["Wie lange dauert eine Dachsanierung?"],
            measured_at=COLLECTED_AT,
        ),
        SerpResult(
            phrase="Dachdeckerei Muster",
            organic=[(1, own), (2, "https://www.facebook.com/dachdeckereimuster")],
            local_pack=[],
            ai_overview_present=False,
            ai_overview_mentions=[],
            people_also_ask=[],
            measured_at=COLLECTED_AT,
            ads=["dachdeckerei-muster.de"],
            knowledge_panel=True,
        ),
        SerpResult(
            phrase="Dachdecker Münster",
            organic=[(1, "gelbeseiten.de"), (2, "dachdeckerei-muster.de")],
            local_pack=[],
            ai_overview_present=False,
            ai_overview_mentions=[],
            people_also_ask=[],
            measured_at=COLLECTED_AT,
            engine="bing",
        ),
        SerpResult(
            phrase="Dachdecker Münster",
            organic=[(1, "pinterest.de"), (2, "dachdeckerei-muster.de")],
            local_pack=[],
            ai_overview_present=False,
            ai_overview_mentions=[],
            people_also_ask=[],
            measured_at=COLLECTED_AT,
            vertical="images",
        ),
    ]


def build_directories() -> list[DirectoryHit]:
    """Listings with one consistent phone and address."""
    found = {
        "Apple Business Connect": "https://maps.apple.com/place?id=1",
        "Bing Places": "https://www.bing.com/maps?cid=1",
        "Gelbe Seiten": "https://www.gelbeseiten.de/gsbiz/1",
        "Das Örtliche": "https://www.dasoertliche.de/1",
        "11880": "https://www.11880.com/1",
        "Handwerkskammer": "https://www.hwk-muenster.de/1",
        "Facebook": "https://www.facebook.com/dachdeckereimuster",
        "Instagram": "https://www.instagram.com/dachdeckereimuster",
        "ProvenExpert": "https://www.provenexpert.com/muster",
        "Wikidata": "https://www.wikidata.org/wiki/Q1",
    }
    hits = [
        DirectoryHit(
            directory=directory,
            found=True,
            listing_url=url,
            name="Dachdeckerei Muster GmbH",
            address="Hauptstr. 5, 48143 Münster",
            phone="0251 / 123456",
        )
        for directory, url in found.items()
    ]
    hits.append(
        DirectoryHit(
            directory="Cylex", found=False, listing_url=None, name=None, address=None, phone=None
        )
    )
    return hits


def build_answers() -> list[AssistantAnswer]:
    """Ten recommendation answers (half naming the business) and ten problem answers (two)."""
    recommendation = "Welchen Dachdecker in Münster empfiehlst du?"
    problem = "Mein Dach ist undicht, was kann ich in Münster tun?"
    answers = []
    for index in range(10):
        answers.append(
            AssistantAnswer(
                provider="openai" if index % 2 else "gemini",
                question=recommendation,
                mentioned=index < 5,
                cited_sources=[f"{SITE}/", "https://www.gelbeseiten.de/dachdecker"],
                answer_excerpt="Dachdeckerei Muster GmbH, Telefon 0251 123456."
                if index < 5
                else "Dach Schmidt ist bekannt.",
            )
        )
        answers.append(
            AssistantAnswer(
                provider="openai",
                question=problem,
                mentioned=index < 2,
                cited_sources=[],
                answer_excerpt="Fragen Sie die Dachdeckerei Muster in 48143 Münster."
                if index < 2
                else "Rufen Sie einen Dachdecker.",
            )
        )
    return answers


PROFILE_NOW = {
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": 310,
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS": 1240,
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": 520,
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": 1880,
    "CALL_CLICKS": 42,
    "WEBSITE_CLICKS": 63,
    "BUSINESS_DIRECTION_REQUESTS": 18,
    "BUSINESS_CONVERSATIONS": 0,
    "BUSINESS_BOOKINGS": 0,
}
PROFILE_BEFORE = {
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": 290,
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS": 1100,
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": 540,
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": 1700,
    "CALL_CLICKS": 35,
    "WEBSITE_CLICKS": 60,
    "BUSINESS_DIRECTION_REQUESTS": 20,
    "BUSINESS_CONVERSATIONS": 0,
    "BUSINESS_BOOKINGS": 0,
}
CUSTOMER_DOMAIN = "dachdeckerei-muster.de"


def owner_review(day: str, rating: int, reply_day: str | None) -> dict[str, object]:
    """A profile review as the access collector stores it, answered on reply_day."""
    return {
        "publishTime": f"{day}T10:00:00Z",
        "rating": float(rating),
        "text": "Gute Arbeit am Dach in Münster.",
        "reviewReply": (
            {"text": "Vielen Dank!", "updateTime": f"{reply_day}T09:00:00Z"} if reply_day else None
        ),
    }


def build_access() -> AccessSnapshot:
    """Profile figures that grew, matching Search Console queries, enquiries, answered reviews."""
    return AccessSnapshot(
        profile_metrics=PROFILE_NOW,
        profile_metrics_previous=PROFILE_BEFORE,
        search_queries=[
            SearchQuery(
                query="dachdeckerei muster", clicks=40, impressions=210, ctr=0.19, position=1.2
            ),
            SearchQuery(
                query="dachdecker münster", clicks=31, impressions=1650, ctr=0.019, position=4.1
            ),
            SearchQuery(
                query="dachsanierung münster", clicks=12, impressions=480, ctr=0.025, position=6.3
            ),
        ],
        search_totals={"clicks": 120.0, "impressions": 4300.0, "ctr": 0.028, "position": 8.4},
        analytics=AnalyticsSnapshot(
            property_id="123456789", sessions=640, engaged_sessions=410, key_events=23
        ),
        owner_reviews=[
            owner_review("2026-09-28", 5, "2026-09-29"),
            owner_review("2026-09-15", 5, "2026-09-16"),
            owner_review("2026-09-02", 4, "2026-09-03"),
            owner_review("2026-08-20", 5, "2026-08-22"),
            owner_review("2026-08-10", 2, "2026-08-11"),
        ],
        collected_at=COLLECTED_AT,
    )


OwnerReviewFactory = Callable[[str, int, str | None], dict[str, object]]


@pytest.fixture
def make_owner_review() -> OwnerReviewFactory:
    """Factory for profile reviews: review day, stars, reply day or None."""
    return owner_review


@pytest.fixture
def access_snapshot() -> AccessSnapshot:
    """The access data of the full audit."""
    return build_access()


def build_paid() -> PaidSnapshot:
    """Volumes, a link profile ahead of most rivals, a small gap and rising visibility."""
    return PaidSnapshot(
        customer_domain=CUSTOMER_DOMAIN,
        volumes={"Dachdecker Münster": 880, "Dachsanierung Münster": 170},
        primary_phrases=["Dachdecker Münster"],
        backlinks={
            CUSTOMER_DOMAIN: BacklinkSummary(
                domain=CUSTOMER_DOMAIN,
                referring_domains=48,
                referring_domains_local_share=0.42,
                spam_score=3,
                referring_domains_spam_share=0.04,
            ),
            "dach-schmidt.de": BacklinkSummary(domain="dach-schmidt.de", referring_domains=52),
            "bedachungen-meier.de": BacklinkSummary(
                domain="bedachungen-meier.de", referring_domains=40
            ),
        },
        ranked_keywords={CUSTOMER_DOMAIN: 140, "dach-schmidt.de": 150, "bedachungen-meier.de": 90},
        keyword_gap=[
            GapKeyword(
                phrase="dachrinne reinigen münster",
                volume=9,
                competitor_domain="dach-schmidt.de",
                competitor_position=4,
            ),
            GapKeyword(
                phrase="gaubenverkleidung münster",
                volume=5,
                competitor_domain="bedachungen-meier.de",
                competitor_position=None,
            ),
        ],
        historic_visibility=[(f"2025-{month:02d}", 18.0 + month) for month in range(10, 13)]
        + [(f"2026-{month:02d}", 30.0 + month) for month in range(1, 10)],
        collected_at=COLLECTED_AT,
    )


@pytest.fixture
def catalogue() -> Catalogue:
    """The real check catalogue from config/."""
    return load_catalogue(CATALOGUE_PATH)


@pytest.fixture
def raw_audit_full() -> RawAudit:
    """A well-run roofer with every source, access and paid data; passes most checks."""
    return RawAudit(
        website=build_website(),
        pagespeed=PageSpeedSnapshot(
            url=f"{SITE}/",
            mobile_performance=85,
            desktop_performance=97,
            lcp_ms=2100,
            cls=0.05,
            inp_ms=150,
            accessibility=92,
        ),
        place=build_place(),
        competitor_places=[
            build_competitor("Dach Schmidt", 84, "roofing_contractor"),
            build_competitor("Bedachungen Meier", 95, "roofing_contractor"),
            build_competitor("Bau Krause", 60, "general_contractor"),
        ],
        serps=build_serps(),
        directories=build_directories(),
        assistant_answers=build_answers(),
        collected_at=COLLECTED_AT,
        warnings=[],
        access=build_access(),
        paid=build_paid(),
    )


@pytest.fixture
def raw_audit_minimal() -> RawAudit:
    """An audit where no collector returned anything."""
    return RawAudit(
        website=None,
        pagespeed=None,
        place=None,
        competitor_places=[],
        serps=[],
        directories=[],
        assistant_answers=[],
        collected_at=COLLECTED_AT,
        warnings=[],
    )


@pytest.fixture
def make_page() -> Callable[..., PageSnapshot]:
    """Factory for a single page with sensible defaults, overridable per field."""

    def factory(path: str = "/seite/", **overrides: Any) -> PageSnapshot:
        spec = {
            "path": path,
            "title": "Testseite für Dacharbeiten in Münster und Umgebung",
            "h1": "Testseite",
            "headings": [],
            "words": 400,
            "text": "Text",
        }
        page = _page(99, spec, [f"{SITE}/"])
        return page.model_copy(update=overrides)

    return factory


@pytest.fixture
def with_website() -> Callable[..., RawAudit]:
    """Copy of an audit with website fields replaced."""

    def factory(raw: RawAudit, **updates: Any) -> RawAudit:
        assert raw.website is not None
        return raw.model_copy(update={"website": raw.website.model_copy(update=updates)})

    return factory


@pytest.fixture
def with_place() -> Callable[..., RawAudit]:
    """Copy of an audit with Google profile fields replaced."""

    def factory(raw: RawAudit, **updates: Any) -> RawAudit:
        assert raw.place is not None
        return raw.model_copy(update={"place": raw.place.model_copy(update=updates)})

    return factory


REPORT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PRIMARY_KEYWORD_ID = UUID("00000000-0000-4000-8000-000000000101")
REPORT_SECOND_KEYWORD_ID = UUID("00000000-0000-4000-8000-000000000102")
REPORT_UNMEASURED_KEYWORD_ID = UUID("00000000-0000-4000-8000-000000000103")


@pytest.fixture
def report_settings(settings: Settings) -> Settings:
    """Settings pointing at the real catalogue, forecast curves and templates."""
    return settings.model_copy(
        update={
            "catalogue_path": CATALOGUE_PATH,
            "forecast_path": REPORT_ROOT / "config" / "prognose.yaml",
            "templates_dir": REPORT_ROOT / "templates",
            "packages_path": REPORT_ROOT / "config" / "pakete.yaml",
        }
    )


@pytest.fixture
def report_forecast_path() -> Path:
    """The real forecast curves."""
    return REPORT_ROOT / "config" / "prognose.yaml"


@pytest.fixture
def report_keywords() -> list[Keyword]:
    """A primary keyword with volume, one without volume and one never measured."""
    return [
        Keyword(
            id=REPORT_PRIMARY_KEYWORD_ID,
            customer_id=CUSTOMER_ID,
            phrase="Dachdecker Münster",
            city="Münster",
            monthly_volume=880,
            is_primary=True,
        ),
        Keyword(
            id=REPORT_SECOND_KEYWORD_ID,
            customer_id=CUSTOMER_ID,
            phrase="Dachsanierung Münster",
            city="Münster",
            monthly_volume=None,
            is_primary=False,
        ),
        Keyword(
            id=REPORT_UNMEASURED_KEYWORD_ID,
            customer_id=CUSTOMER_ID,
            phrase="Dachfenster Münster",
            city="Münster",
            monthly_volume=90,
            is_primary=False,
        ),
    ]


def _report_ranking(
    keyword_id: UUID,
    subject: str,
    organic: int | None,
    local: int | None,
    grid_point: str | None = None,
    audit_id: UUID = AUDIT_ID,
) -> Ranking:
    return Ranking(
        id=uuid4(),
        audit_id=audit_id,
        keyword_id=keyword_id,
        subject=subject,
        organic_position=organic,
        local_pack_position=local,
        grid_point=grid_point,
        measured_at=COLLECTED_AT,
    )


@pytest.fixture
def report_make_ranking() -> Callable[..., Ranking]:
    """Factory for one ranking row: keyword id, subject, organic, local, grid point, audit id."""
    return _report_ranking


@pytest.fixture
def report_rankings() -> list[Ranking]:
    """Customer and competitor positions for two keywords plus a 3x3 grid of the primary one."""
    rows = [
        _report_ranking(REPORT_PRIMARY_KEYWORD_ID, "customer", 2, 1),
        _report_ranking(REPORT_PRIMARY_KEYWORD_ID, "Dach Schmidt", 4, 2),
        _report_ranking(REPORT_SECOND_KEYWORD_ID, "customer", 5, None),
        _report_ranking(REPORT_SECOND_KEYWORD_ID, "Dach Schmidt", 1, 1),
    ]
    grid = [None, 4, 2, 1, 1, 3, 12, 8, None]
    for index, position in enumerate(grid):
        point = f"{51.95 + index // 3 * 0.009:.5f},{7.62 + index % 3 * 0.014:.5f}"
        rows.append(_report_ranking(REPORT_PRIMARY_KEYWORD_ID, "customer", None, position, point))
    return rows


@pytest.fixture
def report_texts() -> ReportTexts:
    """Report prose as Claude Code writes it."""
    return ReportTexts.model_validate(
        {
            "introduction": "Sie werden in Münster gut gefunden, verschenken aber Anfragen.",
            "summary": "Ihre Website ist solide. Ihr Google-Profil ist stark. "
            "KI-Assistenten nennen Sie nur in jeder zweiten Antwort.",
            "forecast_note": "Die Spanne ist eine Schätzung auf Basis Ihrer heutigen Plätze.",
            "subscription_reason": "Ihre Wettbewerber holen bei den Bewertungen auf.",
            "offer_text": "Erstoptimierung und monatliche Betreuung zum Festpreis.",
            "talk_text": "Sie stehen bei Google Maps hinter drei Nachbarn. Der größte Hebel sind "
            "die Bewertungen: Sie haben 12, Ihre Nachbarn im Schnitt 48. Dazu fehlen "
            "Leistungsseiten und Ihr Profil zeigt keine Beiträge. Geschätzt bringt das zwei "
            "bis fünf Anfragen mehr im Monat; das ist eine Schätzung.",
            "post_suggestions": ["Herbst-Check für Ihr Dach: jetzt Termin sichern."],
            "review_replies": [
                {"review_excerpt": "Termin wurde verschoben.", "reply": "Das tut uns leid."}
            ],
        }
    )


@pytest.fixture
def report_check_results(raw_audit_full: RawAudit, catalogue: Catalogue) -> list[CheckResult]:
    """Real check results of the full audit, every sixth measurable check failed or partial."""
    results = evaluate(AUDIT_ID, raw_audit_full, catalogue)
    degraded = {0: CheckOutcome.FAILED, 3: CheckOutcome.PARTIAL}
    measurable = [r for r in results if r.outcome is not CheckOutcome.NOT_MEASURABLE]
    changed = {
        result.check_id: degraded[index % 6]
        for index, result in enumerate(measurable)
        if index % 6 in degraded
    }
    return [
        result.model_copy(update={"outcome": changed[result.check_id]})
        if result.check_id in changed
        else result
        for result in results
    ]


@pytest.fixture
def report_findings(report_check_results: list[CheckResult]) -> list[Finding]:
    """One finding per check that did not pass, severities 3, 2, 1 in turn."""
    open_results = [
        result
        for result in report_check_results
        if result.outcome in (CheckOutcome.FAILED, CheckOutcome.PARTIAL)
    ]
    return [
        Finding(
            id=uuid4(),
            audit_id=AUDIT_ID,
            check_id=result.check_id,
            severity=3 - index % 3,
            title=f"Befund zu Prüfung {result.check_id}",
            explanation=f"Gemessen: {result.measured}",
            customer_impact="Das kostet Sie Anfragen.",
            fix=f"Maßnahme für {result.check_id}",
            fix_payload=None,
        )
        for index, result in enumerate(open_results)
    ]


@pytest.fixture
def report_location() -> Location:
    """The customer's main location."""
    return Location(
        id=uuid4(),
        customer_id=CUSTOMER_ID,
        name="Dachdeckerei Muster GmbH",
        street="Hauptstraße 5",
        postal_code="48143",
        city="Münster",
        phone="0251 123456",
        google_place_id="place-customer",
        latitude=51.96,
        longitude=7.62,
    )


@pytest.fixture
def report_storage(raw_audit_full: RawAudit, report_texts: ReportTexts) -> dict[str, bytes]:
    """Content of storage keyed "<bucket>/<path>"; tests remove or replace entries."""
    return {
        f"raw/{AUDIT_ID}/raw.json": raw_audit_full.model_dump_json().encode(),
        f"raw/{AUDIT_ID}/texte.json": report_texts.model_dump_json().encode(),
    }


@pytest.fixture
def report_repositories(
    fake_repositories: MagicMock,
    report_storage: dict[str, bytes],
    report_check_results: list[CheckResult],
    report_findings: list[Finding],
    report_keywords: list[Keyword],
    report_rankings: list[Ranking],
    report_location: Location,
    sample_audit: Audit,
) -> MagicMock:
    """Repositories double serving a complete audit with rankings and storage files."""

    def download(bucket: str, remote_path: str, local_file: Path) -> Path:
        key = f"{bucket}/{remote_path}"
        if key not in report_storage:
            raise StorageError(f"download of {key} failed: not found")
        local_file.parent.mkdir(parents=True, exist_ok=True)
        local_file.write_bytes(report_storage[key])
        return local_file

    fake_repositories.download.side_effect = download
    fake_repositories.check_results.return_value = report_check_results
    fake_repositories.findings_of_audit.return_value = report_findings
    fake_repositories.keywords.return_value = report_keywords
    fake_repositories.locations.return_value = [report_location]
    fake_repositories.audits_of_customer.return_value = [sample_audit]
    fake_repositories.rankings = MagicMock(return_value=report_rankings)
    return fake_repositories


@pytest.fixture
def report_supplier() -> dict[str, object]:
    """Supplier settings with every required key."""
    return {
        "name": "RepoCity Daniel Hütte",
        "owner": "Daniel Hütte",
        "street": "Musterweg 1",
        "postal_code": "48149",
        "city": "Münster",
        "email": "kontakt@speedofthespirit.dev",
        "tax_number": "337/5000/1234",
        "iban": "DE02120300000000202051",
        "bic": "BYLADEM1001",
        "bank_name": "Musterbank",
        "small_business": False,
        "payment_terms_days": 14,
    }


@pytest.fixture
def report_quote_data() -> dict[str, object]:
    """Quote data with two positions."""
    return {
        "positions": [
            {"description": "Laufende Betreuung Oktober 2026", "quantity": 1, "unit_net": "390.00"},
            {"description": "Profil-Beiträge", "quantity": 2, "unit_net": "45.50"},
        ],
    }


@pytest.fixture
def report_document_repositories(
    fake_repositories: MagicMock, report_supplier: dict[str, object], report_location: Location
) -> MagicMock:
    """Repositories double for documents: supplier setting, saving echoes the row."""
    fake_repositories.settings_value.side_effect = lambda key: (
        report_supplier if key == "supplier" else None
    )
    fake_repositories.locations.return_value = [report_location]
    fake_repositories.save_document.side_effect = lambda document: document
    return fake_repositories


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    """An executable stand-in for the Claude Code CLI; mode via FAKE_CLAUDE_MODE."""
    script = tmp_path / "bin" / "claude"
    script.parent.mkdir()
    source = (FIXTURES_DIR / "fake_claude.py").read_text(encoding="utf-8")
    script.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    script.chmod(0o755)
    return script


@pytest.fixture
def claude_settings(settings: Settings, fake_claude: Path, tmp_path: Path) -> Settings:
    """Settings that run the fake Claude Code with a short timeout."""
    return settings.model_copy(
        update={
            "analysis_claude_command": str(fake_claude),
            "analysis_timeout_seconds": 5,
            "analysis_project_dir": tmp_path,
        }
    )


CAPTCHA_PAGE = """
<html><body><div>Our systems have detected unusual traffic from your computer network.</div>
<form id="captcha-form"><div class="g-recaptcha"></div></form></body></html>
"""
HEADLESS_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "HeadlessChrome/156.0.0.0 Safari/537.36"
)


@dataclass
class FakeDocument:
    """What the fake page shows after a navigation; url None means the requested address.

    after_submit are the states the page shows on the following reads once a question was sent
    with Enter, for example a streaming answer and then the complete one; the last one stays.
    """

    html: str
    url: str | None = None
    status: int = 200
    after_submit: list[str] = field(default_factory=list)


@dataclass
class FakeResponse:
    """Navigation response with a status code."""

    status: int


class FakeLocator:
    """Button or field of the fake page."""

    def __init__(self, page: "FakePage", name: str) -> None:
        """Remember the page and the accessible name."""
        self.page = page
        self.name = name

    @property
    def first(self) -> "FakeLocator":
        """Return the locator itself, like Playwright's first match."""
        return self

    async def click(self, timeout: int | None = None) -> None:
        """Record the click; a consent button or an advancing control shows the next document."""
        self.page.clicked.append(self.name)
        if self.page.awaits_consent or self.name in self.page.advancing_clicks:
            self.page.show(self.page.documents.pop(0), self.page.url)

    async def wait_for(self, state: str, timeout: int) -> None:
        """Accept the wait for the dialog to close once it was answered."""
        assert state == "hidden"
        assert self.page.clicked

    async def press_sequentially(self, text: str) -> None:
        """Record typed text."""
        self.page.typed.append(text)

    async def press(self, key: str) -> None:
        """Record a key; Enter sends the question and starts the prepared answer states."""
        self.page.pressed.append(key)
        if key == "Enter":
            self.page.updates = list(self.page.document.after_submit)


class FakeMouse:
    """Records wheel scrolling."""

    def __init__(self) -> None:
        """Start without scrolling."""
        self.scrolled: list[int] = []

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        """Record a vertical scroll."""
        self.scrolled.append(int(delta_y))


class FakePage:
    """Stand-in for a Playwright page that shows prepared documents in order."""

    def __init__(self, documents: list[FakeDocument], user_agent: str) -> None:
        """Queue the documents the page will show."""
        self.documents = documents
        self.user_agent = user_agent
        self.url = "about:blank"
        self.html = ""
        self.visited: list[str] = []
        self.clicked: list[str] = []
        self.typed: list[str] = []
        self.pressed: list[str] = []
        self.mouse = FakeMouse()
        self.closed = False
        self.awaits_consent = False
        self.failure: Exception | None = None
        self.document = FakeDocument("")
        self.updates: list[str] = []
        self.advancing_clicks: set[str] = set()
        self.scripts: list[str] = []
        self.awaited: list[str] = []

    def show(self, document: FakeDocument, requested: str) -> None:
        """Display a document as if it was loaded."""
        self.document = document
        self.updates = []
        self.url = document.url or requested
        self.html = document.html
        self.awaits_consent = (
            "Alle ablehnen" in document.html or "Alle akzeptieren" in document.html
        )

    async def goto(self, url: str, **_: object) -> FakeResponse:
        """Show the next queued document, or raise the prepared failure."""
        self.visited.append(url)
        if self.failure is not None:
            raise self.failure
        document = self.documents.pop(0)
        self.show(document, url)
        return FakeResponse(document.status)

    async def content(self) -> str:
        """Return the current document, moving on to the next answer state after a question."""
        if self.updates:
            self.html = self.updates.pop(0)
        return self.html

    def get_by_role(self, role: str, name: str, exact: bool = False) -> FakeLocator:
        """Return a locator for a button, field, tab or menu entry."""
        assert role in ("button", "textbox", "tab", "menuitemradio")
        return FakeLocator(self, name)

    def locator(self, selector: str) -> FakeLocator:
        """Return a locator for a CSS selector."""
        return FakeLocator(self, selector)

    async def wait_for_selector(self, selector: str, timeout: int) -> None:
        """Record what the caller waited for; the prepared document is already there."""
        self.awaited.append(selector)

    async def wait_for_load_state(self, state: str) -> None:
        """Accept the load state the browser service waits for."""
        assert state == "domcontentloaded"

    async def evaluate(self, expression: str) -> str | None:
        """Answer navigator.userAgent and record every other script."""
        if expression == "navigator.userAgent":
            return self.user_agent
        self.scripts.append(expression)
        return None

    async def close(self) -> None:
        """Record that the tab was closed."""
        self.closed = True


class FakeCdpSession:
    """Records DevTools commands."""

    def __init__(self) -> None:
        """Start without commands."""
        self.sent: list[tuple[str, dict[str, object]]] = []

    async def send(self, method: str, params: dict[str, object]) -> None:
        """Record one command."""
        self.sent.append((method, params))


class FakeContext:
    """Stand-in for the persistent browser context; every new page is the same FakePage."""

    def __init__(self, page: FakePage) -> None:
        """Serve the given page."""
        self.page = page
        self.pages_opened = 0
        self.init_scripts: list[str] = []
        self.geolocations: list[dict[str, float] | None] = []
        self.permissions: list[list[str]] = []
        self.cdp = FakeCdpSession()
        self.opened_with: list[tuple[Path, bool]] = []
        self.closed = False

    async def new_page(self) -> FakePage:
        """Return the page."""
        self.pages_opened += 1
        self.page.closed = False
        return self.page

    async def add_init_script(self, script: str) -> None:
        """Record an init script."""
        self.init_scripts.append(script)

    async def set_geolocation(self, geolocation: dict[str, float] | None) -> None:
        """Record the reported position."""
        self.geolocations.append(geolocation)

    async def grant_permissions(self, permissions: list[str]) -> None:
        """Record granted permissions."""
        self.permissions.append(permissions)

    async def new_cdp_session(self, page: FakePage) -> FakeCdpSession:
        """Return the shared DevTools session."""
        return self.cdp


@dataclass
class FakeBrowser:
    """A BrowserService on a fake context, with the waits it asked for."""

    service: BrowserService
    context: FakeContext
    page: FakePage
    waits: list[float] = field(default_factory=list)


BrowserFactory = Callable[..., FakeBrowser]


@pytest.fixture
def fake_browser(tmp_path: Path) -> BrowserFactory:
    """Build a BrowserService that serves prepared documents instead of starting Chromium."""

    def build(
        documents: list[FakeDocument | str], user_agent: str = HEADLESS_USER_AGENT
    ) -> FakeBrowser:
        queued = [
            item if isinstance(item, FakeDocument) else FakeDocument(item) for item in documents
        ]
        page = FakePage(queued, user_agent)
        context = FakeContext(page)
        waits: list[float] = []

        async def sleep(seconds: float) -> None:
            waits.append(seconds)

        @asynccontextmanager
        async def open_context(profile_dir: Path, headless: bool) -> AsyncIterator[FakeContext]:
            context.opened_with.append((profile_dir, headless))
            try:
                yield context
            finally:
                context.closed = True

        service = BrowserService(
            tmp_path / "profile",
            pacing=Pacing(sleep=sleep, rng=random.Random(7)),
            context_factory=cast(ContextFactory, open_context),
        )
        return FakeBrowser(service, context, page, waits)

    return build
