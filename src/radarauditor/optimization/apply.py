"""Run an apply job: perform every approved change of an order and record each outcome.

Context: decide_change queues one apply job per order when Daniel approves a draft. WordPress
changes go through the REST API with the stored application password; search engine titles and
descriptions through Rank Math or Yoast when available. Business Profile changes need the
customer's grant and the operator's Google authorisation, otherwise they fail with "Kein Zugriff
auf das Unternehmensprofil". Targets without an automatic way (manual:, JSON-LD markup, no search
engine plugin) are stored as manual with a reason starting "Manuell umsetzen:", so the app lists
them as to-dos for the operator. Every action is logged to events.
Input: Claimed apply job with order_id.
Output: Changes applied, manual or failed with error text, events optimization.applied / .manual /
.failed.
"""

import logging
from dataclasses import dataclass

import httpx

from radarauditor.collectors.google_auth import access_token
from radarauditor.config import Settings
from radarauditor.db.models import (
    CustomerAccess,
    Job,
    OptimizationChange,
    OptimizationChangeStatus,
)
from radarauditor.db.repositories import Repositories
from radarauditor.errors import OptimizationError, RadarError
from radarauditor.integrations import business_profile
from radarauditor.integrations.wordpress import SeoField, SeoPlugin, WordPressClient
from radarauditor.optimization.access import (
    NO_PROFILE_ACCESS,
    NO_WORDPRESS_ACCESS,
    profile_location,
    wordpress_client,
)
from radarauditor.optimization.targets import (
    ProfileField,
    System,
    Target,
    WordPressField,
    parse_target,
)

logger = logging.getLogger(__name__)

APPLIED_EVENT = "optimization.applied"
FAILED_EVENT = "optimization.failed"
MANUAL_EVENT = "optimization.manual"
MANUAL_PREFIX = "Manuell umsetzen"
SCHEMA_MANUAL = "strukturierte Daten (JSON-LD) im Theme oder einem Plugin einbinden"
MANUAL_TARGET = "kein automatischer Weg für dieses Ziel"
ERROR_LIMIT = 500


@dataclass
class _Session:
    """Clients made on first use, so an order without WordPress changes never reads the password."""

    access: CustomerAccess | None
    repositories: Repositories
    settings: Settings
    transport: httpx.AsyncBaseTransport | None
    _wordpress: WordPressClient | None = None
    _wordpress_loaded: bool = False
    _plugin: SeoPlugin | None = None
    _plugin_loaded: bool = False
    _token: str | None = None
    _token_error: str | None = None

    def wordpress(self) -> WordPressClient | None:
        """The customer's WordPress client, or None without stored access."""
        if not self._wordpress_loaded:
            self._wordpress = wordpress_client(self.access, self.repositories, self.transport)
            self._wordpress_loaded = True
        return self._wordpress

    async def seo_plugin(self, client: WordPressClient) -> SeoPlugin | None:
        """The active search engine plugin, read once per job."""
        if not self._plugin_loaded:
            self._plugin = await client.seo_plugin()
            self._plugin_loaded = True
        return self._plugin

    async def google_token(self) -> str:
        """Access token for the Business Profile, or raise with the fixed German reason."""
        if self._token is not None:
            return self._token
        if self._token_error is None:
            if profile_location(self.access, self.repositories) is None:
                self._token_error = NO_PROFILE_ACCESS
            else:
                try:
                    self._token = await access_token(self.repositories, self.settings)
                    return self._token
                except RadarError as exc:
                    self._token_error = f"{NO_PROFILE_ACCESS}: {exc}"
        raise OptimizationError(self._token_error)


class ManualChangeError(Exception):
    """The change has no automatic way; the message says what to do by hand."""


@dataclass(frozen=True)
class ChangeOutcome:
    """How one approved change ended: applied, manual or failed, with the stored error text."""

    status: OptimizationChangeStatus
    error: str | None = None


EVENT_KINDS = {
    OptimizationChangeStatus.APPLIED: APPLIED_EVENT,
    OptimizationChangeStatus.MANUAL: MANUAL_EVENT,
    OptimizationChangeStatus.FAILED: FAILED_EVENT,
}


