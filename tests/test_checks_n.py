"""Tests for the checks of area n (werkzeuge) and the script fields they read.

Context: Pure rule tests on RawAudit fixtures and on parsed start pages; no network. |
Input: Fixtures and inline HTML. | Output: Assertions.
"""

from collections.abc import Callable

import pytest
from pydantic import HttpUrl

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.collectors.web_tools import google_tag_ids
from radarauditor.collectors.website import parse_page
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.n_werkzeuge import run

START = "https://www.dachdeckerei-muster.de/"
Thresholds = dict[str, float | int | str]


def _with_start_page(raw: RawAudit, html: str) -> RawAudit:
    """The audit with its start page replaced by the parsed HTML."""
    assert raw.website is not None
    parsed = parse_page(HttpUrl(START), 200, html)
    pages = list(raw.website.pages)
    pages[0] = parsed
    website = raw.website.model_copy(update={"start_url": HttpUrl(START), "pages": pages})
    return raw.model_copy(update={"website": website})


def _page(head: str = "", body: str = "") -> str:
    return f"<html><head>{head}</head><body><h1>Dach</h1>{body}</body></html>"


@pytest.fixture
def start_page() -> Callable[..., RawAudit]:
    def build(raw: RawAudit, head: str = "", body: str = "") -> RawAudit:
        return _with_start_page(raw, _page(head, body))

    return build


Build = Callable[..., RawAudit]


@pytest.mark.parametrize("check_id", ["n1", "n2", "n3"])
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, {})[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", ["n1", "n2", "n3"])
def test_without_website_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, {}) == (
        CheckOutcome.NOT_MEASURABLE,
        {"reason": "no_website"},
    )


@pytest.mark.parametrize("check_id", ["n1", "n2", "n3"])
def test_start_page_crawled_before_script_fields_is_not_measurable(
    raw_audit_full: RawAudit, check_id: str
) -> None:
    assert raw_audit_full.website is not None
    pages = list(raw_audit_full.website.pages)
    pages[0] = pages[0].model_copy(
        update={"script_sources": None, "inline_script_signatures": None}
    )
    website = raw_audit_full.website.model_copy(update={"pages": pages})
    raw = raw_audit_full.model_copy(update={"website": website})
    assert run(check_id, raw, {}) == (
        CheckOutcome.NOT_MEASURABLE,
        {"reason": "scripts_not_recorded"},
    )


@pytest.mark.parametrize(
    ("head", "body", "tools", "messengers"),
    [
        ('<script src="https://embed.tawk.to/abc/default"></script>', "", ["tawk.to"], []),
        ('<script src="https://client.crisp.chat/l.js"></script>', "", ["Crisp"], []),
        ('<script src="//code.tidio.co/abc.js"></script>', "", ["Tidio"], []),
        (
            '<script src="https://userlike-cdn-widgets.s3.amazonaws.com/x.js"></script>',
            "",
            ["Userlike"],
            [],
        ),
        ("<script>window.intercomSettings = {app_id: 'x'};</script>", "", ["Intercom"], []),
        (
            '<script id="ze-snippet" src="https://static.zdassets.com/ekr/snippet.js"></script>',
            "",
            ["Zendesk Chat"],
            [],
        ),
        (
            '<script src="//js.usemessages.com/conversations-embed.js"></script>',
            "",
            ["HubSpot Conversations"],
            [],
        ),
        ("<script>window.__lc = {}; window.__lc.license = 1;</script>", "", ["LiveChat"], []),
        ("<script>var _smartsupp = _smartsupp || {};</script>", "", ["Smartsupp"], []),
        ('<script src="//code.jivosite.com/widget/abc"></script>', "", ["JivoChat"], []),
        ("<script>window.chatwootSDK.run({websiteToken: 'x'});</script>", "", ["Chatwoot"], []),
        ("", '<a href="https://m.me/dachdeckereimuster">Messenger</a>', [], ["Facebook Messenger"]),
        ("", '<a href="https://wa.me/49251123456">WhatsApp</a>', [], ["WhatsApp"]),
        ("", '<a href="https://api.whatsapp.com/send?phone=49251">WhatsApp</a>', [], ["WhatsApp"]),
    ],
)
def test_n1_finds_chat_widgets_and_messenger_links(
    raw_audit_full: RawAudit,
    start_page: Build,
    head: str,
    body: str,
    tools: list[str],
    messengers: list[str],
) -> None:
    outcome, measured = run("n1", start_page(raw_audit_full, head, body), {})
    assert outcome is CheckOutcome.PASSED
    assert (measured["chat_tools"], measured["messenger_links"]) == (tools, messengers)


def test_n1_without_chat_fails(raw_audit_full: RawAudit, start_page: Build) -> None:
    body = '<a href="https://www.facebook.com/dachdeckereimuster">Facebook</a>'
    assert run("n1", start_page(raw_audit_full, "", body), {}) == (
        CheckOutcome.FAILED,
        {"chat_tools": [], "messenger_links": [], "start_page": START},
    )


GTAG = (
    '<script async src="https://www.googletagmanager.com/gtag/js?id={id}"></script>'
    "<script>window.dataLayer = window.dataLayer || [];"
    " function gtag(){{dataLayer.push(arguments);}} gtag('js', new Date());"
    " gtag('config', '{id}');{extra}</script>"
)


