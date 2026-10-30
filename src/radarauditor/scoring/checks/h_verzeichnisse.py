"""Checks of area h: verzeichnisse (ids h1..).

Context: One pure function per catalogue check; each returns outcome and measured values.
Directory names mirror config/verzeichnisse.yaml.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

from radarauditor.collectors.snapshot import DirectoryHit, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    CheckFunction,
    CheckReturn,
    Thresholds,
    address_key,
    combine,
    dispatch,
    fold,
    nap_value,
    not_measurable,
    number,
    phone_key,
)

APPLE = "Apple Business Connect"
BING = "Bing Places"
WIKIDATA = "Wikidata"
SOCIAL_DIRECTORIES = ("Facebook", "Instagram")
CORE_DIRECTORIES = (
    "Gelbe Seiten",
    "Das Örtliche",
    "11880",
    "GoLocal",
    "Cylex",
    "Meinestadt",
    "Werkenntdenbesten",
)
INDUSTRY_DIRECTORIES = (
    "MyHammer",
    "Check24 Profis",
    "Handwerkskammer",
    "Innung",
    "Jameda",
    "Doctolib",
    "TripAdvisor",
    "OpenTable",
    "Anwalt.de",
    "Steuerberater.de",
    "Autoscout24 Händler",
    "mobile.de Händler",
    "ImmobilienScout24 Anbieter",
)
CORE_PASS_RATIO = 0.7
CORE_PARTIAL_RATIO = 0.4


def hits_named(raw: RawAudit, names: tuple[str, ...]) -> list[DirectoryHit]:
    """Directory hits whose directory name is one of names, ignoring case and umlauts."""
    wanted = {fold(name) for name in names}
    return [hit for hit in raw.directories if fold(hit.directory) in wanted]


def reference_nap(raw: RawAudit) -> tuple[str | None, str | None]:
    """Phone and address key to compare listings with: Google profile first, else website footer."""
    if raw.place is not None:
        return phone_key(raw.place.phone), address_key(raw.place.formatted_address)
    if raw.website is not None:
        nap = raw.website.footer_nap
        parts = (nap_value(nap, "street"), nap_value(nap, "postal_code"))
        line = " ".join(part for part in parts if part)
        return phone_key(nap_value(nap, "phone")), address_key(line or None)
    return None, None


def nap_mismatches(raw: RawAudit, hit: DirectoryHit) -> list[str]:
    """Fields where a found listing differs from the reference NAP; unknown fields are skipped."""
    phone, address = reference_nap(raw)
    mismatches: list[str] = []
    listed_phone = phone_key(hit.phone)
    if phone is not None and listed_phone is not None and listed_phone != phone:
        mismatches.append("phone")
    listed_address = address_key(hit.address)
    if address is not None and listed_address is not None and listed_address != address:
        mismatches.append("address")
    return mismatches


def _listing_check(raw: RawAudit, names: tuple[str, ...]) -> CheckReturn:
    """Found with identical data passes, found with differences is partial, missing fails."""
    hits = hits_named(raw, names)
    if not hits:
        return not_measurable("directory_not_checked")
    outcomes: list[CheckOutcome] = []
    details: dict[str, object] = {}
    for hit in hits:
        mismatches = nap_mismatches(raw, hit) if hit.found else []
        if not hit.found:
            outcomes.append(CheckOutcome.FAILED)
        elif mismatches:
            outcomes.append(CheckOutcome.PARTIAL)
        else:
            outcomes.append(CheckOutcome.PASSED)
        details[hit.directory] = {
            "found": hit.found,
            "listing_url": str(hit.listing_url) if hit.listing_url else None,
            "mismatches": mismatches,
        }
    return combine(outcomes), {"listings": details}


def apple_maps(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """h1: listed in Apple Business Connect with the same phone and address."""
    return _listing_check(raw, (APPLE,))


def bing_places(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """h2: listed in Bing Places with the same phone and address."""
    return _listing_check(raw, (BING,))


def core_directories(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """h3: found in at least 70 % of the checked core directories; 40 % is partial."""
    hits = hits_named(raw, CORE_DIRECTORIES)
    if not hits:
        return not_measurable("directories_not_checked")
    found = sorted(hit.directory for hit in hits if hit.found)
    ratio = len(found) / len(hits)
    if ratio >= CORE_PASS_RATIO:
        outcome = CheckOutcome.PASSED
    elif ratio >= CORE_PARTIAL_RATIO:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {
        "found": found,
        "missing": sorted(hit.directory for hit in hits if not hit.found),
        "found_ratio": round(ratio, 3),
    }


def industry_directories(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """h4: found in every checked industry directory; some is partial."""
    hits = hits_named(raw, INDUSTRY_DIRECTORIES)
    if not hits:
        return not_measurable("directories_not_checked")
    outcome = combine(CheckOutcome.PASSED if hit.found else CheckOutcome.FAILED for hit in hits)
    return outcome, {
        "found": sorted(hit.directory for hit in hits if hit.found),
        "missing": sorted(hit.directory for hit in hits if not hit.found),
    }


def social_profiles(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """h5: Facebook and Instagram profiles exist with the same phone and address."""
    return _listing_check(raw, SOCIAL_DIRECTORIES)


def nap_variants(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """h6: found listings use at most max_variants spellings of phone and of address."""
    found = [hit for hit in raw.directories if hit.found]
    phones = sorted({key for hit in found if (key := phone_key(hit.phone)) is not None})
    addresses = sorted({key for hit in found if (key := address_key(hit.address)) is not None})
    if not phones and not addresses:
        return not_measurable("no_listing_data")
    limit = number(thresholds, "max_variants")
    within = [len(variants) <= limit for variants in (phones, addresses) if variants]
    outcome = combine(CheckOutcome.PASSED if ok else CheckOutcome.FAILED for ok in within)
    return outcome, {
        "phone_variants": phones,
        "address_variants": addresses,
        "listings_compared": len(found),
        "max_variants": limit,
    }


def wikidata(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """h7: Wikidata has an item for the business."""
    hits = hits_named(raw, (WIKIDATA,))
    if not hits:
        return not_measurable("directory_not_checked")
    found = any(hit.found for hit in hits)
    listing = next((str(hit.listing_url) for hit in hits if hit.found and hit.listing_url), None)
    return (CheckOutcome.PASSED if found else CheckOutcome.FAILED), {
        "found": found,
        "listing_url": listing,
    }


CHECKS: dict[str, CheckFunction] = {
    "h1": apple_maps,
    "h2": bing_places,
    "h3": core_directories,
    "h4": industry_directories,
    "h5": social_profiles,
    "h6": nap_variants,
    "h7": wikidata,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
