"""Reply drafts to real unanswered reviews, as a PDF for a business that wants to think it over.

Context: The Auswertung of the app inserts a documents row of kind review_replies with data
{audit_id} and file_path null; the worker completes it while idle like every document request. The
reviews come from the raw data of the audit, only those whose missing owner reply is known (read on
Google Maps in the browser or with access to the profile), at most five, reviews with text first,
newest first. Claude Code writes the replies with the subscription through the same headless path
as the analysis (/antworten, may write only antworten.json); the worker checks one reply per review
and renders templates/report/review_replies.html. Nothing is posted on Google.
Input: Requested review_replies Document row.
Output: The completed row with the PDF in bucket documents and the replies in its data, event
document.issued.
"""

import json
import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from radarauditor.collectors.snapshot import RawAudit
from radarauditor.config import Settings
from radarauditor.db.models import Audit, AuditStatus, Customer, Document, DocumentKind
from radarauditor.db.repositories import Repositories
from radarauditor.documents.builder import DOCUMENT_ISSUED_EVENT, DOCUMENTS_BUCKET
from radarauditor.errors import ClaudeCodeError, ClaudeCodeFailure, ConfigurationError, RenderError
from radarauditor.industries import load_industries
from radarauditor.jobs import handoff
from radarauditor.jobs.claude_code import run_claude_code
from radarauditor.rendering import filename_slug, render_template, write_pdf
from radarauditor.reporting.report import REPORT_TEMPLATE_DIR
from radarauditor.reviews import OpenReview, UnansweredReviews, unanswered_reviews

logger = logging.getLogger(__name__)

REPLIES_DIR = "antworten"
PROMPT_TEMPLATE = "/antworten {folder}"
REVIEWS_FILE = "bewertungen.json"
BUSINESS_FILE = "betrieb.json"
ANSWERS_FILE = "antworten.json"
TEMPLATE = "review_replies.html"
RAW_BUCKET = "raw"
MAX_REVIEWS = 5
MAX_REPLY_CHARS = 4000
ANALYSED_STATUSES = frozenset({AuditStatus.ANALYSED, AuditStatus.REPORTED})

MISSING_AUDIT = "Die Prüfung fehlt in der Anforderung."
NOT_ANALYSED = "Die Prüfung ist noch nicht ausgewertet."
OTHER_CUSTOMER = "Die Prüfung gehört zu einem anderen Kunden."
NO_RAW_DATA = "Zu dieser Prüfung gibt es keine Rohdaten."
NO_REPLY_DATA = (
    "Ob Bewertungen beantwortet sind, zeigt nur Google Maps im Browser oder der Zugang zum Profil; "
    "diese Prüfung hat die Bewertungen über die Google-Schnittstelle gelesen. Bitte neu prüfen."
)
ALL_ANSWERED = "Jede gelesene Bewertung ist schon beantwortet."
INVALID_ANSWERS = "Die Antwortentwürfe waren unvollständig"
# The app shows these; it never names the tool that writes the drafts.
WRITER_REASONS = {
    ClaudeCodeFailure.NOT_INSTALLED: "Der Schreibdienst ist auf dem Server nicht eingerichtet.",
    ClaudeCodeFailure.NOT_LOGGED_IN: "Der Schreibdienst auf dem Server ist nicht angemeldet.",
    ClaudeCodeFailure.USAGE_LIMIT: "Das Kontingent des Schreibdienstes ist aufgebraucht.",
    ClaudeCodeFailure.TIMEOUT: "Der Schreibdienst hat nicht rechtzeitig geantwortet.",
    ClaudeCodeFailure.FAILED: "Der Schreibdienst hat mit einem Fehler geantwortet.",
}

ReplyWriter = Callable[[Path, Settings], None]


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review: int
    reply: str = Field(max_length=MAX_REPLY_CHARS)


class _Answers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replies: list[_Reply]


@dataclass(frozen=True)
class ReplyItem:
    """One review with its reply draft, as printed and stored in the data snapshot."""

    date: str
    stars: float | None
    review: str
    reply: str


@dataclass(frozen=True)
class ReplyPage:
    """Everything the template prints."""

    customer_name: str
    audit_date: datetime
    items: list[ReplyItem]


def write_replies(folder: Path, settings: Settings) -> None:
    """Run /antworten headless on one folder; Claude Code may write only antworten.json.

    Raises:
        ClaudeCodeError: If Claude Code is missing, not logged in, too slow or fails.
    """
    run_claude_code(folder, settings, prompt_template=PROMPT_TEMPLATE, output_files=(ANSWERS_FILE,))


def replies_html(templates_dir: Path, page: ReplyPage) -> str:
    """Render the reply page as HTML.

    Raises:
        RenderError: If the template fails.
    """
    return render_template(templates_dir / REPORT_TEMPLATE_DIR, TEMPLATE, {"report": page})


