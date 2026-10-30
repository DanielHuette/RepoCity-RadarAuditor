"""Run an optimize job: Claude Code drafts concrete changes for every ordered service.

Context: The app accepts a CRM quote (crm.accept_quote creates the order) and queues an optimize job
with start_optimization(order_id). The worker writes one folder per job with the order, the audit
and what is reachable (WordPress pages and posts, Business Profile reviews), runs /optimieren
headless (skills/radar-optimierer/SKILL.md), validates aenderungen.json and replaces the order's
undecided drafts in optimization_changes. Nothing is applied here; Daniel approves each draft in
the app and the apply job performs it. The folder never holds a password.
Input: Claimed optimize job with order_id and audit_id.
Output: Draft rows, event optimization.drafted.
"""

import asyncio
import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from radarauditor.collectors.access import owner_reviews
from radarauditor.collectors.google_auth import access_token
from radarauditor.config import Settings
from radarauditor.db.crm import CrmOrder, CrmProduct, CrmQuote, CrmQuoteItem
from radarauditor.db.models import (
    CheckResult,
    Finding,
    Job,
    OptimizationChange,
    OptimizationChangeStatus,
)
from radarauditor.db.repositories import Repositories
from radarauditor.errors import CollectionError, IntegrationError, OptimizationError
from radarauditor.integrations.wordpress import SeoPlugin, WordPressSnapshot
from radarauditor.jobs import handoff
from radarauditor.jobs.claude_code import run_claude_code
from radarauditor.optimization.access import profile_location, wordpress_client
from radarauditor.optimization.targets import (
    ProfileField,
    System,
    Target,
    WordPressField,
    parse_target,
)

logger = logging.getLogger(__name__)

OPTIMIZE_DIR = "optimierung"
PROMPT_TEMPLATE = "/optimieren {folder}"
ORDER_FILE = "auftrag.json"
FINDINGS_FILE = "befunde.json"
ACCESS_FILE = "zugang.json"
WORDPRESS_FILE = "wordpress.json"
REVIEWS_FILE = "bewertungen.json"
CHANGES_FILE = "aenderungen.json"
DRAFTED_EVENT = "optimization.drafted"
MAX_CHANGES = 200
MAX_TEXT_CHARS = 60_000
MAX_LENGTHS: dict[WordPressField | ProfileField, int] = {
    WordPressField.TITLE: 120,
    WordPressField.META_TITLE: 70,
    WordPressField.META_DESCRIPTION: 320,
    WordPressField.EXCERPT: 1000,
    ProfileField.NAME: 100,
    ProfileField.DESCRIPTION: 750,
    ProfileField.PHONE: 30,
    ProfileField.WEBSITE: 500,
    ProfileField.REVIEW_REPLY: 4000,
}
SEO_FIELDS = frozenset({WordPressField.META_TITLE, WordPressField.META_DESCRIPTION})

OptimizerRunner = Callable[[Path, Settings], None]

_CHECK_RESULTS_ADAPTER = TypeAdapter(list[CheckResult])
_FINDINGS_ADAPTER = TypeAdapter(list[Finding])
_REVIEWS_ADAPTER: TypeAdapter[list[dict[str, object]]] = TypeAdapter(list[dict[str, object]])


class OrderedItem(BaseModel):
    """One quote position with the product it sells (radar_areas: the catalogue areas it covers)."""

    item: CrmQuoteItem
    product: CrmProduct | None


class OrderInput(BaseModel):
    """Content of auftrag.json."""

    order: CrmOrder
    quote: CrmQuote
    items: list[OrderedItem]


class WordPressAccess(BaseModel):
    """What zugang.json says about WordPress."""

    available: bool
    url: str | None = None
    seo_plugin: SeoPlugin | None = None
    problem: str | None = None


class ProfileAccess(BaseModel):
    """What zugang.json says about the Business Profile."""

    available: bool
    location_name: str | None = None
    problem: str | None = None


class AccessSummary(BaseModel):
    """Content of zugang.json: which systems the drafts may target."""

    wordpress: WordPressAccess
    business_profile: ProfileAccess


