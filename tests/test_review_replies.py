"""Tests for the PDF of reply drafts to unanswered reviews ("lass mich überlegen").

Context: Supabase is a MagicMock, Claude Code a fake runner that writes antworten.json; WeasyPrint
renders a real PDF. | Input: An analysed audit with reviews read in the browser.
| Output: Assertions.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from conftest import AUDIT_ID, CUSTOMER_ID

from radarauditor.collectors.snapshot import PlaceSnapshot, RawAudit
from radarauditor.config import Settings
from radarauditor.db.models import Audit, AuditStatus, Document, DocumentKind
from radarauditor.documents import review_replies
from radarauditor.documents.requests import fulfil_request
from radarauditor.errors import ClaudeCodeError, ClaudeCodeFailure, RenderError

DOCUMENT_ID = UUID("00000000-0000-4000-8000-0000000000d7")
COLLECTED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
Runner = Callable[[Path, Settings], None]


def _review(day: int, text: str, reply: object = None) -> dict[str, object]:
    return {
        "publishTime": f"2026-09-{day:02d}T10:00:00Z",
        "relativePublishTimeDescription": "vor einem Monat",
        "rating": 2 if day == 9 else 5,
        "text": text,
        "reviewReply": reply,
    }


def _raw(reviews: list[dict[str, object]], route: str = "browser") -> RawAudit:
    place = PlaceSnapshot(
        place_id="ChIJ-own",
        name="Dachdeckerei Muster",
        formatted_address="Hiltruper Str. 210, 48165 Münster",
        phone=None,
        website=None,
        primary_type=None,
        types=[],
        rating=4.5,
        review_count=len(reviews),
        has_opening_hours=True,
        photo_count=3,
        recent_reviews=reviews,
        business_status="OPERATIONAL",
        value_routes={"recent_reviews": route},  # type: ignore[dict-item]
    )
    return RawAudit(
        website=None,
        pagespeed=None,
        place=place,
        competitor_places=[],
        serps=[],
        directories=[],
        assistant_answers=[],
        collected_at=COLLECTED,
        warnings=[],
    )


SEVEN_REVIEWS = [
    *(_review(day, f"Bewertung {day}") for day in range(1, 10) if day != 5),
    _review(5, "Schon beantwortet", reply={"text": "Danke!"}),
]


def _request() -> Document:
    return Document(
        id=DOCUMENT_ID,
        customer_id=CUSTOMER_ID,
        kind=DocumentKind.REVIEW_REPLIES,
        number=None,
        issued_on=None,
        net_amount=None,
        vat_rate=None,
        gross_amount=None,
        data={"audit_id": str(AUDIT_ID)},
        file_path=None,
    )


@pytest.fixture
def repositories(fake_repositories: MagicMock, sample_audit: Audit) -> MagicMock:
    fake_repositories.audit.return_value = sample_audit.model_copy(
        update={"status": AuditStatus.REPORTED}
    )
    stored = {"raw": _raw(SEVEN_REVIEWS).model_dump_json()}
    uploaded: dict[str, bytes] = {}

    def download(bucket: str, remote_path: str, local_file: Path) -> Path:
        local_file.parent.mkdir(parents=True, exist_ok=True)
        local_file.write_text(stored[bucket], encoding="utf-8")
        return local_file

    def upload(bucket: str, remote_path: str, local_file: Path) -> str:
        uploaded[f"{bucket}/{remote_path}"] = local_file.read_bytes()
        return remote_path

    fake_repositories.download.side_effect = download
    fake_repositories.upload.side_effect = upload
    fake_repositories.complete_document.side_effect = lambda document: document
    fake_repositories.uploaded = uploaded
    fake_repositories.stored = stored
    return fake_repositories


@pytest.fixture
def replies_settings(report_settings: Settings) -> Settings:
    return report_settings


class FakeWriter:
    """Stands in for Claude Code: records the folder and writes antworten.json."""

    def __init__(self, replies: object | None = None) -> None:
        """Keep the answer to write; None writes one reply per review."""
        self.replies = replies
        self.seen: dict[str, object] = {}

    def __call__(self, folder: Path, settings: Settings) -> None:
        """Read the hand-off files and write antworten.json."""
        reviews = json.loads((folder / "bewertungen.json").read_text(encoding="utf-8"))
        self.seen = {
            "reviews": reviews,
            "business": json.loads((folder / "betrieb.json").read_text(encoding="utf-8")),
        }
        replies = self.replies
        if replies is None:
            replies = {
                "replies": [
                    {"review": item["nummer"], "reply": f"Vielen Dank, Antwort {item['nummer']}."}
                    for item in reviews
                ]
            }
        (folder / "antworten.json").write_text(json.dumps(replies), encoding="utf-8")


def test_five_newest_unanswered_reviews_get_a_reply_in_one_pdf(
    repositories: MagicMock, replies_settings: Settings
) -> None:
    writer = FakeWriter()

    completed = review_replies.build_review_replies(
        _request(), repositories, replies_settings, writer
    )

    reviews = writer.seen["reviews"]
    assert isinstance(reviews, list)
    assert [item["nummer"] for item in reviews] == [1, 2, 3, 4, 5]
    assert [item["text"] for item in reviews] == [f"Bewertung {day}" for day in (9, 8, 7, 6, 4)]
    assert reviews[0]["sterne"] == 2
    assert writer.seen["business"] == {
        "name": "Dachdeckerei Muster",
        "ort": "Münster",
        "branche": "Dachdecker",
    }
    assert completed.file_path is not None
    assert completed.file_path.startswith(f"{CUSTOMER_ID}/{DOCUMENT_ID}/Antwortentwuerfe_")
    assert repositories.uploaded[f"documents/{completed.file_path}"].startswith(b"%PDF")
    snapshot = completed.data
    assert snapshot["audit_id"] == str(AUDIT_ID)
    assert snapshot["source"] == "browser"
    replies = snapshot["replies"]
    assert isinstance(replies, list) and len(replies) == 5
    assert replies[0] == {
        "date": "09.09.2026",
        "stars": 2,
        "review": "Bewertung 9",
        "reply": "Vielen Dank, Antwort 1.",
    }
    kind, payload = repositories.new_event.call_args.args
    assert kind == "document.issued"
    assert payload["kind"] == "review_replies"


def test_the_work_folder_is_removed_afterwards(
    repositories: MagicMock, replies_settings: Settings
) -> None:
    review_replies.build_review_replies(_request(), repositories, replies_settings, FakeWriter())

    assert not (replies_settings.radar_work_dir / "antworten" / str(DOCUMENT_ID)).exists()


def test_the_page_promises_work_and_no_ranking(repositories: MagicMock) -> None:
    html = review_replies.replies_html(
        Path(__file__).resolve().parents[1] / "templates",
        review_replies.ReplyPage(
            customer_name="Dachdeckerei Muster",
            audit_date=COLLECTED,
            items=[
                review_replies.ReplyItem(
                    date="09.09.2026",
                    stars=2,
                    review="Zu spät gekommen.",
                    reply="Das tut uns leid.",
                )
            ],
        ),
    )

    assert "Zu spät gekommen." in html and "Das tut uns leid." in html
    assert "veröffentlicht" in html
    assert "Platzierung bei Google versprechen wir nicht" in html


@pytest.mark.parametrize(
    "answer",
    [
        {"replies": [{"review": 1, "reply": "Nur eine."}]},
        {"replies": [{"review": number, "reply": " "} for number in range(1, 6)]},
        {"replies": [{"review": number, "reply": "x" * 4001} for number in range(1, 6)]},
        {"replies": [{"review": 9, "reply": "Falsch."}]},
        {"antworten": []},
    ],
)
def test_invalid_answers_of_claude_code_are_rejected(
    repositories: MagicMock, replies_settings: Settings, answer: object
) -> None:
    with pytest.raises(RenderError, match="Die Antwortentwürfe waren unvollständig"):
        review_replies.build_review_replies(
            _request(), repositories, replies_settings, FakeWriter(answer)
        )
    repositories.complete_document.assert_not_called()


def test_claude_code_without_login_gives_the_german_reason(
    repositories: MagicMock, replies_settings: Settings
) -> None:
    def failing(folder: Path, settings: Settings) -> None:
        raise ClaudeCodeError("not logged in", ClaudeCodeFailure.NOT_LOGGED_IN)

    with pytest.raises(RenderError, match="^Der Schreibdienst auf dem Server ist nicht angemeldet"):
        review_replies.build_review_replies(_request(), repositories, replies_settings, failing)


def test_reviews_from_the_places_api_are_refused(
    repositories: MagicMock, replies_settings: Settings
) -> None:
    plain = [{"publishTime": "2026-09-01T10:00:00Z", "rating": 2, "text": "Schlecht"}]
    repositories.stored["raw"] = _raw(plain, route="places_api").model_dump_json()

    with pytest.raises(RenderError, match="Ob Bewertungen beantwortet sind"):
        review_replies.build_review_replies(
            _request(), repositories, replies_settings, FakeWriter()
        )


def test_an_audit_where_every_review_is_answered_is_refused(
    repositories: MagicMock, replies_settings: Settings
) -> None:
    answered = [_review(1, "Gut", reply={"text": "Danke"})]
    repositories.stored["raw"] = _raw(answered).model_dump_json()

    with pytest.raises(RenderError, match="Jede gelesene Bewertung ist schon beantwortet"):
        review_replies.build_review_replies(
            _request(), repositories, replies_settings, FakeWriter()
        )


def test_an_audit_not_yet_analysed_is_refused(
    repositories: MagicMock, replies_settings: Settings, sample_audit: Audit
) -> None:
    repositories.audit.return_value = sample_audit

    with pytest.raises(RenderError, match="noch nicht ausgewertet"):
        review_replies.build_review_replies(
            _request(), repositories, replies_settings, FakeWriter()
        )


def test_an_audit_of_another_customer_is_refused(
    repositories: MagicMock, replies_settings: Settings, sample_audit: Audit
) -> None:
    repositories.audit.return_value = sample_audit.model_copy(
        update={"status": AuditStatus.REPORTED, "customer_id": UUID(int=99)}
    )

    with pytest.raises(RenderError, match="gehört zu einem anderen Kunden"):
        review_replies.build_review_replies(
            _request(), repositories, replies_settings, FakeWriter()
        )


def test_a_request_without_audit_is_refused(
    repositories: MagicMock, replies_settings: Settings
) -> None:
    request = _request().model_copy(update={"data": {}})

    with pytest.raises(RenderError, match="Prüfung fehlt"):
        review_replies.build_review_replies(request, repositories, replies_settings, FakeWriter())


def test_document_requests_dispatch_review_replies(
    repositories: MagicMock, replies_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = MagicMock(return_value=_request())
    monkeypatch.setattr("radarauditor.documents.requests.build_review_replies", build)

    fulfil_request(_request(), repositories, replies_settings)

    build.assert_called_once_with(_request(), repositories, replies_settings)
