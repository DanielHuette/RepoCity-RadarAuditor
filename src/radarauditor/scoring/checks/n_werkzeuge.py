"""Checks of area n: werkzeuge (ids n1..), the tools built into the customer's website.

Context: One pure function per catalogue check; each returns outcome and measured values. All
three read the start page as the crawler recorded it: external script addresses, known markers in
inline scripts, Google tag ids, Google Ads conversion measurements and links to other sites.
Whether a tool loads only after consent is judged by l3, not here. A start page crawled before
the script fields existed is not measurable.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

from radarauditor.collectors.snapshot import PageSnapshot, RawAudit
from radarauditor.collectors.web_tools import (
    ADS_MARKERS,
    ANALYTICS_TOOLS,
    CHAT_TOOLS,
    MESSENGER_LINKS,
    tools_in,
)
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    CheckFunction,
    CheckReturn,
    Thresholds,
    dispatch,
    listed,
    not_measurable,
    start_page,
)

ANALYTICS_ID_PREFIXES = {"G-": "Google Analytics", "UA-": "Google Analytics"}
TAG_MANAGER_PREFIX = "GTM-"
ADS_ID_PREFIX = "AW-"


def _recorded_start_page(raw: RawAudit) -> PageSnapshot | CheckReturn:
    """The crawled start page with its script fields, or the not-measurable answer."""
    if raw.website is None:
        return not_measurable("no_website")
    page = start_page(raw.website)
    if page is None:
        return not_measurable("no_website")
    if page.script_sources is None or page.inline_script_signatures is None:
        return not_measurable("scripts_not_recorded")
    return page


def _script_texts(page: PageSnapshot) -> list[str]:
    """Script addresses and inline markers of a page, the texts the tool tables match on."""
    return [*(page.script_sources or []), *(page.inline_script_signatures or [])]


def chat_contact(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """n1: a chat widget or a messenger link (WhatsApp, Facebook Messenger) is on the start page."""
    page = _recorded_start_page(raw)
    if not isinstance(page, PageSnapshot):
        return page
    widgets = tools_in(CHAT_TOOLS, _script_texts(page))
    messengers = tools_in(MESSENGER_LINKS, [str(link) for link in page.external_links])
    outcome = CheckOutcome.PASSED if widgets or messengers else CheckOutcome.FAILED
    return outcome, {
        "chat_tools": widgets,
        "messenger_links": messengers,
        "start_page": str(page.url),
    }


def visitor_analytics(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """n2: a visitor counting service is built into the start page; consent is l3's matter."""
    page = _recorded_start_page(raw)
    if not isinstance(page, PageSnapshot):
        return page
    tag_ids = page.google_tag_ids or []
    found = tools_in(ANALYTICS_TOOLS, _script_texts(page))
    for tag_id in tag_ids:
        for prefix, tool in ANALYTICS_ID_PREFIXES.items():
            if tag_id.startswith(prefix) and tool not in found:
                found.append(tool)
        if tag_id.startswith(TAG_MANAGER_PREFIX) and "Google Tag Manager" not in found:
            found.append("Google Tag Manager")
    ids = [tag_id for tag_id in tag_ids if not tag_id.startswith(ADS_ID_PREFIX)]
    outcome = CheckOutcome.PASSED if found else CheckOutcome.FAILED
    return outcome, {"analytics_tools": found, "tag_ids": listed(ids), "start_page": str(page.url)}


def google_ads(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """n3: no Google Ads tag, or one measuring conversions; a tag without measurement is partial.

    An Ads tag is an AW- id or a googleadservices / google_conversion_id call; conversions are
    measured by a send_to target "AW-id/label", a google_conversion_id or a googleadservices
    conversion call.
    """
    page = _recorded_start_page(raw)
    if not isinstance(page, PageSnapshot):
        return page
    ads_ids = [tag_id for tag_id in page.google_tag_ids or [] if tag_id.startswith(ADS_ID_PREFIX)]
    conversions = page.ads_conversions or []
    texts = [text.casefold() for text in _script_texts(page)]
    ads_calls = any(marker in text for text in texts for marker in ADS_MARKERS)
    ads_found = bool(ads_ids or conversions or ads_calls)
    if ads_found and not conversions:
        outcome = CheckOutcome.PARTIAL
    else:
        outcome = CheckOutcome.PASSED
    return outcome, {
        "ads_found": ads_found,
        "ads_ids": ads_ids,
        "conversions": listed(conversions),
        "conversion_tracking": bool(conversions),
        "start_page": str(page.url),
    }


CHECKS: dict[str, CheckFunction] = {
    "n1": chat_contact,
    "n2": visitor_analytics,
    "n3": google_ads,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
