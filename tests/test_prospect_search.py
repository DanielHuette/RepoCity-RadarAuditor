"""Tests for running one customer search in the worker.

Context: Repositories and the Places page reader are doubles; nothing reaches Google or Supabase.
| Input: A claimed search. | Output: Assertions on what is stored.
"""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, call
from uuid import UUID

import pytest
from conftest import FIXED_TIME
from pydantic import SecretStr

from radarauditor.collectors.places import ProspectPage, ProspectPlace
from radarauditor.config import Settings
from radarauditor.db.models import ProspectSearch, ProspectSearchStatus
from radarauditor.errors import CollectionError, StorageError
from radarauditor.prospects import search

SEARCH_ID = UUID("00000000-0000-4000-8000-0000000000f1")
CRITERIA_PATH = Path(__file__).resolve().parents[1] / "config" / "kundensuche.yaml"


def _search() -> ProspectSearch:
    return ProspectSearch(
        id=SEARCH_ID,
        trade="Dachdecker",
        city="Münster",
        status=ProspectSearchStatus.RUNNING,
        requested_at=FIXED_TIME,
        claimed_at=FIXED_TIME,
    )


def _place(name: str, **changes: object) -> ProspectPlace:
    values: dict[str, object] = {
        "place_id": f"ChIJ-{name}",
        "name": name,
        "formatted_address": "Hiltruper Str. 210, 48165 Münster, Deutschland",
        "phone": "0251 1",
        "website": "https://example.de/",
        "maps_url": None,
        "rating": 4.0,
        "review_count": 10,
        "photo_count": 10,
        "has_opening_hours": True,
        "business_status": "OPERATIONAL",
        **changes,
    }
    return ProspectPlace(**values)  # type: ignore[arg-type]


class Pages:
    """Answers the page reader with prepared pages and records each call."""

    def __init__(self, *pages: ProspectPage | Exception) -> None:
        """Keep the pages to answer with, in order."""
        self._pages = list(pages)
        self.calls: list[tuple[str, str | None]] = []

    async def __call__(
        self, phrase: str, api_key: SecretStr, page_token: str | None = None
    ) -> ProspectPage:
        """Answer the next prepared page or raise the prepared error."""
        self.calls.append((phrase, page_token))
        page = self._pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


@pytest.fixture
def search_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"prospect_criteria_path": CRITERIA_PATH})


@pytest.fixture
def repositories(fake_repositories: MagicMock) -> MagicMock:
    fake_repositories.claim_prospect_search.return_value = _search()
    fake_repositories.finish_prospect_search.return_value = 0
    fake_repositories.month_source_cost.return_value = Decimal(0)
    fake_repositories.add_source_cost.return_value = Decimal(0)
    return fake_repositories


async def test_nothing_waits_so_nothing_is_asked(
    repositories: MagicMock, search_settings: Settings
) -> None:
    repositories.claim_prospect_search.return_value = None
    pages = Pages()

    assert await search.run_next_search(repositories, search_settings, pages) is False
    assert pages.calls == []


async def test_every_page_is_read_ranked_and_stored(
    repositories: MagicMock, search_settings: Settings
) -> None:
    pages = Pages(
        ProspectPage([_place("Gepflegt")], "seite-2"),
        ProspectPage([_place("Verwaist", website=None)], None),
    )

    assert await search.run_next_search(repositories, search_settings, pages) is True

    assert pages.calls == [("Dachdecker in Münster", None), ("Dachdecker in Münster", "seite-2")]
    assert [call.args[0] for call in repositories.consume_source_quota.call_args_list] == [
        "prospect_search",
        "prospect_search",
    ]
    search_id, results, note = repositories.finish_prospect_search.call_args.args
    assert search_id == SEARCH_ID
    assert note is None
    assert [(item["rank"], item["name"]) for item in results] == [(1, "Verwaist"), (2, "Gepflegt")]
    assert results[0]["signals"] == ["no_website"]
    assert results[0]["reasons"] == "Keine Webseite im Profil."
    repositories.fail_prospect_search.assert_not_called()


async def test_no_more_pages_than_the_criteria_allow(
    repositories: MagicMock, search_settings: Settings
) -> None:
    endless = [ProspectPage([_place(f"B{index}")], f"seite-{index + 2}") for index in range(5)]
    pages = Pages(*endless)

    await search.run_next_search(repositories, search_settings, pages)

    assert len(pages.calls) == 3


