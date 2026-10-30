"""Tests for the identify step of an audit started with only the website address.

Context: Saved legal notice and start pages, a mocked legal notice request, Places Text Search
replaced by a fake and fake repositories. | Input: Fixtures. | Output: Assertions.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import HttpUrl, SecretStr

from radarauditor.collectors import identify, places
from radarauditor.collectors.places import BusinessCandidate
from radarauditor.collectors.quota import SourceGate
from radarauditor.collectors.snapshot import IdentifiedValue, WebsiteSnapshot
from radarauditor.collectors.website import parse_page
from radarauditor.db.app_settings import Source, SourcesSettings
from radarauditor.db.models import Customer, Keyword, Location
from radarauditor.errors import CollectionError, IdentificationError, StorageError
from radarauditor.industries import IndustryCatalogue, IndustryGroup, load_industries

FIXTURES = Path(__file__).parent / "fixtures" / "identify"
INDUSTRIES = Path(__file__).resolve().parents[1] / "config" / "branchen.yaml"
KEY = SecretStr("google-key")
CUSTOMER_ID = uuid4()
HOLLENBECK = "https://www.dachdecker-hollenbeck.de/"
LEGAL_NOTICE = f"{HOLLENBECK}impressum"

pytestmark = pytest.mark.usefixtures("fast_retries")


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _customer(
    name: str = "dachdecker-hollenbeck.de",
    city: str = "",
    industry: str = "",
    website: str = HOLLENBECK,
) -> Customer:
    return Customer(
        id=CUSTOMER_ID,
        name=name,
        website=HttpUrl(website),
        industry=industry,
        city=city,
        created_at=datetime(2026, 10, 24, tzinfo=UTC),
    )


def _location(**values: object) -> Location:
    row: dict[str, object] = {
        "id": uuid4(),
        "customer_id": CUSTOMER_ID,
        "name": "dachdecker-hollenbeck.de",
        "street": "",
        "postal_code": "",
        "city": "",
        "phone": "",
        "google_place_id": None,
        "latitude": None,
        "longitude": None,
    }
    row.update(values)
    return Location.model_validate(row)


def _site(
    start_html: str = "<html><body><h1>Willkommen</h1></body></html>",
    *,
    legal_notice: str | None = LEGAL_NOTICE,
    footer: dict[str, str] | None = None,
) -> WebsiteSnapshot:
    start = parse_page(HttpUrl(HOLLENBECK), 200, start_html)
    return WebsiteSnapshot(
        start_url=HttpUrl(HOLLENBECK),
        uses_https=True,
        redirects_to_single_host=True,
        robots_txt=None,
        sitemap_urls=[],
        legal_notice_url=HttpUrl(legal_notice) if legal_notice else None,
        privacy_url=None,
        footer_nap=footer or {},
        opening_hours_text=None,
        pages=[start],
        crawl_errors=[],
    )


def _candidate(**values: object) -> BusinessCandidate:
    fields: dict[str, object] = {
        "place_id": "ChIJ-hollenbeck",
        "name": "Dachdeckerei Hollenbeck GmbH",
        "formatted_address": "Hiltruper Str. 210, 48165 Münster, Deutschland",
        "phone": "0251 123456",
        "website": HOLLENBECK,
        "primary_type": "roofing_contractor",
        "category": "Dachdecker",
        "types": ("roofing_contractor", "point_of_interest", "establishment"),
        "latitude": 51.92,
        "longitude": 7.63,
    }
    fields.update(values)
    return BusinessCandidate(**fields)  # type: ignore[arg-type]


class FakeSearch:
    """Stands in for places.search_businesses and records the queries."""

    def __init__(self, *answers: list[BusinessCandidate]) -> None:
        """Answer the queries in order; without answers every search finds nothing."""
        self.answers = list(answers)
        self.queries: list[str] = []

    async def __call__(self, query: str, api_key: SecretStr) -> list[BusinessCandidate]:
        """Record the query and return the next answer."""
        self.queries.append(query)
        return self.answers.pop(0) if self.answers else []


@pytest.fixture
def industries() -> IndustryCatalogue:
    """The real industry groups."""
    return load_industries(INDUSTRIES)


@pytest.fixture
def repositories(fake_repositories: MagicMock) -> MagicMock:
    """Repositories that store rows and give back what they were handed."""
    fake_repositories.create_location.side_effect = lambda location: location
    fake_repositories.link_crm_customer.return_value = uuid4()

    def add(
        customer_id: object, phrases: list[str], city: str, *, first_primary: bool
    ) -> list[Keyword]:
        return [
            Keyword(
                id=uuid4(),
                customer_id=CUSTOMER_ID,
                phrase=phrase,
                city=city,
                monthly_volume=None,
                is_primary=first_primary and index == 0,
            )
            for index, phrase in enumerate(phrases)
        ]

    fake_repositories.add_keywords.side_effect = add
    return fake_repositories


async def _run(
    customer: Customer,
    site: WebsiteSnapshot | None,
    repositories: MagicMock,
    industries: IndustryCatalogue,
    gate: SourceGate,
    *,
    location: Location | None = None,
    keywords: list[Keyword] | None = None,
    notes: list[str] | None = None,
) -> identify.Identification:
    return await identify.identify_customer(
        customer,
        location if location is not None else _location(),
        keywords or [],
        site,
        repositories=repositories,
        industries=industries,
        gate=gate,
        api_key=KEY,
        notes=notes if notes is not None else [],
    )


def _keyword(phrase: str, city: str) -> Keyword:
    return Keyword(
        id=uuid4(),
        customer_id=CUSTOMER_ID,
        phrase=phrase,
        city=city,
        monthly_volume=None,
        is_primary=False,
    )


def _sources(result: identify.Identification) -> dict[str, tuple[str, str]]:
    return {item.field: (item.value, item.source) for item in result.identified}


def test_legal_notice_with_company_street_and_postal_line_on_own_lines() -> None:
    """The company stands above the street, the street above the postal code and city."""
    facts = identify.facts_from_legal_notice(_html("impressum_lines.html"))

    assert isinstance(facts, identify.Facts)
    assert facts.name == "Dachdeckerei Hollenbeck GmbH"
    assert isinstance(facts.address, identify.Address)
    assert (facts.address.street, facts.address.postal_code, facts.address.city) == (
        "Hiltruper Straße 210",
        "48165",
        "Münster",
    )
    # The emergency number in the header is not the number of the legal notice.
    assert facts.phone == "0251 123456"


def test_legal_notice_with_labelled_company_and_address_on_one_line() -> None:
    """A labelled company and "Weseler Str. 7, D-48151 Münster" on one line are understood."""
    facts = identify.facts_from_legal_notice(_html("impressum_one_line.html"))

    assert facts.name == "Malerbetrieb Kortmann e.K."
    assert facts.address is not None
    assert (facts.address.street, facts.address.postal_code, facts.address.city) == (
        "Weseler Str. 7",
        "48151",
        "Münster",
    )
    assert facts.phone == "+49 (0) 251 / 77 88 99"


def test_legal_notice_takes_no_label_as_company() -> None:
    """A label such as "Anschrift:" above the street is no company name."""
    facts = identify.facts_from_legal_notice(_html("impressum_label_above.html"))

    assert facts.name is None
    assert facts.address is not None
    assert (facts.address.street, facts.address.city) == ("Musterstraße 12a", "Münster")


def test_legal_notice_without_postal_address_says_nothing() -> None:
    """A post office box is no address of the business, so nothing is taken."""
    facts = identify.facts_from_legal_notice(_html("impressum_without_address.html"))

    assert facts == identify.facts_from_legal_notice("<html><body></body></html>")
    assert facts.address is None and facts.name is None


def test_json_ld_business_in_a_graph() -> None:
    """A LocalBusiness block inside @graph gives name, address and phone; WebSite is skipped."""
    page = parse_page(
        HttpUrl("https://www.elektro-brinkmann.de/"), 200, _html("start_json_ld.html")
    )

    facts = identify.facts_from_json_ld(page.json_ld)

    assert isinstance(facts, identify.Facts)
    assert facts.name == "Elektro Brinkmann GmbH"
    assert facts.address is not None
    assert (facts.address.street, facts.address.postal_code, facts.address.city) == (
        "Münsterstraße 5",
        "48291",
        "Telgte",
    )
    assert facts.phone == "+49 2504 123123"


def test_json_ld_without_business_says_nothing() -> None:
    """Pages, persons and products are not the business."""
    blocks: list[dict[str, object]] = [
        {"@type": "WebPage", "name": "Start"},
        {"@type": "Person", "name": "Jan", "address": {"addressLocality": "Münster"}},
    ]

    facts = identify.facts_from_json_ld(blocks)

    assert facts.name is None and facts.address is None


@respx.mock
async def test_identifies_from_the_legal_notice_and_confirms_on_google(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Name and address from the legal notice; Google adds place, categories and coordinates."""
    respx.get(LEGAL_NOTICE).mock(
        return_value=httpx.Response(200, text=_html("impressum_lines.html"))
    )
    search = FakeSearch([_candidate()])
    monkeypatch.setattr(places, "search_businesses", search)
    location = _location()

    result = await _run(
        _customer(), _site(), repositories, industries, open_gate, location=location
    )

    assert search.queries == ["Dachdeckerei Hollenbeck GmbH Münster"]
    assert _sources(result) == {
        "name": ("Dachdeckerei Hollenbeck GmbH", "impressum"),
        "city": ("Münster", "impressum"),
        "industry": ("bauhandwerk", "google_types"),
        "street": ("Hiltruper Straße 210", "impressum"),
        "postal_code": ("48165", "impressum"),
        "phone": ("0251 123456", "impressum"),
        "google_place_id": ("ChIJ-hollenbeck", "google_profile"),
        "keywords": (
            "Dachdecker Münster, Maler Münster, Fliesenleger Münster",
            "industry_services",
        ),
    }
    repositories.update_customer.assert_called_once_with(
        CUSTOMER_ID, name="Dachdeckerei Hollenbeck GmbH", city="Münster", industry="bauhandwerk"
    )
    repositories.link_crm_customer.assert_called_once_with(CUSTOMER_ID)
    repositories.update_location.assert_called_once_with(
        location.id,
        name="Dachdeckerei Hollenbeck GmbH",
        street="Hiltruper Straße 210",
        postal_code="48165",
        city="Münster",
        phone="0251 123456",
        google_place_id="ChIJ-hollenbeck",
        latitude=51.92,
        longitude=7.63,
    )
    repositories.add_keywords.assert_called_once_with(
        CUSTOMER_ID, ["Dachdecker", "Maler", "Fliesenleger"], "Münster", first_primary=True
    )
    assert result.customer.city == "Münster" and result.customer.industry == "bauhandwerk"
    assert result.location is not None and result.location.latitude == 51.92
    assert [(item.phrase, item.city, item.is_primary) for item in result.keywords] == [
        ("Dachdecker", "Münster", True),
        ("Maler", "Münster", False),
        ("Fliesenleger", "Münster", False),
    ]