def build_review_replies(
    document: Document,
    repositories: Repositories,
    settings: Settings,
    writer: ReplyWriter = write_replies,
) -> Document:
    """Write reply drafts to the newest unanswered reviews of an audit and complete the request.

    Args:
        document: Requested review_replies row with data {audit_id}.
        repositories: Database and storage access.
        settings: Work directory, templates, industries and Claude Code settings.
        writer: Lets Claude Code write antworten.json in a folder; replaced in tests.

    Returns:
        The completed row.

    Raises:
        RenderError: With a German reason if audit, reviews, Claude Code or rendering fail.
        StorageError: If loading, uploading or updating fails.
    """
    audit = _analysed_audit(document, repositories)
    customer = repositories.customer(document.customer_id)
    picked = _open_reviews(audit, repositories)
    reviews = picked.reviews
    folder = settings.radar_work_dir / REPLIES_DIR / str(document.id)
    try:
        _prepare(folder, customer, reviews, settings)
        _run_writer(writer, folder, settings)
        items = _items(reviews, _read_answers(folder / ANSWERS_FILE, len(reviews)))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    page = ReplyPage(customer_name=customer.name, audit_date=audit.requested_at, items=items)
    remote_path = _render_and_upload(document, customer, page, repositories, settings)
    completed = repositories.complete_document(
        document.model_copy(
            update={
                "issued_on": datetime.now(UTC).date(),
                "data": {
                    "audit_id": str(audit.id),
                    "source": picked.source,
                    "replies": [asdict(item) for item in items],
                },
                "file_path": remote_path,
            }
        )
    )
    repositories.new_event(
        DOCUMENT_ISSUED_EVENT,
        {
            "document_id": str(completed.id),
            "customer_id": str(customer.id),
            "kind": DocumentKind.REVIEW_REPLIES.value,
            "audit_id": str(audit.id),
            "file_path": remote_path,
        },
    )
    logger.info("Issued %d review reply drafts for customer %s", len(items), customer.id)
    return completed


def _analysed_audit(document: Document, repositories: Repositories) -> Audit:
    try:
        audit_id = UUID(str(document.data.get("audit_id")))
    except ValueError as exc:
        raise RenderError(MISSING_AUDIT) from exc
    audit = repositories.audit(audit_id)
    if audit.customer_id != document.customer_id:
        raise RenderError(OTHER_CUSTOMER)
    if audit.status not in ANALYSED_STATUSES:
        raise RenderError(NOT_ANALYSED)
    return audit


def _load_raw(audit: Audit, repositories: Repositories) -> RawAudit:
    if audit.raw_data_path is None:
        raise RenderError(NO_RAW_DATA)
    with tempfile.TemporaryDirectory(prefix="radar-replies-") as scratch:
        local = repositories.download(RAW_BUCKET, audit.raw_data_path, Path(scratch) / "raw.json")
        return RawAudit.model_validate_json(local.read_text(encoding="utf-8"))


def _open_reviews(audit: Audit, repositories: Repositories) -> UnansweredReviews:
    picked = unanswered_reviews(_load_raw(audit, repositories), limit=MAX_REVIEWS)
    if not picked.reply_data_available:
        raise RenderError(NO_REPLY_DATA)
    if not picked.reviews:
        raise RenderError(ALL_ANSWERED)
    return picked


def _industry(customer: Customer, settings: Settings) -> str:
    try:
        catalogue = load_industries(settings.industries_path)
    except ConfigurationError:
        return customer.industry
    group = next((item for item in catalogue.groups if item.id == customer.industry), None)
    return group.label if group is not None else customer.industry


def _prepare(
    folder: Path, customer: Customer, reviews: list[OpenReview], settings: Settings
) -> None:
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    entries = [
        {"nummer": number, "datum": review.date_text, "sterne": review.stars, "text": review.text}
        for number, review in enumerate(reviews, 1)
    ]
    business = {
        "name": customer.name,
        "ort": customer.city,
        "branche": _industry(customer, settings),
    }
    handoff.write_atomic(folder / REVIEWS_FILE, json.dumps(entries, ensure_ascii=False, indent=2))
    handoff.write_atomic(folder / BUSINESS_FILE, json.dumps(business, ensure_ascii=False, indent=2))


def _run_writer(writer: ReplyWriter, folder: Path, settings: Settings) -> None:
    try:
        writer(folder, settings)
    except ClaudeCodeError as exc:
        raise RenderError(WRITER_REASONS[exc.failure]) from exc


def _read_answers(path: Path, count: int) -> dict[int, str]:
    try:
        answers = _Answers.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise RenderError(f"{INVALID_ANSWERS}: {exc}") from exc
    replies = {item.review: item.reply.strip() for item in answers.replies}
    if sorted(replies) != list(range(1, count + 1)) or len(answers.replies) != count:
        raise RenderError(f"{INVALID_ANSWERS}: genau eine Antwort je Bewertung 1 bis {count}")
    if not all(replies.values()):
        raise RenderError(f"{INVALID_ANSWERS}: eine Antwort ist leer")
    return replies


def _items(reviews: list[OpenReview], replies: dict[int, str]) -> list[ReplyItem]:
    return [
        ReplyItem(
            date=review.date_text,
            stars=int(review.stars)
            if review.stars is not None and review.stars.is_integer()
            else review.stars,
            review=review.text,
            reply=replies[number],
        )
        for number, review in enumerate(reviews, 1)
    ]


def _render_and_upload(
    document: Document,
    customer: Customer,
    page: ReplyPage,
    repositories: Repositories,
    settings: Settings,
) -> str:
    file_name = f"Antwortentwuerfe_{filename_slug(customer.name)}_{page.audit_date:%Y-%m-%d}.pdf"
    with tempfile.TemporaryDirectory(prefix="radar-replies-pdf-") as scratch:
        output = write_pdf(replies_html(settings.templates_dir, page), Path(scratch) / file_name)
        return repositories.upload(
            DOCUMENTS_BUCKET, f"{customer.id}/{document.id}/{file_name}", output
        )
