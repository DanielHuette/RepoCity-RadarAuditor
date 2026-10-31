"""Optimisation: targets, WordPress client, optimize drafts with the fake CLI, apply with fakes.

Context: Supabase is a MagicMock, WordPress and Google are respx routes, Claude Code is the fake
CLI of tests/fixtures. | Input: Fixtures. | Output: Assertions.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from conftest import CUSTOMER_ID, FIXED_TIME, JobFactory
from pydantic import SecretStr

from radarauditor.config import Settings
from radarauditor.db.crm import CrmOrder, CrmProduct, CrmQuote, CrmQuoteItem
from radarauditor.db.models import (
    Audit,
    AuditPurpose,
    AuditStatus,
    CustomerAccess,
    Job,
    JobKind,
    OptimizationChange,
    OptimizationChangeStatus,
)
from radarauditor.errors import ClaudeCodeError, IntegrationError, OptimizationError
from radarauditor.integrations import business_profile
from radarauditor.integrations.wordpress import WordPressClient, site_root
from radarauditor.jobs import worker
from radarauditor.optimization import apply as apply_module
from radarauditor.optimization import prepare
from radarauditor.optimization.apply import apply_order
from radarauditor.optimization.prepare import optimize_order, validate_drafts
from radarauditor.optimization.targets import (
    ProfileField,
    System,
    WordPressField,
    WordPressType,
    parse_target,
)

SITE = "https://dachdecker-muster.example"
API = f"{SITE}/wp-json"
PASSWORD = "abcd efgh ijkl mnop"
ORDER_ID = UUID("00000000-0000-4000-8000-0000000000b1")
QUOTE_ID = UUID("00000000-0000-4000-8000-0000000000a1")
PRODUCT_ID = UUID("00000000-0000-4000-8000-0000000000d1")
LOCATION = "locations/4711"
ACCOUNT = "accounts/42"
GRANT = {
    "refresh_token": "1//refresh",
    "granted_scopes": ["https://www.googleapis.com/auth/business.manage"],
    "obtained_at": "2026-10-01T09:00:00+00:00",
}
PAGE_ROW = {
    "id": 42,
    "link": f"{SITE}/leistungen/",
    "status": "publish",
    "title": {"raw": "Leistungen", "rendered": "Leistungen"},
    "excerpt": {"raw": "", "rendered": ""},
    "content": {"raw": "<!-- wp:paragraph --><p>Wir decken Dächer.</p>", "rendered": ""},
    "yoast_head_json": {"title": "Leistungen - Muster", "description": "Willkommen"},
}


def access(**changes: object) -> CustomerAccess:
    values: dict[str, object] = {
        "customer_id": CUSTOMER_ID,
        "google_business_profile_granted": False,
        "search_console_property": None,
        "wordpress_url": SITE,
        "wordpress_user": "daniel",
        "granted_at": FIXED_TIME,
        "google_location_name": None,
    }
    return CustomerAccess.model_validate({**values, **changes})


def change(target: str, after: str = "Neuer Wert", **changes: object) -> OptimizationChange:
    values: dict[str, object] = {
        "id": uuid4(),
        "order_id": ORDER_ID,
        "audit_id": None,
        "check_id": "b3",
        "target": target,
        "before": None,
        "after": after,
        "status": OptimizationChangeStatus.APPROVED,
        "error": None,
        "created_at": FIXED_TIME,
        "decided_at": FIXED_TIME,
        "applied_at": None,
    }
    return OptimizationChange.model_validate({**values, **changes})


@pytest.fixture
def order_job(make_job: JobFactory) -> Job:
    return make_job(JobKind.OPTIMIZE).model_copy(update={"order_id": ORDER_ID})


@pytest.fixture
def apply_job(make_job: JobFactory) -> Job:
    return make_job(JobKind.APPLY).model_copy(update={"order_id": ORDER_ID})


@pytest.fixture
def order_repositories(fake_repositories: MagicMock) -> MagicMock:
    """CRM order with one quote position, WordPress access with password, no profile access."""
    fake_repositories.crm_order.return_value = CrmOrder(
        id=ORDER_ID,
        number="AU-2026-0003",
        customer_id=uuid4(),
        quote_id=QUOTE_ID,
        title="Erstoptimierung",
        status="open",
    )
    fake_repositories.crm_quote.return_value = CrmQuote.model_validate(
        {
            "id": QUOTE_ID,
            "number": "AN-2026-0012",
            "customer_id": uuid4(),
            "status": "accepted",
            "net": "900",
            "vat": "171",
            "gross": "1071",
        }
    )
    fake_repositories.crm_quote_items.return_value = [
        CrmQuoteItem(
            id=uuid4(),
            quote_id=QUOTE_ID,
            position=1,
            product_id=PRODUCT_ID,
            title="Erstoptimierung Website",
            quantity=1,  # type: ignore[arg-type]
            unit_price_net=900,  # type: ignore[arg-type]
            vat_rate=19,  # type: ignore[arg-type]
        )
    ]
    fake_repositories.crm_products.return_value = [
        CrmProduct(id=PRODUCT_ID, name="Erstoptimierung", radar_areas=["a", "b"])
    ]
    fake_repositories.findings_of_audit.return_value = []
    fake_repositories.customer_access.return_value = access()
    fake_repositories.wordpress_password.return_value = SecretStr(PASSWORD)
    return fake_repositories


def mock_wordpress(plugin_namespace: str | None = "rankmath/v1") -> None:
    namespaces = ["wp/v2"] + ([plugin_namespace] if plugin_namespace else [])
    respx.get(f"{API}/").respond(200, json={"namespaces": namespaces})
    respx.get(f"{API}/wp/v2/pages").respond(200, json=[PAGE_ROW])
    respx.get(f"{API}/wp/v2/posts").respond(200, json=[])


# Targets -----


def test_parse_target_reads_every_form() -> None:
    page = parse_target("wordpress:page:42:meta_description")
    assert (page.system, page.wordpress_type, page.wordpress_id, page.wordpress_field) == (
        System.WORDPRESS,
        WordPressType.PAGE,
        42,
        WordPressField.META_DESCRIPTION,
    )
    assert parse_target("business_profile:phone").profile_field is ProfileField.PHONE
    review = parse_target("business_profile:review:AbC-1_2:reply")
    assert (review.profile_field, review.review_id) == (ProfileField.REVIEW_REPLY, "AbC-1_2")
    assert parse_target("manual:Branchenbuch Gelbe Seiten").label == "Branchenbuch Gelbe Seiten"


@pytest.mark.parametrize(
    "text",
    [
        "wordpress:page:0:title",
        "wordpress:menu:3:title",
        "wordpress:page:3:slug",
        "business_profile:hours",
        "manual:",
        "ftp:server",
    ],
)
def test_parse_target_rejects_unknown_forms(text: str) -> None:
    with pytest.raises(ValueError):
        parse_target(text)


# WordPress client -----


def test_site_root_adds_scheme_and_drops_slash() -> None:
    assert site_root("dachdecker-muster.example/") == "https://dachdecker-muster.example"


@respx.mock
async def test_snapshot_reads_plugin_pages_and_posts_with_basic_auth() -> None:
    mock_wordpress("yoast/v1")
    client = WordPressClient(SITE, "daniel", SecretStr(PASSWORD))

    snapshot = await client.snapshot()

    assert snapshot.seo_plugin == "yoast"
    item = snapshot.items[0]
    assert (item.type, item.id, item.title, item.meta_description) == (
        "page",
        42,
        "Leistungen",
        "Willkommen",
    )
    assert not item.content_truncated
    request = respx.calls[1].request
    assert request.headers["Authorization"].startswith("Basic ")
    assert request.url.params["context"] == "edit"


@respx.mock
async def test_rejected_request_carries_the_wordpress_message() -> None:
    respx.post(f"{API}/wp/v2/pages/42").respond(
        403, json={"code": "rest_cannot_edit", "message": "Sorry, you are not allowed."}
    )
    client = WordPressClient(SITE, "daniel", SecretStr(PASSWORD))

    with pytest.raises(IntegrationError, match="HTTP 403: Sorry, you are not allowed."):
        await client.update("page", 42, {"title": "Neu"})


@respx.mock
async def test_server_errors_are_retried(fast_retries: None) -> None:
    route = respx.post(f"{API}/wp/v2/pages/42").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"id": 42})]
    )
    client = WordPressClient(SITE, "daniel", SecretStr(PASSWORD))

    assert await client.update("page", 42, {"title": "Neu"}) == {"id": 42}
    assert route.call_count == 2


@respx.mock
async def test_update_seo_per_plugin() -> None:
    rank_math = respx.post(f"{API}/rankmath/v1/updateMeta").respond(200, json={"success": True})
    yoast = respx.post(f"{API}/wp/v2/pages/42").mock(
        side_effect=[
            httpx.Response(200, json={"meta": {"_yoast_wpseo_metadesc": "Neu"}}),
            httpx.Response(200, json={"meta": []}),
        ]
    )
    client = WordPressClient(SITE, "daniel", SecretStr(PASSWORD))

    assert await client.update_seo("rank_math", "page", 42, "meta_description", "Neu") is None
    sent = json.loads(rank_math.calls[0].request.content)
    assert sent == {"objectType": "post", "objectID": 42, "meta": {"rank_math_description": "Neu"}}
    assert await client.update_seo("yoast", "page", 42, "meta_description", "Neu") is None
    assert "Yoast" in str(await client.update_seo("yoast", "page", 42, "meta_description", "Neu"))
    assert "kein unterstütztes" in str(
        await client.update_seo(None, "page", 42, "meta_title", "Neu")
    )
    assert yoast.call_count == 2


# Optimize: drafts -----


@respx.mock
async def test_optimize_with_the_fake_cli_stores_validated_drafts(
    order_repositories: MagicMock,
    claude_settings: Settings,
    order_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_wordpress()
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")

    drafts = await optimize_order(order_job, order_repositories, claude_settings)

    assert [draft.target for draft in drafts] == [
        "manual:Branchenbuch",
        "wordpress:page:42:meta_description",
    ]
    assert all(draft.status is OptimizationChangeStatus.DRAFT for draft in drafts)
    assert drafts[1].before == "Willkommen"
    order_repositories.replace_draft_changes.assert_called_once_with(
        ORDER_ID, order_job.audit_id, drafts
    )
    assert order_repositories.new_event.call_args.args[0] == prepare.DRAFTED_EVENT
    folder = claude_settings.radar_work_dir / prepare.OPTIMIZE_DIR / str(order_job.id)
    written = "".join(path.read_text(encoding="utf-8") for path in folder.glob("*.json"))
    assert PASSWORD not in written
    assert json.loads((folder / prepare.ACCESS_FILE).read_text())["wordpress"]["seo_plugin"] == (
        "rank_math"
    )
    assert json.loads((folder / prepare.ORDER_FILE).read_text())["items"][0]["product"][
        "radar_areas"
    ] == ["a", "b"]
    argv = json.loads((claude_settings.analysis_project_dir / "bin" / "argv.json").read_text())
    assert argv[1] == f"/optimieren {folder.resolve().as_posix()}"
    assert any(arg.endswith("aenderungen.json)") for arg in argv)
    assert not any(arg.endswith("findings.json)") for arg in argv)


@respx.mock
async def test_invalid_drafts_fail_the_job_and_store_nothing(
    order_repositories: MagicMock,
    claude_settings: Settings,
    order_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_wordpress()
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "invalid")

    with pytest.raises(OptimizationError, match="entry 2 \\(ftp:server\\)"):
        await optimize_order(order_job, order_repositories, claude_settings)

    order_repositories.replace_draft_changes.assert_not_called()
    assert not (claude_settings.radar_work_dir / prepare.OPTIMIZE_DIR / str(order_job.id)).exists()


async def test_without_wordpress_access_only_manual_drafts_are_possible(
    order_repositories: MagicMock,
    claude_settings: Settings,
    order_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order_repositories.wordpress_password.return_value = None
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")

    drafts = await optimize_order(order_job, order_repositories, claude_settings)

    assert [draft.target for draft in drafts] == ["manual:Branchenbuch"]


async def test_optimize_needs_claude_code_order_and_quote(
    order_repositories: MagicMock, settings: Settings, order_job: Job
) -> None:
    with pytest.raises(OptimizationError, match="no order id"):
        await optimize_order(
            order_job.model_copy(update={"order_id": None}), order_repositories, settings
        )

    order = order_repositories.crm_order.return_value
    order_repositories.crm_order.return_value = order.model_copy(update={"quote_id": None})
    with pytest.raises(OptimizationError, match="has no quote"):
        await optimize_order(order_job, order_repositories, settings, runner=MagicMock())


async def test_failing_claude_code_removes_the_folder(
    order_repositories: MagicMock, settings: Settings, order_job: Job
) -> None:
    order_repositories.wordpress_password.return_value = None
    runner = MagicMock(side_effect=ClaudeCodeError("Not logged in"))
    with pytest.raises(ClaudeCodeError):
        await optimize_order(order_job, order_repositories, settings, runner=runner)

    assert not (settings.radar_work_dir / prepare.OPTIMIZE_DIR / str(order_job.id)).exists()


def write_folder(
    folder: Path,
    changes: list[dict[str, Any]],
    *,
    wordpress: bool = True,
    plugin: str | None = "rank_math",
    truncated: bool = False,
    profile: bool = False,
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "check_results.json").write_text(
        json.dumps(
            [
                {
                    "id": str(uuid4()),
                    "audit_id": str(uuid4()),
                    "check_id": "b3",
                    "area": "b",
                    "outcome": "failed",
                    "measured": {},
                    "weight": "2",
                }
            ]
        )
    )
    summary = {
        "wordpress": {"available": wordpress, "seo_plugin": plugin},
        "business_profile": {"available": profile},
    }
    (folder / prepare.ACCESS_FILE).write_text(json.dumps(summary))
    if wordpress:
        item = {
            "type": "page",
            "id": 42,
            "link": SITE,
            "status": "publish",
            "title": "Leistungen",
            "excerpt": "",
            "content": "x",
            "content_truncated": truncated,
        }
        snapshot = {"url": SITE, "seo_plugin": plugin, "items": [item]}
        (folder / prepare.WORDPRESS_FILE).write_text(json.dumps(snapshot))
    if profile:
        (folder / prepare.REVIEWS_FILE).write_text(json.dumps([{"reviewId": "r1", "rating": 2.0}]))
    (folder / prepare.CHANGES_FILE).write_text(json.dumps({"changes": changes}))


def entry(target: str, after: str = "Neuer Wert", **changes: object) -> dict[str, Any]:
    return {"target": target, "check_id": "b3", "before": "Alt", "after": after, **changes}


@pytest.mark.parametrize(
    ("changes", "options", "message"),
    [
        ([entry("manual:x", check_id="z9")], {}, "unknown check id z9"),
        ([entry("wordpress:page:7:title")], {}, "page 7 is not in wordpress.json"),
        ([entry("wordpress:page:42:content")], {"truncated": True}, "was truncated"),
        ([entry("wordpress:page:42:meta_title")], {"plugin": None}, "no search engine plugin"),
        ([entry("wordpress:page:42:title")], {"wordpress": False}, "WordPress is not reachable"),
        ([entry("business_profile:phone")], {}, "Business Profile is not reachable"),
        (
            [entry("business_profile:review:r9:reply")],
            {"profile": True},
            "review r9 is not in bewertungen.json",
        ),
        ([entry("wordpress:page:42:schema", "{no json")], {}, "not valid JSON"),
        ([entry("wordpress:page:42:schema", "[]")], {}, "one JSON-LD object"),
        ([entry("business_profile:website", "example.de")], {"profile": True}, "https://"),
        ([entry("wordpress:page:42:meta_title", "x" * 71)], {}, "at most 70 allowed"),
        ([entry("manual:x", "Alt")], {}, "after equals before"),
        ([entry("manual:x"), entry("manual:x")], {}, "duplicate target"),
    ],
)
def test_validate_drafts_rejects(
    tmp_path: Path, changes: list[dict[str, Any]], options: dict[str, Any], message: str
) -> None:
    write_folder(tmp_path, changes, **options)

    with pytest.raises(OptimizationError, match=message):
        validate_drafts(tmp_path, ORDER_ID, None)


def test_validate_drafts_accepts_every_reachable_target(tmp_path: Path) -> None:
    schema = json.dumps({"@context": "https://schema.org", "@type": "RoofingContractor"})
    changes = [
        entry("wordpress:page:42:meta_description"),
        entry("wordpress:page:42:content"),
        entry("wordpress:page:42:schema", schema),
        entry("business_profile:description", before=None),
        entry("business_profile:review:r1:reply", before=None),
        entry("manual:Branchenbuch", check_id=None),
    ]
    write_folder(tmp_path, changes, profile=True)

    drafts = validate_drafts(tmp_path, ORDER_ID, None, now=datetime(2026, 10, 23, tzinfo=UTC))

    assert [draft.target for draft in drafts] == [item["target"] for item in changes]
    assert {draft.order_id for draft in drafts} == {ORDER_ID}


def test_validate_drafts_rejects_unknown_keys(tmp_path: Path) -> None:
    write_folder(tmp_path, [entry("manual:x", reason="weil")])

    with pytest.raises(OptimizationError, match="invalid"):
        validate_drafts(tmp_path, ORDER_ID, None)


def test_validate_drafts_needs_the_changes_file(tmp_path: Path) -> None:
    write_folder(tmp_path, [])
    (tmp_path / prepare.CHANGES_FILE).unlink()

    with pytest.raises(OptimizationError, match="aenderungen.json: cannot be read"):
        validate_drafts(tmp_path, ORDER_ID, None)


def test_validate_drafts_turns_an_entry_into_a_draft_row(tmp_path: Path) -> None:
    audit_id = uuid4()
    now = datetime(2026, 10, 23, tzinfo=UTC)
    write_folder(tmp_path, [entry("manual:Branchenbuch", after="  Neuer Eintrag \n")])

    [draft] = validate_drafts(tmp_path, ORDER_ID, audit_id, now=now)

    assert draft.model_dump(exclude={"id"}) == {
        "order_id": ORDER_ID,
        "audit_id": audit_id,
        "check_id": "b3",
        "target": "manual:Branchenbuch",
        "before": "Alt",
        "after": "Neuer Eintrag",
        "status": OptimizationChangeStatus.DRAFT,
        "error": None,
        "created_at": now,
        "decided_at": None,
        "applied_at": None,
    }


async def test_materialise_needs_an_audit(
    order_repositories: MagicMock, settings: Settings, order_job: Job, tmp_path: Path
) -> None:
    job = order_job.model_copy(update={"audit_id": None})

    with pytest.raises(OptimizationError, match="has no audit; audit the customer first"):
        await prepare.materialise(job, ORDER_ID, tmp_path / "job", order_repositories, settings)

    assert not (tmp_path / "job").exists()


# Monthly service -----


def _monthly_raw() -> str:
    from radarauditor.collectors.snapshot import PlaceSnapshot, RawAudit

    def review(day: str, text: str, reply: object = None) -> dict[str, object]:
        return {"publishTime": f"{day}T10:00:00Z", "rating": 4, "text": text, "reviewReply": reply}

    place = PlaceSnapshot(
        place_id="ChIJ-own",
        name="Dachdeckerei Muster",
        formatted_address="Hiltruper Str. 210, 48165 Münster",
        phone="0251 1",
        website=None,
        primary_type=None,
        types=[],
        rating=4.5,
        review_count=12,
        has_opening_hours=False,
        photo_count=3,
        recent_reviews=[
            review("2026-08-20", "Vor der letzten Prüfung"),
            review("2026-09-20", "Neu und offen"),
            review("2026-09-22", "Neu, beantwortet", reply={"text": "Danke"}),
        ],
        business_status="OPERATIONAL",
        post_ages_days=[120],
        value_routes={"recent_reviews": "browser"},
    )
    return RawAudit(
        website=None,
        pagespeed=None,
        place=place,
        competitor_places=[],
        serps=[],
        directories=[],
        assistant_answers=[],
        collected_at=FIXED_TIME,
        warnings=[],
    ).model_dump_json()


@pytest.fixture
def monthly_repositories(order_repositories: MagicMock, sample_audit: Audit) -> MagicMock:
    """A monthly audit of the subscription with reviews read in the browser."""
    order_repositories.audit.return_value = sample_audit.model_copy(
        update={"purpose": AuditPurpose.MONTHLY, "status": AuditStatus.REPORTED}
    )
    order_repositories.previous_audit.return_value = sample_audit.model_copy(
        update={"id": uuid4(), "requested_at": datetime(2026, 9, 1, tzinfo=UTC)}
    )
    order_repositories.wordpress_password.return_value = None

    def download(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.write_text(_monthly_raw(), encoding="utf-8")
        return local_file

    order_repositories.download.side_effect = download
    return order_repositories


def monthly_writer(changes: list[dict[str, Any]]) -> Any:
    def write(folder: Path, settings: Settings) -> None:
        (folder / prepare.CHANGES_FILE).write_text(json.dumps({"changes": changes}))

    return write


async def test_the_monthly_service_hands_new_unanswered_reviews_and_profile_facts(
    monthly_repositories: MagicMock, settings: Settings, order_job: Job
) -> None:
    seen: dict[str, Any] = {}
    changes = [
        entry("manual:Antwort auf die Bewertung vom 20.09.2026", "Vielen Dank!", check_id=None),
        entry("manual:Öffnungszeiten eintragen", "Mo–Fr 7–17 Uhr eintragen.", check_id=None),
    ]

    def write(folder: Path, current: Settings) -> None:
        seen.update(json.loads((folder / prepare.MONTHLY_FILE).read_text(encoding="utf-8")))
        monthly_writer(changes)(folder, current)

    drafts = await optimize_order(order_job, monthly_repositories, settings, runner=write)

    assert seen["neue_bewertungen"] == [
        {"reviewId": None, "datum": "20.09.2026", "sterne": 4, "text": "Neu und offen"}
    ]
    assert seen["antworten_bekannt"] is True
    assert seen["letzte_pruefung"] == "2026-09-01T00:00:00Z"
    assert seen["profil"]["has_opening_hours"] is False
    assert seen["profil"]["post_ages_days"] == [120]
    assert [draft.target for draft in drafts] == [item["target"] for item in changes]
    kind, payload = monthly_repositories.new_event.call_args.args
    assert kind == prepare.DRAFTED_EVENT
    assert payload["purpose"] == "monthly"
    assert payload["audit_id"] == str(order_job.audit_id)


async def test_a_sales_optimize_job_gets_no_monthly_file(
    order_repositories: MagicMock, settings: Settings, order_job: Job
) -> None:
    seen: list[bool] = []

    def write(folder: Path, current: Settings) -> None:
        seen.append((folder / prepare.MONTHLY_FILE).exists())
        monthly_writer([])(folder, current)

    await optimize_order(order_job, order_repositories, settings, runner=write)

    assert seen == [False]
    assert order_repositories.new_event.call_args.args[1]["purpose"] == "sales"


def test_monthly_drafts_never_touch_the_website(tmp_path: Path) -> None:
    write_folder(tmp_path, [entry("wordpress:page:42:title")])
    (tmp_path / prepare.MONTHLY_FILE).write_text("{}")

    with pytest.raises(OptimizationError, match="the monthly service drafts no website changes"):
        validate_drafts(tmp_path, ORDER_ID, None)


# Apply -----


def outcomes(repositories: MagicMock) -> dict[str, tuple[OptimizationChangeStatus, str | None]]:
    by_id = {
        str(call.args[0]): (call.args[1], call.args[2])
        for call in repositories.finish_change.call_args_list
    }
    return by_id


@respx.mock
async def test_apply_writes_wordpress_changes_and_logs_events(
    order_repositories: MagicMock, settings: Settings, apply_job: Job
) -> None:
    title = change("wordpress:page:42:title", "Dachdecker Münster")
    description = change("wordpress:page:42:meta_description", "Beschreibung")
    order_repositories.optimization_changes.return_value = [title, description]
    respx.get(f"{API}/").respond(200, json={"namespaces": ["wp/v2", "rankmath/v1"]})
    page = respx.post(f"{API}/wp/v2/pages/42").respond(200, json={"id": 42})
    meta = respx.post(f"{API}/rankmath/v1/updateMeta").respond(200, json={"success": True})

    counts = await apply_order(apply_job, order_repositories, settings)

    assert counts[OptimizationChangeStatus.APPLIED] == 2
    order_repositories.optimization_changes.assert_called_once_with(
        ORDER_ID, OptimizationChangeStatus.APPROVED
    )
    assert json.loads(page.calls[0].request.content) == {"title": "Dachdecker Münster"}
    assert meta.called
    assert outcomes(order_repositories)[str(title.id)] == (OptimizationChangeStatus.APPLIED, None)
    events = [call.args for call in order_repositories.new_event.call_args_list]
    assert [kind for kind, _ in events] == [apply_module.APPLIED_EVENT] * 2
    assert events[0][1]["target"] == "wordpress:page:42:title"


@respx.mock
async def test_apply_stores_manual_changes_as_todos_and_unreachable_ones_failed(
    order_repositories: MagicMock, settings: Settings, apply_job: Job
) -> None:
    schema = change("wordpress:page:42:schema", '{"@context": "https://schema.org"}')
    manual = change("manual:Branchenbuch", "Eintragen")
    rejected = change("wordpress:post:7:content", "Neuer Text")
    profile = change("business_profile:description", "Beschreibung")
    order_repositories.optimization_changes.return_value = [schema, manual, rejected, profile]
    respx.post(f"{API}/wp/v2/posts/7").respond(
        403, json={"code": "rest_cannot_edit", "message": "Sorry, you are not allowed."}
    )

    counts = await apply_order(apply_job, order_repositories, settings)

    assert counts == {
        OptimizationChangeStatus.APPLIED: 0,
        OptimizationChangeStatus.MANUAL: 2,
        OptimizationChangeStatus.FAILED: 2,
    }
    result = outcomes(order_repositories)
    assert result[str(schema.id)][0] is OptimizationChangeStatus.MANUAL
    assert str(result[str(schema.id)][1]).startswith("Manuell umsetzen: strukturierte Daten")
    assert result[str(manual.id)] == (
        OptimizationChangeStatus.MANUAL,
        "Manuell umsetzen: kein automatischer Weg für dieses Ziel",
    )
    assert result[str(rejected.id)][0] is OptimizationChangeStatus.FAILED
    assert "Sorry, you are not allowed." in str(result[str(rejected.id)][1])
    assert result[str(profile.id)] == (
        OptimizationChangeStatus.FAILED,
        "Kein Zugriff auf das Unternehmensprofil",
    )
    events = {
        call.args[1]["change_id"]: call.args[0]
        for call in order_repositories.new_event.call_args_list
    }
    assert events == {
        str(schema.id): apply_module.MANUAL_EVENT,
        str(manual.id): apply_module.MANUAL_EVENT,
        str(rejected.id): apply_module.FAILED_EVENT,
        str(profile.id): apply_module.FAILED_EVENT,
    }


@respx.mock
async def test_apply_without_search_engine_plugin_leaves_meta_text_to_do_by_hand(
    order_repositories: MagicMock, settings: Settings, apply_job: Job
) -> None:
    description = change("wordpress:page:42:meta_description", "Beschreibung")
    order_repositories.optimization_changes.return_value = [description]
    respx.get(f"{API}/").respond(200, json={"namespaces": ["wp/v2"]})

    counts = await apply_order(apply_job, order_repositories, settings)

    assert counts[OptimizationChangeStatus.MANUAL] == 1
    status, error = outcomes(order_repositories)[str(description.id)]
    assert status is OptimizationChangeStatus.MANUAL
    assert str(error).startswith("Manuell umsetzen: ")


async def test_apply_without_wordpress_access_fails_with_reason(
    order_repositories: MagicMock, settings: Settings, apply_job: Job
) -> None:
    order_repositories.wordpress_password.return_value = None
    title = change("wordpress:page:42:title")
    order_repositories.optimization_changes.return_value = [title]

    await apply_order(apply_job, order_repositories, settings)

    assert outcomes(order_repositories)[str(title.id)] == (
        OptimizationChangeStatus.FAILED,
        "Kein Zugang zu WordPress hinterlegt",
    )


@respx.mock
async def test_apply_changes_the_business_profile_with_oauth_access(
    order_repositories: MagicMock,
    settings: Settings,
    apply_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order_repositories.customer_access.return_value = access(
        google_business_profile_granted=True, google_location_name=LOCATION
    )
    order_repositories.settings_value.side_effect = lambda key: (
        GRANT if key == "google_oauth" else None
    )
    token = AsyncMock(return_value="ya29.token")
    monkeypatch.setattr(apply_module, "access_token", token)
    description = change("business_profile:description", "Meisterbetrieb in Münster.")
    reply = change("business_profile:review:r1:reply", "Danke für Ihre Bewertung!")
    order_repositories.optimization_changes.return_value = [description, reply]
    patch = respx.patch(business_profile.LOCATION_URL.format(location=LOCATION)).respond(
        200, json={}
    )
    respx.get("https://mybusinessaccountmanagement.googleapis.com/v1/accounts").respond(
        200, json={"accounts": [{"name": ACCOUNT}]}
    )
    respx.get(
        f"https://mybusinessbusinessinformation.googleapis.com/v1/{ACCOUNT}/locations"
    ).respond(200, json={"locations": [{"name": LOCATION}]})
    put = respx.put(
        business_profile.REPLY_URL.format(account=ACCOUNT, location=LOCATION, review="r1")
    ).respond(200, json={})

    counts = await apply_order(apply_job, order_repositories, settings)

    assert counts[OptimizationChangeStatus.APPLIED] == 2
    request = patch.calls[0].request
    assert request.url.params["updateMask"] == "profile.description"
    assert json.loads(request.content) == {"profile": {"description": "Meisterbetrieb in Münster."}}
    assert request.headers["Authorization"] == "Bearer ya29.token"
    assert json.loads(put.calls[0].request.content) == {"comment": "Danke für Ihre Bewertung!"}
    token.assert_awaited_once()


async def test_apply_without_order_id_is_an_error(
    order_repositories: MagicMock, settings: Settings, apply_job: Job
) -> None:
    with pytest.raises(OptimizationError, match="no order id"):
        await apply_order(
            apply_job.model_copy(update={"order_id": None}), order_repositories, settings
        )


# Worker -----


async def test_worker_dispatches_optimize_and_apply(
    fake_repositories: MagicMock,
    settings: Settings,
    order_job: Job,
    apply_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optimize = AsyncMock()
    apply = AsyncMock()
    monkeypatch.setattr(prepare, "optimize_order", optimize)
    monkeypatch.setattr(apply_module, "apply_order", apply)

    await worker.run_job(order_job, fake_repositories, settings)
    await worker.run_job(apply_job, fake_repositories, settings)

    optimize.assert_awaited_once_with(order_job, fake_repositories, settings)
    apply.assert_awaited_once_with(apply_job, fake_repositories, settings)


async def test_failed_optimize_job_leaves_the_audit_alone(
    fake_repositories: MagicMock,
    settings: Settings,
    order_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = order_job.model_copy(update={"attempts": worker.MAX_ATTEMPTS})
    monkeypatch.setattr(worker, "run_job", AsyncMock(side_effect=OptimizationError("kaputt")))

    await worker.process_job(job, fake_repositories, settings)

    fake_repositories.set_audit_status.assert_not_called()


def test_claim_order_puts_order_work_after_the_audit_pipeline() -> None:
    assert worker.PRIORITY.index(JobKind.APPLY) > worker.PRIORITY.index(JobKind.REPORT)
    assert worker.PRIORITY.index(JobKind.OPTIMIZE) > worker.PRIORITY.index(JobKind.REPORT)


@respx.mock
async def test_owner_reviews_keep_the_review_id_for_replies() -> None:
    from radarauditor.collectors import access as access_collector

    respx.get(access_collector.ACCOUNTS_URL).respond(200, json={"accounts": [{"name": ACCOUNT}]})
    respx.get(access_collector.LOCATIONS_URL.format(account=ACCOUNT)).respond(
        200, json={"locations": [{"name": LOCATION}]}
    )
    respx.get(access_collector.REVIEWS_URL.format(account=ACCOUNT, location=LOCATION)).respond(
        200, json={"reviews": [{"name": f"{ACCOUNT}/{LOCATION}/reviews/r1", "comment": "Gut"}]}
    )

    reviews = await access_collector.owner_reviews("ya29.token", LOCATION)

    assert reviews[0]["reviewId"] == "r1"
