"""Read and change a customer's WordPress site through its REST API with an application password.

Context: The customer created an application password for a WordPress user and the operator stored
URL, user and password in customer_access. The optimisation step reads pages and posts (raw edit
context) so Claude Code can draft changes against the real text; the apply step writes approved
changes. Search engine titles and descriptions are written through Rank Math's updateMeta route
when Rank Math is active, through the Yoast meta keys when Yoast exposes them, otherwise the change
is reported as manual.
Input: Site URL, user, application password.
Output: Page and post snapshots, updated fields, or IntegrationError.
"""

import logging
from typing import Any, Literal

import httpx
from pydantic import BaseModel, SecretStr
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt

from radarauditor.collectors import transport
from radarauditor.errors import IntegrationError

logger = logging.getLogger(__name__)

API_PREFIX = "/wp-json"
REQUEST_TIMEOUT_SECONDS = 30
PAGE_SIZE = 100
MAX_PAGE_REQUESTS = 3
MAX_POSTS = 20
CONTENT_LIMIT = 6000
ERROR_DETAIL_CHARS = 200
RANK_MATH_NAMESPACE = "rankmath/v1"
YOAST_NAMESPACE = "yoast/v1"
RANK_MATH_META = {"meta_title": "rank_math_title", "meta_description": "rank_math_description"}
YOAST_META = {"meta_title": "_yoast_wpseo_title", "meta_description": "_yoast_wpseo_metadesc"}
LIST_FIELDS = "id,link,status,title,excerpt,content,yoast_head_json"
COLLECTIONS = {"page": "pages", "post": "posts"}

SeoPlugin = Literal["rank_math", "yoast"]
ContentType = Literal["page", "post"]
SeoField = Literal["meta_title", "meta_description"]


class TransientWordPressError(IntegrationError):
    """WordPress answered 429 or 5xx, or could not be reached; the request may be retried."""


class WordPressItem(BaseModel):
    """A page or post as Claude Code sees it: raw title, excerpt and content (truncated)."""

    type: ContentType
    id: int
    link: str
    status: str
    title: str
    excerpt: str
    content: str
    content_truncated: bool
    meta_title: str | None = None
    meta_description: str | None = None


class WordPressSnapshot(BaseModel):
    """Content of wordpress.json in the optimisation folder."""

    url: str
    seo_plugin: SeoPlugin | None
    items: list[WordPressItem]


def site_root(url: str) -> str:
    """Site address without trailing slash and with a scheme ("example.de" gets https://)."""
    cleaned = url.strip().rstrip("/")
    return cleaned if "://" in cleaned else f"https://{cleaned}"


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return f": {body['message'][:ERROR_DETAIL_CHARS]}"
    return ""


def _raw(value: object) -> str:
    """The raw text of a REST field in edit context ({"raw": ..., "rendered": ...})."""
    if isinstance(value, dict):
        raw = value.get("raw", value.get("rendered", ""))
        return raw if isinstance(raw, str) else ""
    return value if isinstance(value, str) else ""