class DraftChange(BaseModel):
    """One entry of aenderungen.json."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=300)
    check_id: str | None = None
    before: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    after: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class DraftFile(BaseModel):
    """Content of aenderungen.json."""

    model_config = ConfigDict(extra="forbid")

    changes: list[DraftChange] = Field(max_length=MAX_CHANGES)


def run_optimizer(folder: Path, settings: Settings) -> None:
    """Run /optimieren headless on one folder; Claude Code may write only aenderungen.json.

    Raises:
        ClaudeCodeError: If Claude Code is missing, not logged in, too slow or fails.
    """
    run_claude_code(folder, settings, prompt_template=PROMPT_TEMPLATE, output_files=(CHANGES_FILE,))


def _write_json(target: Path, model: BaseModel) -> None:
    handoff.write_atomic(target, model.model_dump_json(indent=2))


def load_order(order_id: UUID, repositories: Repositories) -> OrderInput:
    """Load the order, its quote, the quote positions and their products.

    Raises:
        OptimizationError: If the order has no quote or the quote has no positions.
        StorageError: If a row cannot be read.
    """
    order = repositories.crm_order(order_id)
    if order.quote_id is None:
        raise OptimizationError(f"order {order.number} has no quote, so no ordered services")
    quote = repositories.crm_quote(order.quote_id)
    items = repositories.crm_quote_items(quote.id)
    if not items:
        raise OptimizationError(f"quote {quote.number} of order {order.number} has no positions")
    product_ids = [item.product_id for item in items if item.product_id is not None]
    products = {product.id: product for product in repositories.crm_products(product_ids)}
    return OrderInput(
        order=order,
        quote=quote,
        items=[
            OrderedItem(
                item=item, product=products.get(item.product_id) if item.product_id else None
            )
            for item in items
        ],
    )


async def _wordpress_part(job: Job, repositories: Repositories, folder: Path) -> WordPressAccess:
    access = repositories.customer_access(job.customer_id)
    client = wordpress_client(access, repositories)
    if client is None:
        return WordPressAccess(available=False, problem="kein WordPress-Zugang hinterlegt")
    try:
        snapshot = await client.snapshot()
    except IntegrationError as exc:
        logger.warning("WordPress of customer %s not readable: %s", job.customer_id, exc)
        return WordPressAccess(available=False, url=client.url, problem=str(exc))
    _write_json(folder / WORDPRESS_FILE, snapshot)
    return WordPressAccess(available=True, url=snapshot.url, seo_plugin=snapshot.seo_plugin)


async def _profile_part(
    job: Job, repositories: Repositories, folder: Path, settings: Settings
) -> ProfileAccess:
    location = profile_location(repositories.customer_access(job.customer_id), repositories)
    if location is None:
        return ProfileAccess(available=False, problem="kein Zugriff auf das Unternehmensprofil")
    try:
        token = await access_token(repositories, settings)
        reviews = await owner_reviews(token, location)
    except CollectionError as exc:
        logger.warning("Business Profile %s not readable: %s", location, exc)
        return ProfileAccess(available=False, location_name=location, problem=str(exc))
    handoff.write_atomic(
        folder / REVIEWS_FILE, _REVIEWS_ADAPTER.dump_json(reviews, indent=2).decode()
    )
    return ProfileAccess(available=True, location_name=location)


async def materialise(
    job: Job, order_id: UUID, folder: Path, repositories: Repositories, settings: Settings
) -> None:
    """Write every input file of one optimize job into its folder.

    Raises:
        OptimizationError: If the job has no audit or the order no ordered services.
        StorageError: If a database read or a download fails.
    """
    if job.audit_id is None:
        raise OptimizationError(f"optimize job {job.id} has no audit; audit the customer first")
    order = load_order(order_id, repositories)
    audit = repositories.audit(job.audit_id)
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(folder / ORDER_FILE, order)
    _write_json(folder / handoff.AUDIT_FILE, handoff.analysis_input(audit, repositories))
    if audit.raw_data_path is not None:
        repositories.download(handoff.RAW_BUCKET, audit.raw_data_path, folder / handoff.RAW_FILE)
    _write_audit_results(audit.id, folder, repositories)
    handoff.write_atomic(
        folder / handoff.CATALOGUE_FILE, handoff.load_catalogue_json(settings.catalogue_path)
    )
    summary = AccessSummary(
        wordpress=await _wordpress_part(job, repositories, folder),
        business_profile=await _profile_part(job, repositories, folder, settings),
    )
    _write_json(folder / ACCESS_FILE, summary)


def _write_audit_results(audit_id: UUID, folder: Path, repositories: Repositories) -> None:
    results = repositories.check_results(audit_id)
    handoff.write_atomic(
        folder / handoff.CHECK_RESULTS_FILE,
        _CHECK_RESULTS_ADAPTER.dump_json(results, indent=2).decode(),
    )
    findings = repositories.findings_of_audit(audit_id)
    handoff.write_atomic(
        folder / FINDINGS_FILE, _FINDINGS_ADAPTER.dump_json(findings, indent=2).decode()
    )


@dataclass(frozen=True)
class _Context:
    check_ids: set[str]
    access: AccessSummary
    wordpress: dict[tuple[str, int], bool]
    review_ids: set[str]


def _read_context(folder: Path) -> _Context:
    try:
        results = _CHECK_RESULTS_ADAPTER.validate_json(
            (folder / handoff.CHECK_RESULTS_FILE).read_text(encoding="utf-8")
        )
        access = AccessSummary.model_validate_json(
            (folder / ACCESS_FILE).read_text(encoding="utf-8")
        )
        wordpress: dict[tuple[str, int], bool] = {}
        if (folder / WORDPRESS_FILE).exists():
            snapshot = WordPressSnapshot.model_validate_json(
                (folder / WORDPRESS_FILE).read_text(encoding="utf-8")
            )
            wordpress = {(item.type, item.id): item.content_truncated for item in snapshot.items}
        review_ids: set[str] = set()
        if (folder / REVIEWS_FILE).exists():
            reviews = _REVIEWS_ADAPTER.validate_json(
                (folder / REVIEWS_FILE).read_text(encoding="utf-8")
            )
            review_ids = {str(review["reviewId"]) for review in reviews if review.get("reviewId")}
    except (OSError, ValidationError) as exc:
        raise OptimizationError(f"{folder}: input files unreadable: {exc}") from exc
    return _Context(
        check_ids={result.check_id for result in results},
        access=access,
        wordpress=wordpress,
        review_ids=review_ids,
    )


def _check_wordpress(target: Target, context: _Context) -> None:
    if not context.access.wordpress.available:
        raise ValueError("WordPress is not reachable; use a manual: target")
    if target.wordpress_type is None or target.wordpress_id is None:
        raise ValueError("WordPress target without type or id")
    key = (target.wordpress_type.value, target.wordpress_id)
    if key not in context.wordpress:
        raise ValueError(f"{key[0]} {key[1]} is not in {WORDPRESS_FILE}")
    if target.wordpress_field is WordPressField.CONTENT and context.wordpress[key]:
        raise ValueError(f"content of {key[0]} {key[1]} was truncated; it cannot be replaced")
    if target.wordpress_field in SEO_FIELDS and context.access.wordpress.seo_plugin is None:
        raise ValueError("no search engine plugin is active; use a manual: target")


def _check_profile(target: Target, context: _Context) -> None:
    if not context.access.business_profile.available:
        raise ValueError("the Business Profile is not reachable; use a manual: target")
    if target.profile_field is ProfileField.REVIEW_REPLY and target.review_id not in (
        context.review_ids
    ):
        raise ValueError(f"review {target.review_id} is not in {REVIEWS_FILE}")


def _check_value(target: Target, after: str) -> None:
    field = target.wordpress_field or target.profile_field
    limit = MAX_LENGTHS.get(field) if field is not None else None
    if limit is not None and len(after) > limit:
        raise ValueError(f"after has {len(after)} characters, at most {limit} allowed")
    if field is ProfileField.WEBSITE and not after.startswith(("https://", "http://")):
        raise ValueError("website must start with https://")
    if field is WordPressField.SCHEMA:
        try:
            markup = json.loads(after)
        except json.JSONDecodeError as exc:
            raise ValueError(f"schema is not valid JSON: {exc.msg}") from exc
        if not isinstance(markup, dict) or "@context" not in markup:
            raise ValueError("schema must be one JSON-LD object with @context")


def _check_draft(draft: DraftChange, context: _Context) -> Target:
    target = parse_target(draft.target)
    if draft.check_id is not None and draft.check_id not in context.check_ids:
        raise ValueError(f"unknown check id {draft.check_id}")
    if not draft.after.strip():
        raise ValueError("after is empty")
    if draft.before is not None and draft.before.strip() == draft.after.strip():
        raise ValueError("after equals before")
    if target.system is System.WORDPRESS:
        _check_wordpress(target, context)
    elif target.system is System.BUSINESS_PROFILE:
        _check_profile(target, context)
    _check_value(target, draft.after.strip())
    return target


def validate_drafts(
    folder: Path, order_id: UUID, audit_id: UUID | None, now: datetime | None = None
) -> list[OptimizationChange]:
    """Check aenderungen.json against the folder's inputs and turn it into draft rows.

    Raises:
        OptimizationError: If the file is missing or invalid, naming the first bad entry.
    """
    context = _read_context(folder)
    path = folder / CHANGES_FILE
    drafts = _read_drafts(path)
    created_at = now or datetime.now(UTC)
    seen: set[str] = set()
    changes: list[OptimizationChange] = []
    for index, draft in enumerate(drafts):
        try:
            _check_draft(draft, context)
            if draft.target in seen:
                raise ValueError("duplicate target")
        except ValueError as exc:
            raise OptimizationError(f"{path}: entry {index} ({draft.target}): {exc}") from exc
        seen.add(draft.target)
        changes.append(_draft_row(draft, order_id, audit_id, created_at))
    return changes


def _read_drafts(path: Path) -> list[DraftChange]:
    try:
        return DraftFile.model_validate_json(path.read_text(encoding="utf-8")).changes
    except OSError as exc:
        raise OptimizationError(f"{path}: cannot be read: {exc}") from exc
    except ValidationError as exc:
        raise OptimizationError(f"{path}: invalid: {exc.errors()[0]['msg']}") from exc


def _draft_row(
    draft: DraftChange, order_id: UUID, audit_id: UUID | None, created_at: datetime
) -> OptimizationChange:
    return OptimizationChange(
        id=uuid4(),
        order_id=order_id,
        audit_id=audit_id,
        check_id=draft.check_id,
        target=draft.target,
        before=draft.before,
        after=draft.after.strip(),
        status=OptimizationChangeStatus.DRAFT,
        error=None,
        created_at=created_at,
        decided_at=None,
        applied_at=None,
    )


async def optimize_order(
    job: Job,
    repositories: Repositories,
    settings: Settings,
    runner: OptimizerRunner = run_optimizer,
) -> list[OptimizationChange]:
    """Prepare, draft and store the changes of one optimize job; the caller marks it done.

    Args:
        job: Claimed optimize job with order_id and audit_id.
        repositories: Database and storage access.
        settings: Work directory, catalogue and Claude Code settings.
        runner: Runs Claude Code on a folder; replaced in tests.

    Returns:
        The stored drafts.

    Raises:
        OptimizationError: If order, audit or the drafts are not usable.
        ClaudeCodeError: If Claude Code fails.
        StorageError: If a read, download or write fails.
    """
    if job.order_id is None:
        raise OptimizationError(f"optimize job {job.id} has no order id")
    folder = settings.radar_work_dir / OPTIMIZE_DIR / str(job.id)
    try:
        await materialise(job, job.order_id, folder, repositories, settings)
        (folder / CHANGES_FILE).unlink(missing_ok=True)
        await asyncio.to_thread(runner, folder, settings)
        changes = validate_drafts(folder, job.order_id, job.audit_id)
    except Exception:
        # A failed attempt is retried from scratch; nothing of it must be reused.
        shutil.rmtree(folder, ignore_errors=True)
        raise
    repositories.replace_draft_changes(job.order_id, changes)
    repositories.new_event(
        DRAFTED_EVENT,
        {
            "order_id": str(job.order_id),
            "job_id": str(job.id),
            "customer_id": str(job.customer_id),
            "drafts": len(changes),
        },
    )
    logger.info("Optimize job %s stored %d drafts for order %s", job.id, len(changes), job.order_id)
    return changes
