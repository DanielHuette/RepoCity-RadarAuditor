"""Checks of area d: firmendaten (ids d1..).

Context: One pure function per catalogue check; each returns outcome and measured values.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    NAP_KEYS,
    CheckFunction,
    CheckReturn,
    Thresholds,
    combine,
    content_pages,
    dispatch,
    is_google_profile_link,
    nap_value,
    not_measurable,
    phone_key,
    postal_code,
    schema_objects,
    street_key,
    string_values,
)


def footer_nap_text(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """d1: name, street, postal code and phone appear as text in the footer."""
    if raw.website is None:
        return not_measurable("no_website")
    nap = raw.website.footer_nap
    found = {field: nap_value(nap, field) for field in NAP_KEYS}
    missing = [field for field, value in found.items() if value is None]
    outcome = combine(
        CheckOutcome.FAILED if value is None else CheckOutcome.PASSED for value in found.values()
    )
    return outcome, {"footer_nap": found, "missing_fields": missing}


def nap_matches_profile(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """d2: phone, postal code and street on the website equal the Google profile."""
    if raw.place is None:
        return not_measurable("no_place")
    if raw.website is None or not raw.website.footer_nap:
        return not_measurable("no_footer_nap")
    nap = raw.website.footer_nap
    place = raw.place
    google_street = place.formatted_address.split(",")[0]
    pairs = {
        "phone": (phone_key(nap_value(nap, "phone")), phone_key(place.phone)),
        "postal_code": (
            postal_code(nap_value(nap, "postal_code") or nap_value(nap, "street")),
            postal_code(place.formatted_address),
        ),
        "street": (street_key(nap_value(nap, "street")), street_key(google_street)),
    }
    compared = {field: pair for field, pair in pairs.items() if pair[1] is not None}
    if not compared:
        return not_measurable("no_profile_nap")
    mismatches = [field for field, (site, google) in compared.items() if site != google]
    outcome = combine(
        CheckOutcome.FAILED if field in mismatches else CheckOutcome.PASSED for field in compared
    )
    return outcome, {
        "website": {"phone": nap_value(nap, "phone"), "street": nap_value(nap, "street")},
        "google": {"phone": place.phone, "address": place.formatted_address},
        "mismatches": mismatches,
    }


def opening_hours(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """d3: opening hours are on the website and in the Google profile; texts are not compared."""
    if raw.website is None:
        return not_measurable("no_website")
    on_site = bool(raw.website.opening_hours_text)
    on_google = raw.place.has_opening_hours if raw.place is not None else None
    parts = [on_site] if on_google is None else [on_site, on_google]
    outcome = combine(CheckOutcome.PASSED if part else CheckOutcome.FAILED for part in parts)
    return outcome, {
        "hours_on_website": on_site,
        "hours_on_google": on_google,
        "hours_text_compared": False,
    }


def location_page(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """d4: a page names the location's street and postal code in its text."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    nap = raw.website.footer_nap
    address = raw.place.formatted_address if raw.place is not None else None
    street = street_key(nap_value(nap, "street") or (address.split(",")[0] if address else None))
    code = postal_code(nap_value(nap, "postal_code") or address)
    if street is None or code is None:
        return not_measurable("no_address")
    pages = [
        str(page.url)
        for page in content_pages(raw.website)
        if code in page.text_excerpt and street in (street_key(page.text_excerpt) or "")
    ]
    outcome = CheckOutcome.PASSED if pages else CheckOutcome.FAILED
    return outcome, {"location_pages": pages[:10], "postal_code": code}


def google_profile_link(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """d5: a page links the Google profile or map, as a link or in its structured data."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    profile_url = (
        str(raw.place.google_maps_uri)
        if raw.place is not None and raw.place.google_maps_uri is not None
        else None
    )
    schema_links = [
        link
        for page in pages
        for item in schema_objects(page.json_ld)
        for key in ("hasMap", "sameAs")
        for link in string_values(item.get(key))
    ]
    page_links = [str(link) for page in pages for link in page.external_links]
    found = sorted(
        {link for link in schema_links + page_links if is_google_profile_link(link, profile_url)}
    )
    outcome = CheckOutcome.PASSED if found else CheckOutcome.FAILED
    return outcome, {"google_profile_links": found[:10]}


def clickable_contact(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """d6: the site has tel: and mailto: links."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = content_pages(raw.website)
    tel = sorted({link for page in pages for link in page.tel_links})
    mail = sorted({link for page in pages for link in page.mailto_links})
    outcome = combine(
        CheckOutcome.PASSED if links else CheckOutcome.FAILED for links in (tel, mail)
    )
    return outcome, {"tel_links": tel[:10], "mailto_links": mail[:10]}


CHECKS: dict[str, CheckFunction] = {
    "d1": footer_nap_text,
    "d2": nap_matches_profile,
    "d3": opening_hours,
    "d4": location_page,
    "d5": google_profile_link,
    "d6": clickable_contact,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
