"""Checks of area e: strukturierte_daten (ids e1..).

Context: One pure function per catalogue check; each returns outcome and measured values.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

import re
from collections.abc import Mapping

from radarauditor.collectors.snapshot import PageSnapshot, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    CheckFunction,
    CheckReturn,
    Thresholds,
    content_pages,
    dispatch,
    fold,
    listed,
    not_measurable,
    page_schema_types,
    page_text,
    schema_objects,
    schema_types,
    service_pages,
    share_outcome,
    start_page,
    string_values,
)

LOCAL_BUSINESS_TYPES = frozenset(
    {
        "LocalBusiness",
        "HomeAndConstructionBusiness",
        "Plumber",
        "Electrician",
        "RoofingContractor",
        "HVACBusiness",
        "GeneralContractor",
        "HousePainter",
        "Locksmith",
        "MovingCompany",
        "AutomotiveBusiness",
        "AutoRepair",
        "MedicalBusiness",
        "Dentist",
        "Physician",
        "LegalService",
        "Attorney",
        "Notary",
        "AccountingService",
        "FinancialService",
        "FoodEstablishment",
        "Restaurant",
        "Cafe",
        "Bakery",
        "HealthAndBeautyBusiness",
        "BeautySalon",
        "HairSalon",
        "DaySpa",
        "Store",
        "ProfessionalService",
        "RealEstateAgent",
        "ChildCare",
        "EmploymentAgency",
        "TravelAgency",
    }
)
LOCAL_BUSINESS_SUFFIXES = ("Business", "Contractor", "Store", "Service")
ORGANIZATION_TYPES = frozenset({"Organization", "Corporation"})
LOCAL_BUSINESS_REQUIRED = ("name", "address", "telephone", "url", "geo")
OPENING_HOURS_PROPERTIES = ("openingHoursSpecification", "openingHours")
RATING_TYPES = frozenset({"AggregateRating", "Review"})
REQUIRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "FAQPage": ("mainEntity",),
    "BreadcrumbList": ("itemListElement",),
    "Service": ("name",),
    "Organization": ("name",),
    "LocalBusiness": ("name", "address"),
    "PostalAddress": ("streetAddress",),
}
FAQ_HEADING_PATTERN = re.compile(r"(faq|haeufige fragen|fragen und antworten)")
REVIEW_TEXT_PATTERN = re.compile(r"(bewertung|rezension|kundenstimme|erfahrungen|★)")
GOOGLE_MARKERS = ("google.com/maps", "maps.google", "g.page", "maps.app.goo.gl", "cid=")
DIRECTORY_MARKERS = (
    "facebook.com",
    "instagram.com",
    "gelbeseiten.de",
    "dasoertliche.de",
    "11880.com",
    "yelp.",
    "golocal.de",
    "cylex",
    "werkenntdenbesten.de",
    "linkedin.com",
    "provenexpert.com",
)
WIKIDATA_MARKER = "wikidata.org"


def is_local_business(item: Mapping[str, object]) -> bool:
    """True if the object is a LocalBusiness or one of its subtypes."""
    kinds = schema_types(item)
    return bool(kinds & LOCAL_BUSINESS_TYPES) or any(
        kind != "Service" and kind.endswith(LOCAL_BUSINESS_SUFFIXES) for kind in kinds
    )


def _is_organization(item: Mapping[str, object]) -> bool:
    """True for Organization, Corporation and every LocalBusiness type."""
    return bool(schema_types(item) & ORGANIZATION_TYPES) or is_local_business(item)


def _site_objects(raw: RawAudit) -> list[dict[str, object]]:
    """Every JSON-LD object of every successful page."""
    if raw.website is None:
        return []
    return [item for page in content_pages(raw.website) for item in schema_objects(page.json_ld)]


def local_business_schema(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """e1: the start page has a LocalBusiness with name, address, phone, URL, geo and hours."""
    if raw.website is None:
        return not_measurable("no_website")
    start = start_page(raw.website)
    if start is None:
        return not_measurable("no_start_page")
    businesses = [item for item in schema_objects(start.json_ld) if is_local_business(item)]
    if not businesses:
        return CheckOutcome.FAILED, {"local_business_type": None, "missing_properties": []}
    best = max(businesses, key=len)
    missing = [prop for prop in LOCAL_BUSINESS_REQUIRED if not best.get(prop)]
    if not any(best.get(prop) for prop in OPENING_HOURS_PROPERTIES):
        missing.append(OPENING_HOURS_PROPERTIES[0])
    outcome = CheckOutcome.PARTIAL if missing else CheckOutcome.PASSED
    return outcome, {
        "local_business_type": sorted(schema_types(best)),
        "missing_properties": missing,
    }


def organization_logo(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """e2: an Organization (or LocalBusiness) object names a logo."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    organizations = [item for item in _site_objects(raw) if _is_organization(item)]
    with_logo = [item for item in organizations if item.get("logo")]
    if with_logo:
        outcome = CheckOutcome.PASSED
    elif organizations:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {"organization_found": bool(organizations), "logo_found": bool(with_logo)}


def _has_service_with_area(page: PageSnapshot) -> bool:
    """True if the page has a Service object with areaServed."""
    return any(
        "Service" in schema_types(item) and item.get("areaServed")
        for item in schema_objects(page.json_ld)
    )