async def test_json_ld_comes_before_the_legal_notice(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With name and address in JSON-LD the legal notice is not even fetched."""
    monkeypatch.setattr(places, "search_businesses", FakeSearch())
    site = _site(_html("start_json_ld.html"))

    with respx.mock(assert_all_called=False) as mock:
        legal = mock.get(LEGAL_NOTICE).mock(return_value=httpx.Response(500))
        result = await _run(
            _customer(name="elektro-brinkmann.de", website="https://www.elektro-brinkmann.de/"),
            site.model_copy(update={"start_url": HttpUrl("https://www.elektro-brinkmann.de/")}),
            repositories,
            industries,
            open_gate,
            location=_location(name="elektro-brinkmann.de"),
        )

    assert not legal.called
    sources = _sources(result)
    assert sources["name"] == ("Elektro Brinkmann GmbH", "json_ld")
    assert sources["city"] == ("Telgte", "json_ld")
    # Google found nothing, so the name decides the group: "Elektro" is an example of haustechnik.
    assert sources["industry"] == ("haustechnik", "name_words")
    assert sources["keywords"][0].startswith("Elektriker Telgte")


async def test_footer_fills_what_website_pages_lack(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without JSON-LD and legal notice the footer contact data of the crawl is used."""
    monkeypatch.setattr(places, "search_businesses", FakeSearch())
    footer = {
        "name": "Friseur Haarmonie",
        "street": "Ludgeristraße 1",
        "postal_code": "48143",
        "city": "Münster",
        "phone": "0251 555",
    }

    result = await _run(
        _customer(name="haarmonie-ms.de", website="https://haarmonie-ms.de/"),
        _site(legal_notice=None, footer=footer),
        repositories,
        industries,
        open_gate,
        location=_location(name="haarmonie-ms.de"),
    )

    sources = _sources(result)
    assert sources["name"] == ("Friseur Haarmonie", "footer")
    assert sources["city"] == ("Münster", "footer")
    assert sources["street"] == ("Ludgeristraße 1", "footer")
    assert sources["industry"] == ("friseur_kosmetik", "name_words")


async def test_google_profile_alone_gives_city_when_the_website_names_none(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The host is searched; a profile linking the same website is the customer."""
    search = FakeSearch([_candidate(website="http://dachdecker-hollenbeck.de")])
    monkeypatch.setattr(places, "search_businesses", search)

    result = await _run(_customer(), _site(legal_notice=None), repositories, industries, open_gate)

    assert search.queries == ["dachdecker-hollenbeck.de"]
    sources = _sources(result)
    assert sources["name"] == ("Dachdeckerei Hollenbeck GmbH", "google_profile")
    assert sources["city"] == ("Münster", "google_profile")
    assert sources["street"] == ("Hiltruper Str. 210", "google_profile")


async def test_a_google_profile_of_another_business_is_not_taken(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile with another website and another name gives nothing; the audit fails."""
    other = _candidate(name="Bedachungen Overmann", website="https://overmann.de/")
    monkeypatch.setattr(places, "search_businesses", FakeSearch([other]))
    notes: list[str] = []

    with pytest.raises(IdentificationError) as raised:
        await _run(
            _customer(), _site(legal_notice=None), repositories, industries, open_gate, notes=notes
        )

    assert str(raised.value) == identify.CITY_NOT_FOUND
    assert "no Google profile clearly belongs to dachdecker-hollenbeck.de" in notes
    repositories.update_customer.assert_not_called()


async def test_a_failed_google_search_is_noted_and_the_website_decides(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Places error (e.g. the daily cap) leaves a note; without a city the audit fails."""

    async def failing_search(query: str, api_key: SecretStr) -> list[BusinessCandidate]:
        raise CollectionError("Places API: daily limit reached")

    monkeypatch.setattr(places, "search_businesses", failing_search)
    notes: list[str] = []

    with pytest.raises(IdentificationError) as raised:
        await _run(
            _customer(), _site(legal_notice=None), repositories, industries, open_gate, notes=notes
        )

    assert str(raised.value) == identify.CITY_NOT_FOUND
    assert "Google profile not searched: Places API: daily limit reached" in notes


def test_a_shared_platform_host_alone_does_not_confirm_a_profile() -> None:
    """A website on facebook.com confirms no Google profile that also links facebook.com."""
    foreign = _candidate(name="Bedachungen Overmann", website="https://www.facebook.com/overmann")

    assert (
        identify._is_customer(foreign, "facebook.com", "Dachdeckerei Hollenbeck", "Münster")
        is False
    )
    assert identify._is_customer(foreign, "dachdecker-hollenbeck.de", None, None) is False
    own = _candidate(website="https://www.facebook.com/hollenbeck")
    assert identify._is_customer(own, "facebook.com", "Dachdeckerei Hollenbeck GmbH", "Münster")


@respx.mock
async def test_missing_city_fails_with_the_german_message(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an address on the website and with the Places API off the city stays unknown."""
    respx.get(LEGAL_NOTICE).mock(
        return_value=httpx.Response(200, text=_html("impressum_without_address.html"))
    )
    search = FakeSearch([_candidate()])
    monkeypatch.setattr(places, "search_businesses", search)
    sources = SourcesSettings.model_validate({Source.PLACES.value: {"enabled": False}})
    gate = SourceGate(sources, lambda source, cap: True)
    notes: list[str] = []

    with pytest.raises(IdentificationError, match="^Ort nicht gefunden: Weder Impressum"):
        await _run(_customer(), _site(), repositories, industries, gate, notes=notes)

    assert search.queries == []
    assert any(note.startswith("Google profile not searched:") for note in notes)


async def test_given_values_stay_and_only_keywords_are_added(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Name, city and industry entered in the app are kept; Google is asked for coordinates only."""
    search = FakeSearch([_candidate()])
    monkeypatch.setattr(places, "search_businesses", search)
    customer = _customer(name="Dachdeckerei Hollenbeck GmbH", city="Münster", industry="kfz")
    location = _location(name="Dachdeckerei Hollenbeck GmbH", city="Münster")

    result = await _run(customer, _site(), repositories, industries, open_gate, location=location)

    assert search.queries == ["Dachdeckerei Hollenbeck GmbH Münster"]
    assert result.customer == customer
    # The confirmed profile fills the empty street, postal code and phone of the location.
    assert [item.field for item in result.identified] == [
        "street",
        "postal_code",
        "phone",
        "google_place_id",
        "keywords",
    ]
    repositories.update_customer.assert_called_once_with(
        CUSTOMER_ID, name=None, city=None, industry=None
    )
    repositories.link_crm_customer.assert_not_called()
    repositories.add_keywords.assert_called_once_with(
        CUSTOMER_ID, ["Autowerkstatt", "Reifenwechsel", "Autohaus"], "Münster", first_primary=True
    )


async def test_nothing_missing_changes_nothing(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully recorded customer is neither searched nor written."""
    search = FakeSearch([_candidate()])
    monkeypatch.setattr(places, "search_businesses", search)
    customer = _customer(name="Dachdeckerei Hollenbeck", city="Münster", industry="bauhandwerk")
    keyword = Keyword(
        id=uuid4(),
        customer_id=CUSTOMER_ID,
        phrase="Dachdecker",
        city="Münster",
        monthly_volume=None,
        is_primary=True,
    )

    location = _location(name="Dachdeckerei Hollenbeck", city="Münster")

    result = await _run(
        customer, None, repositories, industries, open_gate, location=location, keywords=[keyword]
    )

    assert result == identify.Identification(customer, location, [keyword])
    assert search.queries == []
    repositories.update_customer.assert_not_called()
    repositories.update_location.assert_not_called()
    repositories.add_keywords.assert_not_called()


async def test_unknown_industry_leaves_it_empty_and_creates_no_keywords(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile category outside every group and a neutral name give no industry; it goes on."""
    neutral = _candidate(
        name="Lichtblick Fotografie",
        website="https://lichtblick-fotografie.de/",
        primary_type="art_studio",
        category="Fotostudio",
        types=("art_studio",),
    )
    monkeypatch.setattr(places, "search_businesses", FakeSearch([neutral]))
    notes: list[str] = []

    result = await _run(
        _customer(name="lichtblick-fotografie.de", website="https://lichtblick-fotografie.de/"),
        _site(legal_notice=None),
        repositories,
        industries,
        open_gate,
        location=_location(name="lichtblick-fotografie.de"),
        notes=notes,
    )

    assert result.customer.industry == ""
    assert result.keywords == []
    assert "industry" not in _sources(result)
    assert "industry not recognised, checks that need it are not measurable" in notes
    assert "no keywords: the industry is not known" in notes
    repositories.add_keywords.assert_not_called()


async def test_keywords_stored_without_city_get_the_identified_city(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keywords given at the start without a city receive it; no service keywords are added."""
    monkeypatch.setattr(places, "search_businesses", FakeSearch([_candidate()]))
    flat, gutter = _keyword("Flachdach", ""), _keyword("Dachrinne", "")

    result = await _run(
        _customer(),
        _site(legal_notice=None),
        repositories,
        industries,
        open_gate,
        keywords=[flat, gutter],
    )

    repositories.update_keywords_city.assert_called_once_with([flat.id, gutter.id], "Münster")
    repositories.add_keywords.assert_not_called()
    assert [(item.phrase, item.city) for item in result.keywords] == [
        ("Flachdach", "Münster"),
        ("Dachrinne", "Münster"),
    ]


async def test_a_keyword_already_tracked_in_the_city_keeps_no_city(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same phrase cannot be tracked twice in one city, so the copy without city is left."""
    monkeypatch.setattr(places, "search_businesses", FakeSearch())
    customer = _customer(name="Dachdeckerei Hollenbeck", city="Münster", industry="bauhandwerk")
    without_city, tracked = _keyword("Flachdach", ""), _keyword("Flachdach", "Münster")
    notes: list[str] = []

    result = await _run(
        customer,
        None,
        repositories,
        industries,
        open_gate,
        location=_location(name="Dachdeckerei Hollenbeck", city="Münster"),
        keywords=[without_city, tracked],
        notes=notes,
    )

    repositories.update_keywords_city.assert_not_called()
    assert result.keywords == [tracked]
    assert "keyword Flachdach keeps no city: it is already tracked in Münster" in notes


async def test_keyword_cities_that_cannot_be_stored_are_noted(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed write leaves the keywords without city out of the audit and says why."""
    monkeypatch.setattr(places, "search_businesses", FakeSearch([_candidate()]))
    repositories.update_keywords_city.side_effect = StorageError("update rejected")
    notes: list[str] = []

    result = await _run(
        _customer(),
        _site(legal_notice=None),
        repositories,
        industries,
        open_gate,
        keywords=[_keyword("Flachdach", ""), _keyword("Dachrinne", "")],
        notes=notes,
    )

    assert result.keywords == []
    assert "keywords Flachdach, Dachrinne keep no city: update rejected" in notes


@respx.mock
async def test_an_unreadable_legal_notice_is_noted_and_the_footer_used(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legal notice that cannot be fetched adds a note; the other sources still count."""
    respx.get(LEGAL_NOTICE).mock(return_value=httpx.Response(404))
    monkeypatch.setattr(places, "search_businesses", FakeSearch())
    footer = {"name": "Dachdeckerei Hollenbeck GmbH", "city": "Münster"}
    notes: list[str] = []

    result = await _run(
        _customer(), _site(footer=footer), repositories, industries, open_gate, notes=notes
    )

    assert any(note.startswith("legal notice not readable:") for note in notes)
    assert _sources(result)["city"] == ("Münster", "footer")


async def test_a_failed_crm_link_is_noted_and_the_audit_goes_on(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The identified values stay stored when the CRM cannot be linked."""
    monkeypatch.setattr(places, "search_businesses", FakeSearch([_candidate()]))
    repositories.link_crm_customer.side_effect = StorageError("crm unreachable")
    notes: list[str] = []

    result = await _run(
        _customer(), _site(legal_notice=None), repositories, industries, open_gate, notes=notes
    )

    assert "customer not linked to the CRM: crm unreachable" in notes
    assert result.customer.city == "Münster"
    repositories.update_customer.assert_called_once()


async def test_a_customer_without_location_gets_one(
    repositories: MagicMock,
    industries: IndustryCatalogue,
    open_gate: SourceGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The identified name and address become the first location."""
    monkeypatch.setattr(places, "search_businesses", FakeSearch([_candidate()]))

    result = await identify.identify_customer(
        _customer(),
        None,
        [],
        _site(legal_notice=None),
        repositories=repositories,
        industries=industries,
        gate=open_gate,
        api_key=KEY,
        notes=[],
    )

    created = repositories.create_location.call_args.args[0]
    assert (created.name, created.street, created.postal_code, created.city) == (
        "Dachdeckerei Hollenbeck GmbH",
        "Hiltruper Str. 210",
        "48165",
        "Münster",
    )
    assert result.location == created


def test_keyword_suggestions_follow_the_app() -> None:
    """Same rule as keywordSuggestions in app/src/lib/fieldAudit.ts."""
    groups = load_industries(INDUSTRIES)
    group = groups.resolve("haustechnik")

    assert identify.keyword_suggestions(group, " Münster ") == [
        "Elektriker Münster",
        "Heizung Notdienst Münster",
        "Sanitär Notdienst Münster",
    ]
    assert identify.keyword_suggestions(group, "  ") == []
    assert identify.keyword_suggestions(None, "Münster") == []


def test_keyword_suggestions_name_each_stored_service_once() -> None:
    """The noted suggestions are the stored phrases plus the city, trimmed and without repeats."""
    group = IndustryGroup(
        id="bauhandwerk",
        label="Bauhandwerk",
        examples=[],
        services=[" Dachdecker", "Dachdecker", "Maler "],
        question_term="Handwerker",
    )

    assert identify.keyword_suggestions(group, "Münster") == ["Dachdecker Münster", "Maler Münster"]


def test_identified_values_round_trip_in_the_raw_audit() -> None:
    """The app reads field, value and source from the stored raw data."""
    value = IdentifiedValue(field="city", value="Münster", source="impressum")

    assert IdentifiedValue.model_validate_json(value.model_dump_json()) == value
