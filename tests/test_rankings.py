"""Tests for deriving ranking rows from collected searches.

Context: Pure function tests on the roofer fixture. | Input: Fixtures. | Output: Assertions.
"""

from uuid import UUID, uuid5

from radarauditor.collectors.snapshot import GridPoint, RawAudit, SerpResult
from radarauditor.db.models import Competitor, Keyword
from radarauditor.scoring.rankings import CUSTOMER_SUBJECT, grid_rankings, rankings_from_raw

AUDIT_ID = UUID("00000000-0000-4000-8000-000000000002")
CUSTOMER_ID = UUID("00000000-0000-4000-8000-000000000001")
KEYWORD_ROOFER = UUID("00000000-0000-4000-8000-0000000000b1")
KEYWORD_RENOVATION = UUID("00000000-0000-4000-8000-0000000000b2")
KEYWORD_UNSEARCHED = UUID("00000000-0000-4000-8000-0000000000b3")


def _keyword(keyword_id: UUID, phrase: str, city: str = "Münster") -> Keyword:
    return Keyword(
        id=keyword_id,
        customer_id=CUSTOMER_ID,
        phrase=phrase,
        city=city,
        monthly_volume=None,
        is_primary=False,
    )


def _competitor(name: str, website: str | None) -> Competitor:
    return Competitor.model_validate(
        {
            "id": uuid5(CUSTOMER_ID, name),
            "customer_id": CUSTOMER_ID,
            "name": name,
            "google_place_id": None,
            "website": website,
        }
    )


KEYWORDS = [
    _keyword(KEYWORD_ROOFER, "Dachdecker"),
    _keyword(KEYWORD_RENOVATION, "Dachsanierung"),
    _keyword(KEYWORD_UNSEARCHED, "Dachfenster"),
]
COMPETITORS = [
    _competitor("Dach Schmidt", "https://www.gelbeseiten.de/"),
    _competitor("Bedachungen Meier", None),
]


def test_rows_for_customer_and_competitors_per_searched_keyword(raw_audit_full: RawAudit) -> None:
    rows = rankings_from_raw(
        AUDIT_ID,
        raw_audit_full,
        KEYWORDS,
        COMPETITORS,
        "www.dachdeckerei-muster.de",
        "Dachdeckerei Muster GmbH",
    )

    summary = [
        (row.keyword_id, row.subject, row.organic_position, row.local_pack_position) for row in rows
    ]
    assert summary == [
        (KEYWORD_ROOFER, CUSTOMER_SUBJECT, 2, 1),
        (KEYWORD_ROOFER, "Dach Schmidt", 1, 2),
        (KEYWORD_ROOFER, "Bedachungen Meier", None, None),
        (KEYWORD_RENOVATION, CUSTOMER_SUBJECT, 5, 2),
        (KEYWORD_RENOVATION, "Dach Schmidt", None, None),
        (KEYWORD_RENOVATION, "Bedachungen Meier", None, None),
    ]
    assert all(row.audit_id == AUDIT_ID and row.grid_point is None for row in rows)
    assert rows[0].measured_at == raw_audit_full.serps[0].measured_at


def test_row_ids_are_stable_across_reruns(raw_audit_full: RawAudit) -> None:
    first = rankings_from_raw(AUDIT_ID, raw_audit_full, KEYWORDS, COMPETITORS, None, None)
    second = rankings_from_raw(AUDIT_ID, raw_audit_full, KEYWORDS, COMPETITORS, None, None)

    assert [row.id for row in first] == [row.id for row in second]
    assert first[0].id == uuid5(AUDIT_ID, f"{KEYWORD_ROOFER}/{CUSTOMER_SUBJECT}")
    assert len({row.id for row in first}) == len(first)


def test_unknown_host_and_place_give_no_positions(raw_audit_full: RawAudit) -> None:
    rows = rankings_from_raw(AUDIT_ID, raw_audit_full, KEYWORDS[:1], [], None, None)

    assert [(row.organic_position, row.local_pack_position) for row in rows] == [(None, None)]


def test_bare_phrase_matches_and_bing_and_images_are_ignored(raw_audit_full: RawAudit) -> None:
    serps: list[SerpResult] = [
        serp.model_copy(
            update={
                "phrase": "  dachdecker ",
                "organic": serp.organic
                if serp.engine == "google" and serp.vertical == "web"
                else [(1, "dachdeckerei-muster.de")],
            }
        )
        for serp in raw_audit_full.serps
        if serp.phrase == "Dachdecker Münster"
    ]
    assert len(serps) == 3
    raw = raw_audit_full.model_copy(update={"serps": serps})

    rows = rankings_from_raw(
        AUDIT_ID, raw, KEYWORDS[:1], [], "dachdeckerei-muster.de", "Dachdeckerei Muster"
    )

    assert [(row.organic_position, row.local_pack_position) for row in rows] == [(2, 1)]


def test_keywords_without_search_give_no_rows(raw_audit_minimal: RawAudit) -> None:
    assert rankings_from_raw(AUDIT_ID, raw_audit_minimal, KEYWORDS, COMPETITORS, None, None) == []


GRID = [
    GridPoint(key="r1c1", latitude=51.97, longitude=7.62, position=1),
    GridPoint(key="r1c2", latitude=51.97, longitude=7.63, position=None),
]


def test_grid_points_become_customer_rows_of_the_primary_keyword(
    raw_audit_full: RawAudit,
) -> None:
    raw = raw_audit_full.model_copy(update={"grid": GRID})
    keywords = [KEYWORDS[0], KEYWORDS[1].model_copy(update={"is_primary": True})]

    rows = rankings_from_raw(AUDIT_ID, raw, keywords, [], None, None)

    gridded = [row for row in rows if row.grid_point is not None]
    assert [(row.grid_point, row.local_pack_position) for row in gridded] == [
        ("r1c1", 1),
        ("r1c2", None),
    ]
    assert all(
        row.keyword_id == KEYWORD_RENOVATION
        and row.subject == CUSTOMER_SUBJECT
        and row.organic_position is None
        and row.measured_at == raw.collected_at
        for row in gridded
    )
    assert len({row.id for row in rows}) == len(rows)


def test_grid_rows_fall_back_to_first_keyword_and_need_one(raw_audit_full: RawAudit) -> None:
    raw = raw_audit_full.model_copy(update={"grid": GRID})

    assert {row.keyword_id for row in grid_rankings(AUDIT_ID, raw, KEYWORDS)} == {KEYWORD_ROOFER}
    assert grid_rankings(AUDIT_ID, raw, []) == []
    assert grid_rankings(AUDIT_ID, raw_audit_full, KEYWORDS) == []
