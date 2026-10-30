"""Tests for the checks of area h (verzeichnisse).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

import pytest

from radarauditor.collectors.snapshot import DirectoryHit, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import address_key, phone_key
from radarauditor.scoring.checks.h_verzeichnisse import run

THRESHOLDS: dict[str, dict[str, float | int | str]] = {
    "h1": {},
    "h2": {},
    "h3": {},
    "h4": {},
    "h5": {},
    "h6": {"max_variants": 1},
    "h7": {},
}


def _hit(directory: str, found: bool = True, **fields: str | None) -> DirectoryHit:
    values: dict[str, str | None] = {
        "name": "Dachdeckerei Muster GmbH",
        "address": "Hauptstraße 5, 48143 Münster",
        "phone": "0251 123456",
    }
    values.update(fields)
    return DirectoryHit(
        directory=directory,
        found=found,
        listing_url=None,
        name=values["name"] if found else None,
        address=values["address"] if found else None,
        phone=values["phone"] if found else None,
    )


def _with_hits(raw: RawAudit, hits: list[DirectoryHit]) -> RawAudit:
    return raw.model_copy(update={"directories": hits})


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, THRESHOLDS[check_id])[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, THRESHOLDS[check_id])[0] is (
        CheckOutcome.NOT_MEASURABLE
    )


def test_normalisation_keys() -> None:
    assert phone_key("+49 (251) 12 34-56") == phone_key("0251/123456") == "0251123456"
    assert phone_key("0049 251 123456") == "0251123456"
    assert phone_key("") is None
    assert address_key("Hauptstr. 5, 48143 Münster") == address_key(
        "Hauptstraße 5 48143 Münster, Deutschland"
    )
    assert address_key("Ringweg 1, Berlin") == "ringweg1|"
    assert address_key(None) is None


def test_h1_found_with_different_phone_is_partial(raw_audit_full: RawAudit) -> None:
    raw = _with_hits(raw_audit_full, [_hit("Apple Business Connect", phone="0251 999")])
    outcome, measured = run("h1", raw, {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["listings"]["Apple Business Connect"]["mismatches"] == ["phone"]


def test_h2_not_found_fails(raw_audit_full: RawAudit) -> None:
    raw = _with_hits(raw_audit_full, [_hit("Bing Places", found=False)])
    assert run("h2", raw, {})[0] is CheckOutcome.FAILED


@pytest.mark.parametrize(
    ("found", "expected"),
    [(7, CheckOutcome.PASSED), (4, CheckOutcome.PARTIAL), (3, CheckOutcome.FAILED)],
)
def test_h3_core_ratio(raw_audit_full: RawAudit, found: int, expected: CheckOutcome) -> None:
    names = ["Gelbe Seiten", "Das Örtliche", "11880", "GoLocal", "Cylex", "Meinestadt"]
    names += ["Werkenntdenbesten", "Gelbe Seiten", "Das Örtliche", "11880"]
    hits = [_hit(name, found=index < found) for index, name in enumerate(names)]
    outcome, measured = run("h3", _with_hits(raw_audit_full, hits), {})
    assert outcome is expected
    assert measured["found_ratio"] == found / 10


def test_h4_some_industry_directories(raw_audit_full: RawAudit) -> None:
    hits = [_hit("MyHammer"), _hit("Handwerkskammer", found=False)]
    outcome, measured = run("h4", _with_hits(raw_audit_full, hits), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["missing"] == ["Handwerkskammer"]


def test_h5_social_missing(raw_audit_full: RawAudit) -> None:
    hits = [_hit("Facebook", found=False), _hit("Instagram", found=False)]
    assert run("h5", _with_hits(raw_audit_full, hits), {})[0] is CheckOutcome.FAILED


def test_h6_counts_distinct_variants(raw_audit_full: RawAudit) -> None:
    hits = [
        _hit("Gelbe Seiten", phone="0251 123456", address="Hauptstr. 5, 48143 Münster"),
        _hit("11880", phone="+49 251 123456", address="Hauptstraße 5 48143 Münster"),
        _hit("Das Örtliche", phone="0251 654321", address="Alter Weg 2, 48143 Münster"),
        _hit("Cylex", found=False),
    ]
    outcome, measured = run("h6", _with_hits(raw_audit_full, hits), THRESHOLDS["h6"])
    assert outcome is CheckOutcome.FAILED
    assert measured["phone_variants"] == ["0251123456", "0251654321"]
    assert measured["address_variants"] == ["alterweg2|48143", "hauptstr5|48143"]
    assert measured["listings_compared"] == 3


def test_h6_only_phone_differs_is_partial(raw_audit_full: RawAudit) -> None:
    hits = [_hit("Gelbe Seiten"), _hit("11880", phone="0251 1")]
    assert run("h6", _with_hits(raw_audit_full, hits), THRESHOLDS["h6"])[0] is (
        CheckOutcome.PARTIAL
    )


def test_h6_without_listing_data_not_measurable(raw_audit_full: RawAudit) -> None:
    hits = [_hit("Gelbe Seiten", phone=None, address=None)]
    assert run("h6", _with_hits(raw_audit_full, hits), THRESHOLDS["h6"])[1] == {
        "reason": "no_listing_data"
    }


def test_h7_wikidata_missing(raw_audit_full: RawAudit) -> None:
    raw = _with_hits(raw_audit_full, [_hit("Wikidata", found=False)])
    assert run("h7", raw, {}) == (CheckOutcome.FAILED, {"found": False, "listing_url": None})


def test_listing_compared_with_footer_without_place(raw_audit_full: RawAudit) -> None:
    raw = _with_hits(raw_audit_full, [_hit("Bing Places", address="Ringweg 1, 10115 Berlin")])
    raw = raw.model_copy(update={"place": None})
    outcome, measured = run("h2", raw, {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["listings"]["Bing Places"]["mismatches"] == ["address"]