class WordPressClient:
    """One customer's site; the password is used for Basic authentication only."""

    def __init__(
        self,
        url: str,
        user: str,
        password: SecretStr,
        transport_override: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Keep the credentials; transport_override replaces the network in tests."""
        self.url = site_root(url)
        self._auth = httpx.BasicAuth(user, password.get_secret_value().replace(" ", ""))
        self._transport = transport_override

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> Any:
        """Send one request with retries on transient failures and return the JSON body.

        Raises:
            IntegrationError: On a rejected request, a non-JSON answer or after the retries.
        """
        url = f"{self.url}{API_PREFIX}{path}"
        retrying = AsyncRetrying(
            stop=stop_after_attempt(transport.RETRY_ATTEMPTS),
            wait=transport.RETRY_WAIT,
            retry=retry_if_exception_type(TransientWordPressError),
            reraise=True,
        )
        async with httpx.AsyncClient(
            auth=self._auth,
            timeout=REQUEST_TIMEOUT_SECONDS,
            transport=self._transport,
            headers={"User-Agent": transport.USER_AGENT},
            follow_redirects=True,
        ) as client:
            async for attempt in retrying:
                with attempt:
                    return await self._once(client, method, url, params, json)
        raise IntegrationError(f"WordPress {method} {path}: no request attempt was made")

    async def _once(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json: Any,
    ) -> Any:
        try:
            response = await client.request(method, url, params=params, json=json)
        except httpx.TransportError as exc:
            raise TransientWordPressError(
                f"WordPress {self.url} unreachable: {type(exc).__name__}"
            ) from exc
        status = response.status_code
        if status == httpx.codes.TOO_MANY_REQUESTS or status >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise TransientWordPressError(f"WordPress {self.url} answered HTTP {status}")
        if status >= httpx.codes.BAD_REQUEST:
            raise IntegrationError(
                f"WordPress rejected {method} {url} with HTTP {status}{_message(response)}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise IntegrationError(f"WordPress {url} did not answer with JSON") from exc

    async def seo_plugin(self) -> SeoPlugin | None:
        """Which supported search engine plugin is active, from the namespaces of the API root.

        Raises:
            IntegrationError: If the API root cannot be read.
        """
        root = await self._request("GET", "/")
        namespaces = root.get("namespaces", []) if isinstance(root, dict) else []
        if RANK_MATH_NAMESPACE in namespaces:
            return "rank_math"
        if YOAST_NAMESPACE in namespaces:
            return "yoast"
        return None

    async def items(self, content_type: ContentType, limit: int) -> list[WordPressItem]:
        """Published pages or posts in edit context, newest first, up to limit.

        Raises:
            IntegrationError: If the list cannot be read (wrong password, no REST API).
        """
        collected: list[WordPressItem] = []
        for page in range(1, MAX_PAGE_REQUESTS + 1):
            per_page = min(PAGE_SIZE, limit - len(collected))
            params: dict[str, Any] = {
                "context": "edit",
                "per_page": per_page,
                "page": page,
                "status": "publish",
                "_fields": LIST_FIELDS,
            }
            rows = await self._request("GET", f"/wp/v2/{COLLECTIONS[content_type]}", params=params)
            if not isinstance(rows, list):
                raise IntegrationError(f"WordPress {self.url} returned no list of {content_type}s")
            collected += [_item(content_type, row) for row in rows if isinstance(row, dict)]
            if len(rows) < per_page or len(collected) >= limit:
                break
        return collected[:limit]

    async def snapshot(self) -> WordPressSnapshot:
        """Plugin, every published page and the newest posts.

        Raises:
            IntegrationError: If the site cannot be read.
        """
        plugin = await self.seo_plugin()
        pages = await self.items("page", PAGE_SIZE * MAX_PAGE_REQUESTS)
        posts = await self.items("post", MAX_POSTS)
        return WordPressSnapshot(url=self.url, seo_plugin=plugin, items=[*pages, *posts])

    async def update(self, content_type: ContentType, item_id: int, values: dict[str, Any]) -> Any:
        """Write fields of a page or post and return WordPress's answer.

        Raises:
            IntegrationError: If WordPress rejects the change.
        """
        path = f"/wp/v2/{COLLECTIONS[content_type]}/{item_id}"
        return await self._request("POST", path, params={"context": "edit"}, json=values)

    async def update_seo(
        self,
        plugin: SeoPlugin | None,
        content_type: ContentType,
        item_id: int,
        field: SeoField,
        value: str,
    ) -> str | None:
        """Write a search engine title or description; None when done, else why it is manual.

        Raises:
            IntegrationError: If WordPress rejects the change.
        """
        if plugin == "rank_math":
            await self._request(
                "POST",
                f"/{RANK_MATH_NAMESPACE}/updateMeta",
                json={
                    "objectType": "post",
                    "objectID": item_id,
                    "meta": {RANK_MATH_META[field]: value},
                },
            )
            return None
        if plugin == "yoast":
            key = YOAST_META[field]
            answer = await self.update(content_type, item_id, {"meta": {key: value}})
            meta = answer.get("meta") if isinstance(answer, dict) else None
            if isinstance(meta, dict) and meta.get(key) == value:
                return None
            return "Yoast gibt dieses Feld nicht über die Programmierschnittstelle frei"
        return "kein unterstütztes Suchmaschinen-Plugin (Rank Math oder Yoast) aktiv"


def _item(content_type: ContentType, row: dict[str, Any]) -> WordPressItem:
    content = _raw(row.get("content"))
    yoast = row.get("yoast_head_json")
    meta_title = yoast.get("title") if isinstance(yoast, dict) else None
    meta_description = yoast.get("description") if isinstance(yoast, dict) else None
    return WordPressItem(
        type=content_type,
        id=int(row.get("id", 0)),
        link=str(row.get("link", "")),
        status=str(row.get("status", "")),
        title=_raw(row.get("title")),
        excerpt=_raw(row.get("excerpt")),
        content=content[:CONTENT_LIMIT],
        content_truncated=len(content) > CONTENT_LIMIT,
        meta_title=meta_title if isinstance(meta_title, str) else None,
        meta_description=meta_description if isinstance(meta_description, str) else None,
    )
