"""Tests for the DataForSEO collector.

Context: Unit tests with mocked external calls. | Input: Fixtures. | Output: Assertions.
"""

import json
import math
from decimal import Decimal

import httpx
import pytest
import respx
from pydantic import SecretStr

from radarauditor.collectors import paid_rankings
from radarauditor.collectors.quota import SourceGate
from radarauditor.config import Settings
from radarauditor.db.app_settings import Source, SourcesSettings
from radarauditor.errors import (
    CollectionError,
    DailyCapReachedError,
    MonthlyCostCapReachedError,
    SourceDisabledError,
)

pytestmark = pytest.mark.usefixtures("fast_retries")

ORGANIC_URL = f"{paid_rankings.API_BASE_URL}{paid_rankings.ORGANIC_PATH}"
VOLUME_URL = f"{paid_rankings.API_BASE_URL}{paid_rankings.VOLUME_PATH}"


@pytest.fixture
def client(collector_settings: Settings, open_gate: SourceGate) -> paid_rankings.DataForSeoClient:
    """Client built from settings with DataForSEO credentials."""
    return paid_rankings.DataForSeoClient(_paid_settings(collector_settings), open_gate)


def _paid_settings(collector_settings: Settings) -> Settings:
    return collector_settings.model_copy(
        update={
            "dataforseo_login": "login@example.de",
            "dataforseo_password": SecretStr("secret"),
        }
    )


def _envelope(
    result: list[dict[str, object]] | None, task_status: int = 20000, cost: float = 0.0
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status_code": 20000,
            "status_message": "Ok.",
            "cost": cost,
            "tasks": [{"status_code": task_status, "status_message": "x", "result": result}],
        },
    )


class Ledger:
    """Monthly spend in memory, like the database functions of migration 009."""

    def __init__(self, spent: Decimal = Decimal(0)) -> None:
        """Start with what was spent this month already."""
        self.spent = spent
        self.added: list[tuple[str, Decimal]] = []

    def month_source_cost(self, source: str) -> Decimal:
        """Return the month's spend."""
        return self.spent

    def add_source_cost(self, source: str, cost: Decimal) -> Decimal:
        """Add one request's cost and return the month's spend."""
        self.added.append((source, cost))
        self.spent += cost
        return self.spent


ORGANIC_RESULT = [
    {
        "items": [
            {"type": "local_pack", "rank_group": 1, "title": "Dachdeckerei Hollenbeck GmbH"},
            {"type": "local_pack", "rank_group": 2, "title": "Bedachungen Overmann"},
            {"type": "organic", "rank_group": 2, "domain": "www.gelbeseiten.de"},
            {"type": "organic", "rank_group": 1, "domain": "www.dachdecker-hollenbeck.de"},
            {"type": "organic", "rank_group": 3, "domain": "dachdecker-hollenbeck.de"},
            {"type": "organic", "rank_group": 4, "domain": "dach-kemper.de"},
            {
                "type": "ai_overview",
                "rank_group": 1,
                "items": [
                    {
                        "type": "ai_overview_element",
                        "text": "Empfohlen wird oft Dach Service Kemper in Münster.",
                        "references": [{"source": "Gelbe Seiten", "domain": "gelbeseiten.de"}],
                    }
                ],
            },
            {
                "type": "people_also_ask",
                "rank_group": 1,
                "items": [
                    {"type": "people_also_ask_element", "title": "Was kostet ein Dachdecker?"},
                    {"type": "people_also_ask_element", "title": "Was kostet ein Dachdecker?"},
                ],
            },
        ]
    }
]


def test_client_requires_credentials(collector_settings: Settings, open_gate: SourceGate) -> None:
    """The app switch is enough to turn DataForSEO on; the credentials come from the env."""
    with pytest.raises(CollectionError, match="credentials"):
        paid_rankings.DataForSeoClient(collector_settings, open_gate)
    assert paid_rankings.is_configured(collector_settings) is False
    assert paid_rankings.is_configured(_paid_settings(collector_settings)) is True


def test_client_requires_the_app_switch(collector_settings: Settings) -> None:
    """The dataforseo source switched off in the app settings blocks the client."""
    sources = SourcesSettings.model_validate({"dataforseo": {"enabled": False}})

    with pytest.raises(SourceDisabledError, match="dataforseo"):
        paid_rankings.DataForSeoClient(
            _paid_settings(collector_settings), SourceGate(sources, lambda s, c: True)
        )


async def test_post_stops_at_the_daily_cap(collector_settings: Settings) -> None:
    """No task is sent once the daily cap is used up."""
    gate = SourceGate(SourcesSettings(), lambda source, cap: False)
    capped = paid_rankings.DataForSeoClient(_paid_settings(collector_settings), gate)

    with pytest.raises(DailyCapReachedError, match="daily cap of 1000"):
        await capped.post(paid_rankings.ORGANIC_PATH, {"keyword": "Dachdecker"})
    assert gate.exhausted == {Source.DATAFORSEO}