def service_schema(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """e3: every service page has a Service object with areaServed."""
    pages = service_pages(raw)
    if not pages:
        return not_measurable("no_service_pages")
    without = [str(page.url) for page in pages if not _has_service_with_area(page)]
    return share_outcome(len(pages) - len(without), len(pages)), {
        "pages_without_service_schema": listed(without),
        "service_pages_total": len(pages),
    }


def _has_faq_section(page: PageSnapshot) -> bool:
    """True if headings announce a FAQ block or at least two headings are questions."""
    texts = [fold(text) for _, text in page.headings]
    questions = sum(1 for text in texts if text.rstrip().endswith("?"))
    return questions >= 2 or any(FAQ_HEADING_PATTERN.search(text) for text in texts)


def faq_schema(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """e4: pages with a question section carry FAQPage markup."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = [page for page in content_pages(raw.website) if _has_faq_section(page)]
    if not pages:
        return not_measurable("no_faq_sections")
    without = [str(page.url) for page in pages if "FAQPage" not in page_schema_types(page)]
    return share_outcome(len(pages) - len(without), len(pages)), {
        "faq_pages_without_schema": listed(without),
        "faq_pages_total": len(pages),
    }


def breadcrumb_schema(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """e5: every subpage carries BreadcrumbList markup."""
    if raw.website is None:
        return not_measurable("no_website")
    start = start_page(raw.website)
    subpages = [
        page for page in content_pages(raw.website) if start is None or page.url != start.url
    ]
    if not subpages:
        return not_measurable("no_subpages")
    without = [
        str(page.url) for page in subpages if "BreadcrumbList" not in page_schema_types(page)
    ]
    return share_outcome(len(subpages) - len(without), len(subpages)), {
        "pages_without_breadcrumb_schema": listed(without),
        "subpages_total": len(subpages),
    }


def _has_rating_markup(page: PageSnapshot) -> bool:
    """True if the page marks up ratings or reviews."""
    return any(
        schema_types(item) & RATING_TYPES or item.get("aggregateRating") or item.get("review")
        for item in schema_objects(page.json_ld)
    )


def honest_rating_schema(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """e6: rating markup appears only on pages whose visible text shows reviews."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    marked = [page for page in content_pages(raw.website) if _has_rating_markup(page)]
    unbacked = [str(page.url) for page in marked if not REVIEW_TEXT_PATTERN.search(page_text(page))]
    return share_outcome(len(marked) - len(unbacked), len(marked)), {
        "pages_with_rating_markup": len(marked),
        "pages_without_visible_reviews": listed(unbacked),
    }


def _schema_errors(page: PageSnapshot) -> list[dict[str, str]]:
    """Missing @context on top-level objects and missing required properties per type."""
    errors: list[dict[str, str]] = []
    url = str(page.url)
    for block in page.json_ld:
        context = block.get("@context")
        if "schema.org" not in str(context or ""):
            errors.append({"url": url, "type": str(block.get("@type")), "problem": "@context"})
    for item in schema_objects(page.json_ld):
        kinds = schema_types(item)
        if not kinds:
            errors.append({"url": url, "type": "", "problem": "@type"})
        lookup = {"LocalBusiness"} if is_local_business(item) else kinds
        for kind in lookup:
            for prop in REQUIRED_BY_TYPE.get(kind, ()):
                if not item.get(prop):
                    errors.append({"url": url, "type": kind, "problem": prop})
    return errors


def schema_valid(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """e7: structured data has a schema.org context and the required properties of each type."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    pages = [page for page in content_pages(raw.website) if page.json_ld]
    if not pages:
        return not_measurable("no_structured_data")
    errors = {str(page.url): found for page in pages if (found := _schema_errors(page))}
    return share_outcome(len(pages) - len(errors), len(pages)), {
        "errors": [error for found in errors.values() for error in found][:10],
        "pages_with_errors": len(errors),
        "pages_with_structured_data": len(pages),
    }


def same_as_links(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """e8: sameAs links the Google profile, at least one directory or network, and Wikidata."""
    if raw.website is None or not content_pages(raw.website):
        return not_measurable("no_website")
    links = sorted(
        {
            link
            for item in _site_objects(raw)
            if _is_organization(item)
            for link in string_values(item.get("sameAs"))
        }
    )
    targets = {
        "google": any(marker in link for link in links for marker in GOOGLE_MARKERS),
        "directories": any(marker in link for link in links for marker in DIRECTORY_MARKERS),
        "wikidata": any(WIKIDATA_MARKER in link for link in links),
    }
    present = sum(targets.values())
    if present == len(targets):
        outcome = CheckOutcome.PASSED
    elif present:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.FAILED
    return outcome, {
        "same_as": links[:10],
        "missing_targets": [name for name, found in targets.items() if not found],
    }


CHECKS: dict[str, CheckFunction] = {
    "e1": local_business_schema,
    "e2": organization_logo,
    "e3": service_schema,
    "e4": faq_schema,
    "e5": breadcrumb_schema,
    "e6": honest_rating_schema,
    "e7": schema_valid,
    "e8": same_as_links,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