@pytest.mark.parametrize(
    ("head", "tools"),
    [
        (GTAG.format(id="G-AB12CD34EF", extra=""), ["Google Analytics"]),
        (
            "<script>ga('create', 'UA-1234567-1', 'auto');</script>"
            '<script src="https://www.google-analytics.com/analytics.js"></script>',
            ["Google Analytics"],
        ),
        (
            "<script>(function(w,d,s,l,i){w[l]=w[l]||[];j.src="
            "'https://www.googletagmanager.com/gtm.js?id='+i;})(window,document,'script',"
            "'dataLayer','GTM-ABC1234');</script>",
            ["Google Tag Manager"],
        ),
        ("<script>var _paq = window._paq = []; _paq.push(['trackPageView']);</script>", ["Matomo"]),
        ('<script src="https://stats.example.de/piwik.js"></script>', ["Piwik"]),
        (
            '<script defer data-domain="x.de" src="https://plausible.io/js/script.js"></script>',
            ["Plausible"],
        ),
        ('<script src="//code.etracker.com/code/e.js"></script>', ["etracker"]),
        ('<script src="https://cdn.usefathom.com/script.js"></script>', ["Fathom"]),
        ('<script src="https://cloud.umami.is/script.js"></script>', ["Umami"]),
        (
            '<script>(function(c,l,a,r,i,t,y){t.src="https://www.clarity.ms/tag/"+i;})</script>',
            ["Microsoft Clarity"],
        ),
        (
            "<script>h._hjSettings={hjid:1,hjsv:6};a.src='https://static.hotjar.com/c/hotjar-';</script>",
            ["Hotjar"],
        ),
    ],
)
def test_n2_finds_visitor_analytics(
    raw_audit_full: RawAudit, start_page: Build, head: str, tools: list[str]
) -> None:
    outcome, measured = run("n2", start_page(raw_audit_full, head), {})
    assert outcome is CheckOutcome.PASSED
    assert measured["analytics_tools"] == tools


def test_n2_reports_the_tag_ids_without_ads_ids(
    raw_audit_full: RawAudit, start_page: Build
) -> None:
    head = GTAG.format(id="G-AB12CD34EF", extra=" gtag('config', 'AW-123456789');")
    measured = run("n2", start_page(raw_audit_full, head), {})[1]
    assert measured["tag_ids"] == ["G-AB12CD34EF"]


def test_n2_ads_tag_alone_is_no_analytics(raw_audit_full: RawAudit, start_page: Build) -> None:
    head = GTAG.format(id="AW-123456789", extra="")
    assert run("n2", start_page(raw_audit_full, head), {}) == (
        CheckOutcome.FAILED,
        {"analytics_tools": [], "tag_ids": [], "start_page": START},
    )


def test_n3_without_ads_passes(raw_audit_full: RawAudit, start_page: Build) -> None:
    head = GTAG.format(id="G-AB12CD34EF", extra="")
    assert run("n3", start_page(raw_audit_full, head), {}) == (
        CheckOutcome.PASSED,
        {
            "ads_found": False,
            "ads_ids": [],
            "conversions": [],
            "conversion_tracking": False,
            "start_page": START,
        },
    )


@pytest.mark.parametrize(
    ("head", "conversions"),
    [
        (
            GTAG.format(
                id="AW-123456789",
                extra=" gtag('event', 'conversion', {'send_to': 'AW-123456789/AbC-dE_f1'});",
            ),
            ["AW-123456789/AbC-dE_f1"],
        ),
        (
            GTAG.format(id="AW-123456789", extra="")
            + "<script>var google_conversion_id = 123456789;</script>"
            '<script src="//www.googleadservices.com/pagead/conversion.js"></script>',
            ["AW-123456789"],
        ),
        (
            GTAG.format(id="AW-123456789", extra="")
            + '<script src="https://www.googleadservices.com/pagead/conversion_async.js"></script>',
            ["pagead/conversion"],
        ),
    ],
)
def test_n3_ads_with_conversion_measurement_passes(
    raw_audit_full: RawAudit, start_page: Build, head: str, conversions: list[str]
) -> None:
    outcome, measured = run("n3", start_page(raw_audit_full, head), {})
    assert outcome is CheckOutcome.PASSED
    assert measured["ads_ids"] == ["AW-123456789"]
    assert measured["conversions"] == conversions
    assert measured["conversion_tracking"] is True


def test_n3_ads_without_conversion_measurement_is_partial(
    raw_audit_full: RawAudit, start_page: Build
) -> None:
    head = GTAG.format(id="AW-987654321", extra="")
    assert run("n3", start_page(raw_audit_full, head), {}) == (
        CheckOutcome.PARTIAL,
        {
            "ads_found": True,
            "ads_ids": ["AW-987654321"],
            "conversions": [],
            "conversion_tracking": False,
            "start_page": START,
        },
    )


def test_parse_page_records_scripts_for_the_tool_checks() -> None:
    html = _page(
        '<script type="application/ld+json">{"@type": "Thing", "x": "embed.tawk.to"}</script>'
        '<script src="/js/app.js"></script>'
        '<script data-src="https://cdn.usefathom.com/script.js"></script>'
        "<script>var _paq = []; _paq.push(['x']); gtag('config', 'GTM-ABC1234');</script>"
    )
    page = parse_page(HttpUrl(START), 200, html)
    assert page.script_sources == [f"{START}js/app.js", "https://cdn.usefathom.com/script.js"]
    assert page.inline_script_signatures == ["_paq.push"]
    assert page.google_tag_ids == ["GTM-ABC1234"]
    assert page.ads_conversions == []


def test_google_tag_ids_ignore_class_names_and_plugin_paths() -> None:
    """Class names such as g-recaptcha and plugin folders starting with g- are no Google tag."""
    texts = [
        '<div class="g-recaptcha" data-callback="x"></div> "#g-recaptcha-response"',
        "/wp-content/plugins/g-translate/x.js",
        "https://www.googletagmanager.com/gtag/js?id=G-AB12CD34EF",
    ]
    assert google_tag_ids(texts) == ["G-AB12CD34EF"]