@respx.mock
async def test_search_each_maps_items(client: paid_rankings.DataForSeoClient) -> None:
    """Organic results, local pack, AI overview and questions; the route is DataForSEO."""
    route = respx.post(ORGANIC_URL).mock(return_value=_envelope(ORGANIC_RESULT))

    batch = await paid_rankings.search_each(client, ["Dachdecker Münster"], 51.9607, 7.6261)

    assert batch.failed == []
    assert batch.reason is None
    result = batch.results[0]
    assert result.phrase == "Dachdecker Münster"
    assert result.route == "dataforseo"
    assert result.organic == [
        (1, "dachdecker-hollenbeck.de"),
        (2, "gelbeseiten.de"),
        (3, "dach-kemper.de"),
    ]
    assert result.local_pack == [(1, "Dachdeckerei Hollenbeck GmbH"), (2, "Bedachungen Overmann")]
    assert result.ai_overview_present is True
    assert result.ai_overview_mentions == ["Gelbe Seiten", "Dach Service Kemper"]
    assert result.people_also_ask == ["Was kostet ein Dachdecker?"]
    assert result.ads == []
    assert result.knowledge_panel is False
    request = route.calls[0].request
    body = json.loads(request.content)
    assert body == [
        {
            "keyword": "Dachdecker Münster",
            "se_domain": "google.de",
            "location_coordinate": "51.9607000,7.6261000,200",
            "language_code": "de",
            "depth": 20,
        }
    ]
    assert request.headers["Authorization"].startswith("Basic ")


@respx.mock
async def test_search_each_reads_ads_and_knowledge_panel(
    client: paid_rankings.DataForSeoClient,
) -> None:
    """Ads and the knowledge panel of a brand search feed j4 and j10 like the browser parser."""
    items = [
        {"type": "paid", "rank_group": 1, "domain": "www.dach-kemper.de"},
        {"type": "paid", "rank_group": 2, "title": "Bedachungen Overmann"},
        {"type": "knowledge_graph", "rank_group": 1, "title": "Dachdeckerei Hollenbeck"},
        {"type": "organic", "rank_group": 1, "domain": "dachdecker-hollenbeck.de"},
    ]
    respx.post(ORGANIC_URL).mock(return_value=_envelope([{"items": items}]))

    batch = await paid_rankings.search_each(client, ["Dachdeckerei Hollenbeck"], 51.9, 7.6)

    assert batch.results[0].ads == ["dach-kemper.de", "Bedachungen Overmann"]
    assert batch.results[0].knowledge_panel is True


@respx.mock
async def test_search_each_without_coordinates_searches_germany(
    client: paid_rankings.DataForSeoClient,
) -> None:
    """Without coordinates the search runs for Germany; the phrase carries the city."""
    route = respx.post(ORGANIC_URL).mock(return_value=_envelope(None))

    batch = await paid_rankings.search_each(client, ["Dachdecker Münster"], None, None)

    body = json.loads(route.calls[0].request.content)[0]
    assert body["location_code"] == paid_rankings.GERMANY_LOCATION_CODE
    assert "location_coordinate" not in body
    assert batch.results[0].organic == []
    assert batch.results[0].ai_overview_present is False


@respx.mock
async def test_rejected_task_is_handed_back_without_retry(
    client: paid_rankings.DataForSeoClient,
) -> None:
    """A rejected task fails only its phrase, permanently."""
    route = respx.post(ORGANIC_URL).mock(return_value=_envelope(None, task_status=40501))

    batch = await paid_rankings.search_each(client, ["x"], 51.0, 7.0)

    assert batch.results == []
    assert batch.failed == ["x"]
    assert batch.reason is not None and "40501" in batch.reason
    assert route.call_count == 1


@respx.mock
async def test_server_side_task_error_is_retried(client: paid_rankings.DataForSeoClient) -> None:
    """Server side task error is retried."""
    route = respx.post(ORGANIC_URL).mock(
        side_effect=[_envelope(None, task_status=50000), _envelope(ORGANIC_RESULT)]
    )

    batch = await paid_rankings.search_each(client, ["x"], 51.0, 7.0)

    assert batch.results[0].organic[0] == (1, "dachdecker-hollenbeck.de")
    assert route.call_count == 2


@respx.mock
async def test_unauthorised_request_fails_the_phrase(
    client: paid_rankings.DataForSeoClient,
) -> None:
    """Unauthorised request is permanent and named in the reason."""
    respx.post(ORGANIC_URL).mock(return_value=httpx.Response(401))

    batch = await paid_rankings.search_each(client, ["x", "y"], 51.0, 7.0)

    assert batch.failed == ["x", "y"]
    assert batch.reason is not None and "401" in batch.reason