async def _apply_wordpress(target: Target, value: str, session: _Session) -> None:
    client = session.wordpress()
    if client is None:
        raise OptimizationError(NO_WORDPRESS_ACCESS)
    content_type = target.wordpress_type
    item_id = target.wordpress_id
    wp_field = target.wordpress_field
    if content_type is None or item_id is None or wp_field is None:
        raise OptimizationError("WordPress target without type, id or field")
    if wp_field is WordPressField.SCHEMA:
        raise ManualChangeError(SCHEMA_MANUAL)
    if wp_field in (WordPressField.META_TITLE, WordPressField.META_DESCRIPTION):
        seo_field: SeoField = (
            "meta_title" if wp_field is WordPressField.META_TITLE else "meta_description"
        )
        plugin = await session.seo_plugin(client)
        reason = await client.update_seo(plugin, content_type.value, item_id, seo_field, value)
        if reason is not None:
            raise ManualChangeError(reason)
        return
    await client.update(content_type.value, item_id, {wp_field.value: value})


async def _apply_profile(target: Target, value: str, session: _Session) -> None:
    token = await session.google_token()
    location = session.access.google_location_name if session.access else None
    if location is None or target.profile_field is None:
        raise OptimizationError(NO_PROFILE_ACCESS)
    if target.profile_field is ProfileField.REVIEW_REPLY:
        if target.review_id is None:
            raise OptimizationError("review reply without review id")
        await business_profile.reply_to_review(token, location, target.review_id, value)
        return
    await business_profile.update_field(token, location, target.profile_field, value)


async def apply_change(change: OptimizationChange, session: _Session) -> ChangeOutcome:
    """Perform one approved change.

    Returns:
        Applied without error; manual with "Manuell umsetzen: <what to do>"; failed with the reason.
    """
    try:
        target = parse_target(change.target)
        if target.system is System.MANUAL:
            raise ManualChangeError(MANUAL_TARGET)
        if target.system is System.WORDPRESS:
            await _apply_wordpress(target, change.after, session)
        else:
            await _apply_profile(target, change.after, session)
    except ManualChangeError as manual:
        return ChangeOutcome(
            OptimizationChangeStatus.MANUAL, f"{MANUAL_PREFIX}: {manual}"[:ERROR_LIMIT]
        )
    except (RadarError, ValueError) as exc:
        return ChangeOutcome(OptimizationChangeStatus.FAILED, str(exc)[:ERROR_LIMIT])
    return ChangeOutcome(OptimizationChangeStatus.APPLIED)


def _record(
    change: OptimizationChange, outcome: ChangeOutcome, job: Job, repositories: Repositories
) -> None:
    repositories.finish_change(change.id, outcome.status, outcome.error)
    repositories.new_event(
        EVENT_KINDS[outcome.status],
        {
            "change_id": str(change.id),
            "order_id": str(change.order_id),
            "job_id": str(job.id),
            "customer_id": str(job.customer_id),
            "check_id": change.check_id,
            "target": change.target,
            "after": change.after,
            "error": outcome.error,
        },
    )


async def apply_order(
    job: Job,
    repositories: Repositories,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[OptimizationChangeStatus, int]:
    """Perform every approved change of the job's order; the caller marks the job done.

    A change without an automatic way is stored as manual, one that cannot be performed as
    failed with its reason; the job still succeeds.

    Args:
        job: Claimed apply job with order_id.
        repositories: Database access.
        settings: OAuth client for the Business Profile.
        transport: Replaces the WordPress network in tests.

    Returns:
        Number of changes per outcome.

    Raises:
        OptimizationError: If the job has no order id.
        StorageError: If the changes or the access cannot be read or an outcome cannot be stored.
    """
    if job.order_id is None:
        raise OptimizationError(f"apply job {job.id} has no order id")
    changes = repositories.optimization_changes(job.order_id, OptimizationChangeStatus.APPROVED)
    session = _Session(
        access=repositories.customer_access(job.customer_id),
        repositories=repositories,
        settings=settings,
        transport=transport,
    )
    counts = dict.fromkeys(EVENT_KINDS, 0)
    for change in changes:
        outcome = await apply_change(change, session)
        _record(change, outcome, job, repositories)
        counts[outcome.status] += 1
        if outcome.status is OptimizationChangeStatus.APPLIED:
            logger.info("Change %s applied to %s", change.id, change.target)
        elif outcome.status is OptimizationChangeStatus.MANUAL:
            logger.info("Change %s to %s is left to do by hand", change.id, change.target)
        else:
            logger.warning(
                "Change %s to %s not applied: %s", change.id, change.target, outcome.error
            )
    return counts
