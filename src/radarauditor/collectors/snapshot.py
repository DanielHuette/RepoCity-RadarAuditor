"""Typed snapshots returned by the collectors and the merged RawAudit.

Context: The RawAudit is stored as JSON in Supabase storage and handed to scoring and to Claude
Code.
Input: Collector results.
Output: RawAudit.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

from radarauditor.db.app_settings import Source

DataRoute = Literal["browser", "places_api"]
"""How a value was read: Google Maps in the server browser, or the paid Places API (New)."""


class PageSnapshot(BaseModel):
    """What one crawled page contains.

    external_links are distinct links to other sites; third_party_hosts are the hosts of scripts,
    stylesheets, frames, images and inline font faces loaded from outside the site.

    The script fields feed the website tool checks: script_sources are the absolute addresses of
    external scripts, inline_script_signatures the known tool markers (see web_tools) found in
    inline scripts, google_tag_ids the Google tag ids (G-, UA-, GTM-, AW-) in scripts and script
    addresses, ads_conversions the Google Ads conversion measurements (send_to targets
    "AW-id/label", google_conversion_id values, googleadservices conversion calls). None means the
    page was crawled before these fields existed.
    """

    url: HttpUrl
    status_code: int
    title: str | None
    meta_description: str | None
    canonical: HttpUrl | None
    lang: str | None
    headings: list[tuple[int, str]]
    word_count: int
    json_ld: list[dict[str, object]]
    open_graph: dict[str, str]
    internal_links: list[HttpUrl]
    images_without_alt: int
    oversized_images: int
    tel_links: list[str]
    mailto_links: list[str]
    text_excerpt: str
    has_viewport_meta: bool = False
    external_links: list[HttpUrl] = Field(default_factory=list)
    third_party_hosts: list[str] = Field(default_factory=list)
    script_sources: list[str] | None = None
    inline_script_signatures: list[str] | None = None
    google_tag_ids: list[str] | None = None
    ads_conversions: list[str] | None = None


class WebsiteSnapshot(BaseModel):
    """Site-wide facts plus the crawled pages."""

    start_url: HttpUrl
    uses_https: bool
    redirects_to_single_host: bool
    robots_txt: str | None
    sitemap_urls: list[HttpUrl]
    legal_notice_url: HttpUrl | None
    privacy_url: HttpUrl | None
    footer_nap: dict[str, str]
    opening_hours_text: str | None
    pages: list[PageSnapshot]
    crawl_errors: list[str]


class PageSpeedSnapshot(BaseModel):
    """Core Web Vitals and scores from PageSpeed Insights, mobile and desktop."""

    url: HttpUrl
    mobile_performance: int
    desktop_performance: int
    lcp_ms: int
    cls: float
    inp_ms: int | None
    accessibility: int


PLACES_API_FIELDS = frozenset(
    {
        "place_id",
        "name",
        "formatted_address",
        "phone",
        "website",
        "primary_type",
        "types",
        "rating",
        "review_count",
        "has_opening_hours",
        "photo_count",
        "recent_reviews",
        "business_status",
        "google_maps_uri",
    }
)


class PlaceSnapshot(BaseModel):
    """Public Google Business Profile facts, read from Google Maps in the browser or the Places API.

    Each recent review keeps publishTime, rating, text and relativePublishTimeDescription. Reviews
    read in the browser are the newest ones and always carry reviewReply (None, or a dict with
    text, publishTime and relativePublishTimeDescription of the owner's answer); their times are
    derived from Google's relative dates. The Places API returns its most relevant reviews without
    owner replies.

    The fields from description on are only shown on the Maps page; None means the route did not
    read them, an empty list means the page shows none. post_ages_days holds the age of every
    visible profile post, newest first; question_count the questions under "Fragen und Antworten".
    photo_newest_date is the capture date of the newest dated photo in the Maps photo viewer
    (Street View and 360° images left out; a month-year date counts as the last day of that
    month), photo_dated_count how many photos with a date were seen; a count of 0 with no date
    means the viewer was opened but showed no dated photo.
    value_routes names the route of every field that was read; an empty map is a snapshot stored
    before routes were recorded, which came from the Places API.
    """

    place_id: str
    name: str
    formatted_address: str
    phone: str | None
    website: HttpUrl | None
    primary_type: str | None
    types: list[str]
    rating: float | None
    review_count: int
    has_opening_hours: bool
    photo_count: int
    recent_reviews: list[dict[str, object]]
    business_status: str | None
    google_maps_uri: HttpUrl | None = None
    description: str | None = None
    services: list[str] | None = None
    products: list[str] | None = None
    attributes: list[str] | None = None
    service_areas: list[str] | None = None
    post_ages_days: list[int] | None = None
    question_count: int | None = None
    photo_newest_date: date | None = None
    photo_dated_count: int | None = None
    value_routes: dict[str, DataRoute] = Field(default_factory=dict)

    def route_of(self, field: str) -> DataRoute | None:
        """Return the route that delivered a field, or None when no route read it.

        Snapshots without recorded routes came from the Places API, which reads every field
        it has.
        """
        if not self.value_routes:
            return "places_api" if field in PLACES_API_FIELDS else None
        return self.value_routes.get(field)


class MapRanking(BaseModel):
    """Position of the customer in the Google Maps result list for one search phrase.

    position is None when the customer is not among the listed results; listed holds the names of
    the results in order (empty when the route returns identifiers only).
    """

    phrase: str
    position: int | None
    listed: list[str] = Field(default_factory=list)
    route: DataRoute


class ListedBusiness(BaseModel):
    """One business of a Google Maps result list with its review count.

    review_count is None where the list does not show it; is_customer marks the audited business.
    """

    name: str
    review_count: int | None
    rating: float | None = None
    place_id: str | None = None
    is_customer: bool = False


class MapResultList(BaseModel):
    """Every business Google Maps lists for the primary keyword, in Google's order.

    route says whether the list was read in the browser or came from the Places text search.
    """

    phrase: str
    businesses: list[ListedBusiness]
    route: DataRoute


class SerpResult(BaseModel):
    """One search executed from the operator's location.

    Organic entries are distinct domains in page order. For the image vertical they are the
    source hosts of the first image results. Ads hold advertiser hosts, or names where a sponsored
    block shows no link. route says where the result was read: in the server browser, or through
    the DataForSEO SERP API when Google blocked the browser.
    """

    phrase: str
    organic: list[tuple[int, str]]
    local_pack: list[tuple[int, str]]
    ai_overview_present: bool
    ai_overview_mentions: list[str]
    people_also_ask: list[str]
    measured_at: datetime
    engine: Literal["google", "bing"] = "google"
    ads: list[str] = Field(default_factory=list)
    knowledge_panel: bool = False
    vertical: Literal["web", "images"] = "web"
    route: Literal["browser", "dataforseo"] = "browser"


class DeferredSearch(BaseModel):
    """A Google search that could not run because Google blocked the browser; retried later."""

    phrase: str
    vertical: Literal["web", "images"] = "web"


class DirectoryHit(BaseModel):
    """Whether a directory lists the business and how it spells the NAP."""

    directory: str
    found: bool
    listing_url: HttpUrl | None
    name: str | None
    address: str | None
    phone: str | None


AnswerRoute = Literal["browser", "api", "claude_code"]


class AssistantAnswer(BaseModel):
    """One answer of an AI assistant to a local recommendation question.

    route says how the answer was obtained: the assistant's website in the server browser, its
    paid interface after the browser was blocked, or the Claude Code CLI. Audits collected before
    routes were recorded have None.
    """

    provider: str
    question: str
    mentioned: bool
    cited_sources: list[str]
    answer_excerpt: str
    route: AnswerRoute | None = None


class GridPoint(BaseModel):
    """Local pack position of the customer at one point of the grid around its location.

    key is "r{row}c{column}", both 1-based, row 1 in the north and column 1 in the west; the app
    and the ranking rows use it. position is None where the customer is not listed; route names
    how the point was measured (None for points stored before routes were recorded).
    """

    key: str = Field(pattern=r"^r[1-9]\d*c[1-9]\d*$")
    latitude: float
    longitude: float
    position: int | None
    route: DataRoute | None = None


class SearchQuery(BaseModel):
    """One search query of the customer's site in Google Search Console."""

    query: str
    clicks: int
    impressions: int
    ctr: float
    position: float


class AnalyticsSnapshot(BaseModel):
    """Visits and enquiries of the website from Google Analytics 4 over the last 28 days.

    key_events counts what GA4 calls key events (formerly conversions).
    """

    property_id: str
    sessions: int
    engaged_sessions: int
    key_events: int


class AccessSnapshot(BaseModel):
    """Data from the customer's own Google accounts, read with the operator's authorisation.

    profile_metrics sums the Business Profile daily metrics over the last 28 days and
    profile_metrics_previous over the 28 days before; empty means the profile was not read.
    search_totals holds clicks, impressions, ctr and position of the whole site; empty means
    Search Console was not read. owner_reviews keep publishTime, rating, text and reviewReply
    (None or a dict with text and updateTime), the shape the review checks read.
    """

    profile_metrics: dict[str, int] = Field(default_factory=dict)
    profile_metrics_previous: dict[str, int] = Field(default_factory=dict)
    search_queries: list[SearchQuery] = Field(default_factory=list)
    search_totals: dict[str, float] = Field(default_factory=dict)
    analytics: AnalyticsSnapshot | None = None
    owner_reviews: list[dict[str, object]] = Field(default_factory=list)
    collected_at: datetime


class BacklinkSummary(BaseModel):
    """Referring domains of one domain; shares are None where the sample could not be read."""

    domain: str
    referring_domains: int
    referring_domains_local_share: float | None = None
    spam_score: int | None = None
    referring_domains_spam_share: float | None = None


class GapKeyword(BaseModel):
    """A search phrase a competitor ranks for and the customer does not."""

    phrase: str
    volume: int
    competitor_domain: str
    competitor_position: int | None


class PaidSnapshot(BaseModel):
    """Volumes, links and keyword data from DataForSEO.

    Keys of volumes are the searched phrases ("<keyword> <city>"); primary_phrases lists those of
    primary keywords. keyword_gap is None when it could not be read, an empty list when there is
    no gap. historic_visibility holds (YYYY-MM, top-10 keyword count) oldest first.
    """

    customer_domain: str | None = None
    volumes: dict[str, int] = Field(default_factory=dict)
    primary_phrases: list[str] = Field(default_factory=list)
    backlinks: dict[str, BacklinkSummary] = Field(default_factory=dict)
    ranked_keywords: dict[str, int] = Field(default_factory=dict)
    keyword_gap: list[GapKeyword] | None = None
    historic_visibility: list[tuple[str, float]] = Field(default_factory=list)
    collected_at: datetime


IdentifiedField = Literal[
    "name", "street", "postal_code", "city", "phone", "google_place_id", "industry", "keywords"
]
"""A customer or location value the identify step filled in."""

IdentificationSource = Literal[
    "json_ld",
    "impressum",
    "footer",
    "google_profile",
    "google_types",
    "name_words",
    "industry_services",
]
"""Where an identified value came from: JSON-LD of the start page, the legal notice (Impressum),
the footer contact data, the Google profile, the profile categories, the words of the name and
category text, or the typical services of the industry group (keywords)."""


class IdentifiedValue(BaseModel):
    """One value the identify step filled in for an audit started with only the website address.

    For field "keywords" value lists the created phrases separated by ", ", for "industry" it is
    the group id of config/branchen.yaml.
    """

    field: IdentifiedField
    value: str
    source: IdentificationSource


class RawAudit(BaseModel):
    """Everything collected for one audit; grid holds the map grid of the primary keyword.

    access is filled only for audits with customer access, paid only with DataForSEO switched on.
    capped_sources lists the sources whose daily cap ran out during collection, cost_capped_sources
    those whose monthly cost cap was reached; deferred_searches the Google searches a serp_retry job
    still has to run. domain_first_capture is the day the Wayback Machine first archived the
    customer's domain, None when it never did or could not be asked. map_rankings holds the
    Google Maps position per keyword; map_results the whole Maps result list of the primary
    keyword with review counts, None for audits collected before it was recorded. identified lists
    what the identify step filled in, with its source, for an audit started without name, city,
    industry or keywords.
    """

    website: WebsiteSnapshot | None
    pagespeed: PageSpeedSnapshot | None
    place: PlaceSnapshot | None
    competitor_places: list[PlaceSnapshot]
    serps: list[SerpResult]
    directories: list[DirectoryHit]
    assistant_answers: list[AssistantAnswer]
    collected_at: datetime
    warnings: list[str]
    grid: list[GridPoint] = Field(default_factory=list)
    access: AccessSnapshot | None = None
    paid: PaidSnapshot | None = None
    capped_sources: list[Source] = Field(default_factory=list)
    cost_capped_sources: list[Source] = Field(default_factory=list)
    deferred_searches: list[DeferredSearch] = Field(default_factory=list)
    domain_first_capture: date | None = None
    map_rankings: list[MapRanking] = Field(default_factory=list)
    map_results: MapResultList | None = None
    identified: list[IdentifiedValue] = Field(default_factory=list)