@respx.mock
async def test_post_records_the_reported_cost_in_euros(collector_settings: Settings) -> None:
    """The dollar cost DataForSEO reports is converted and added to the month's spend."""
    ledger = Ledger()
    gate = SourceGate(SourcesSettings(), lambda source, cap: True, ledger)
    settings = _paid_settings(collector_settings).model_copy(
        update={"dataforseo_eur_per_usd": Decimal("0.9")}
    )
    client = paid_rankings.DataForSeoClient(settings, gate)
    respx.post(ORGANIC_URL).mock(return_value=_envelope(ORGANIC_RESULT, cost=0.002))

    await client.post(paid_rankings.ORGANIC_PATH, {"keyword": "Dachdecker"})

    assert ledger.added == [("dataforseo", Decimal("0.0018"))]
    assert gate.cost_capped == frozenset()


@respx.mock
async def test_monthly_cost_cap_stops_further_requests(collector_settings: Settings) -> None:
    """Once the month's spend reaches the cap, no task is sent and the phrases are handed back."""
    sources = SourcesSettings.model_validate({"dataforseo": {"monthly_cost_cap_eur": "0.003"}})
    ledger = Ledger(spent=Decimal("0.002"))
    gate = SourceGate(sources, lambda source, cap: True, ledger)
    client = paid_rankings.DataForSeoClient(_paid_settings(collector_settings), gate)
    route = respx.post(ORGANIC_URL).mock(return_value=_envelope(ORGANIC_RESULT, cost=0.002))

    first = await paid_rankings.search_each(client, ["Dachdecker Münster"], 51.9, 7.6)
    second = await paid_rankings.search_each(client, ["Flachdach Münster"], 51.9, 7.6)

    assert len(first.results) == 1
    assert second.failed == ["Flachdach Münster"]
    assert second.reason == "dataforseo: monthly cost cap of 0.003 EUR reached"
    assert route.call_count == 1
    assert gate.cost_capped == {Source.DATAFORSEO}
    with pytest.raises(MonthlyCostCapReachedError):
        await client.post(paid_rankings.ORGANIC_PATH, {"keyword": "x"})


def test_grid_points_are_one_kilometre_apart() -> None:
    """`grid_points` are one kilometre apart."""
    points = paid_rankings.grid_points(51.96, 7.63, 3)

    assert len(points) == 9
    assert points[4] == pytest.approx((51.96, 7.63))
    south_west, east_neighbour, north_neighbour = points[0], points[1], points[3]
    latitude_km = (north_neighbour[0] - south_west[0]) * paid_rankings.KM_PER_DEGREE_LATITUDE
    longitude_km = (
        (east_neighbour[1] - south_west[1])
        * paid_rankings.KM_PER_DEGREE_LATITUDE
        * math.cos(math.radians(51.96))
    )
    assert latitude_km == pytest.approx(1.0)
    assert longitude_km == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("index", "key"), [(0, "r3c1"), (2, "r3c3"), (4, "r2c2"), (6, "r1c1"), (8, "r1c3")]
)
def test_grid_key_counts_rows_from_the_north(index: int, key: str) -> None:
    """Index 0 of grid_points is the south-west corner, so it lies in the last row."""
    assert paid_rankings.grid_key(index, 3) == key


def test_grid_key_rejects_index_outside_grid() -> None:
    """An index beyond the grid is a programming error."""
    with pytest.raises(ValueError, match="outside"):
        paid_rankings.grid_key(9, 3)


def test_grid_points_rejects_empty_grid() -> None:
    """`grid_points` rejects empty grid."""
    with pytest.raises(ValueError, match="grid_size"):
        paid_rankings.grid_points(51.96, 7.63, 0)


@respx.mock
async def test_monthly_volumes_maps_back_to_input_phrases(
    client: paid_rankings.DataForSeoClient,
) -> None:
    """`monthly_volumes` maps back to input phrases."""
    route = respx.post(VOLUME_URL).mock(
        return_value=_envelope(
            [
                {"keyword": "dachdecker münster", "search_volume": 880},
                {"keyword": "flachdach münster", "search_volume": None},
            ]
        )
    )

    volumes = await paid_rankings.monthly_volumes(
        client, ["Dachdecker Münster", "Flachdach Münster"]
    )

    assert volumes == {"Dachdecker Münster": 880}
    body = json.loads(route.calls[0].request.content)[0]
    assert body["location_code"] == 2276
    assert body["language_code"] == "de"


@respx.mock
async def test_monthly_volumes_rejects_malformed_rows(
    client: paid_rankings.DataForSeoClient,
) -> None:
    """`monthly_volumes` rejects malformed rows."""
    respx.post(VOLUME_URL).mock(return_value=_envelope([{"search_volume": 1}]))

    with pytest.raises(CollectionError, match="malformed"):
        await paid_rankings.monthly_volumes(client, ["x"])