async def test_every_page_books_the_price_of_a_text_search_enterprise(
    repositories: MagicMock, search_settings: Settings
) -> None:
    repositories.month_source_cost.return_value = Decimal(0)
    repositories.add_source_cost.return_value = Decimal("0.07")
    pages = Pages(ProspectPage([_place("A")], "seite-2"), ProspectPage([_place("B")], None))
    rated = search_settings.model_copy(update={"google_eur_per_usd": Decimal("0.9")})

    await search.run_next_search(repositories, rated, pages)

    assert repositories.add_source_cost.call_args_list == [
        call("prospect_search", Decimal("0.0315")),
        call("prospect_search", Decimal("0.0315")),
    ]


async def test_the_monthly_euro_cap_stops_the_search_before_a_request(
    repositories: MagicMock, search_settings: Settings
) -> None:
    repositories.month_source_cost.return_value = Decimal(10)
    pages = Pages()

    await search.run_next_search(repositories, search_settings, pages)

    assert pages.calls == []
    repositories.fail_prospect_search.assert_called_once_with(
        SEARCH_ID, "Das Monatslimit der Kundensuche in Euro ist erreicht."
    )


async def test_a_switched_off_source_fails_the_search_without_a_request(
    repositories: MagicMock, search_settings: Settings
) -> None:
    repositories.settings_values.return_value = {"sources": {"prospect_search": {"enabled": False}}}
    pages = Pages()

    await search.run_next_search(repositories, search_settings, pages)

    assert pages.calls == []
    repositories.fail_prospect_search.assert_called_once_with(
        SEARCH_ID, "Die Kundensuche ist in den Einstellungen abgeschaltet."
    )


async def test_a_cap_after_the_first_page_keeps_the_first_page_with_a_note(
    repositories: MagicMock, search_settings: Settings
) -> None:
    repositories.consume_source_quota.side_effect = [True, False]
    pages = Pages(ProspectPage([_place("Erster")], "seite-2"))

    await search.run_next_search(repositories, search_settings, pages)

    _, results, note = repositories.finish_prospect_search.call_args.args
    assert [item["name"] for item in results] == ["Erster"]
    assert note == (
        "Nur 1 von bis zu 3 Seiten gelesen: Das Tageslimit der Kundensuche ist erreicht."
    )


async def test_a_cap_before_the_first_page_fails_the_search(
    repositories: MagicMock, search_settings: Settings
) -> None:
    repositories.consume_source_quota.return_value = False

    await search.run_next_search(repositories, search_settings, Pages())

    repositories.fail_prospect_search.assert_called_once_with(
        SEARCH_ID, "Das Tageslimit der Kundensuche ist erreicht."
    )


async def test_a_google_error_fails_the_search_with_a_german_sentence(
    repositories: MagicMock, search_settings: Settings
) -> None:
    pages = Pages(CollectionError("Places prospects answered 403"))

    await search.run_next_search(repositories, search_settings, pages)

    repositories.fail_prospect_search.assert_called_once_with(
        SEARCH_ID, "Die Google-Schnittstelle hat die Suche abgelehnt oder nicht beantwortet."
    )
    repositories.finish_prospect_search.assert_not_called()


async def test_unreadable_criteria_fail_the_search(
    repositories: MagicMock, search_settings: Settings, tmp_path: Path
) -> None:
    broken = search_settings.model_copy(update={"prospect_criteria_path": tmp_path / "x.yaml"})

    await search.run_next_search(repositories, broken, Pages())

    repositories.fail_prospect_search.assert_called_once_with(
        SEARCH_ID, "Die Kriterien der Kundensuche sind auf dem Server nicht lesbar."
    )


async def test_unreadable_source_settings_fail_the_search(
    repositories: MagicMock, search_settings: Settings
) -> None:
    repositories.settings_values.return_value = {"sources": {"places": {"daily_cap": -1}}}

    await search.run_next_search(repositories, search_settings, Pages())

    repositories.fail_prospect_search.assert_called_once_with(
        SEARCH_ID, "Die Einstellungen der Datenquellen sind nicht lesbar."
    )


async def test_a_claim_that_fails_is_logged_not_raised(
    repositories: MagicMock, search_settings: Settings
) -> None:
    repositories.claim_prospect_search.side_effect = StorageError("down")

    assert await search.run_next_search(repositories, search_settings, Pages()) is False
