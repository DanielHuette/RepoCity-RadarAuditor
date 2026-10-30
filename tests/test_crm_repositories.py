"""Repository readers, mail triage and assistant calls of schema crm, optimisation writers.

Context: Supabase client replaced by the recording fake of test_report_repositories. | Input:
Fixtures. | Output: Assertions.
"""

from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import CUSTOMER_ID, FIXED_TIME
from postgrest import APIError
from test_report_repositories import RecordingQuery

from radarauditor.config import Settings
from radarauditor.db import repositories as repositories_module
from radarauditor.db.crm import AssistantRequest, MailTriage
from radarauditor.db.models import OptimizationChange, OptimizationChangeStatus
from radarauditor.db.repositories import Repositories
from radarauditor.errors import StorageError, UnreadableMailError

QUOTE_ID = UUID("00000000-0000-4000-8000-0000000000a1")
ORDER_ID = UUID("00000000-0000-4000-8000-0000000000b1")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setattr(repositories_module, "create_client", MagicMock(return_value=fake))
    return fake


def test_crm_reads_go_to_schema_crm_once(client: MagicMock, settings: Settings) -> None:
    crm = client.schema.return_value
    crm.table.return_value = RecordingQuery(
        [
            {
                "id": str(QUOTE_ID),
                "number": "AN-2026-0012",
                "customer_id": str(uuid4()),
                "status": "sent",
                "net": "100",
                "vat": "19",
                "gross": "119",
                "deal_id": None,
            }
        ]
    )
    repositories = Repositories(settings)

    quote = repositories.crm_quote(QUOTE_ID)
    repositories.crm_quote(QUOTE_ID)

    assert quote.number == "AN-2026-0012"
    client.schema.assert_called_once_with("crm")
    crm.table.assert_called_with("quotes")
    client.table.assert_not_called()


def test_missing_crm_row_is_a_storage_error(client: MagicMock, settings: Settings) -> None:
    client.schema.return_value.table.return_value = RecordingQuery([])

    with pytest.raises(StorageError, match="no row in crm.orders"):
        Repositories(settings).crm_order(ORDER_ID)


def test_quote_items_are_ordered_by_position(client: MagicMock, settings: Settings) -> None:
    query = RecordingQuery([])
    client.schema.return_value.table.return_value = query

    Repositories(settings).crm_quote_items(QUOTE_ID)

    assert ("eq", ("quote_id", str(QUOTE_ID))) in query.calls
    assert ("order", ("position",)) in query.calls


def test_wordpress_password_is_read_only_when_stored(client: MagicMock, settings: Settings) -> None:
    client.table.return_value = RecordingQuery([{"wordpress_app_password_encrypted": "ab cd"}])
    password = Repositories(settings).wordpress_password(CUSTOMER_ID)
    assert password is not None and password.get_secret_value() == "ab cd"

    client.table.return_value = RecordingQuery([{"wordpress_app_password_encrypted": " "}])
    assert Repositories(settings).wordpress_password(CUSTOMER_ID) is None


def test_replace_draft_changes_deletes_only_drafts(client: MagicMock, settings: Settings) -> None:
    delete = RecordingQuery([])
    insert = RecordingQuery([])
    client.table.side_effect = [delete, insert]
    draft = OptimizationChange(
        id=uuid4(),
        order_id=ORDER_ID,
        audit_id=None,
        check_id="b3",
        target="manual:x",
        before=None,
        after="y",
        status=OptimizationChangeStatus.DRAFT,
        error=None,
        created_at=FIXED_TIME,
        decided_at=None,
        applied_at=None,
    )

    Repositories(settings).replace_draft_changes(ORDER_ID, [draft])

    assert ("eq", ("status", "draft")) in delete.calls
    rows = insert.calls[0][1][0]
    assert rows[0]["target"] == "manual:x"
    assert "applied_at" not in rows[0]


def test_finish_change_updates_only_an_approved_change(
    client: MagicMock, settings: Settings
) -> None:
    query = RecordingQuery([])
    client.table.return_value = query
    change_id = uuid4()

    Repositories(settings).finish_change(change_id, OptimizationChangeStatus.APPLIED)

    values = query.calls[0][1][0]
    assert values["status"] == "applied"
    assert "applied_at" in values
    assert ("eq", ("status", "approved")) in query.calls


MAIL_ROW = {
    "id": "00000000-0000-4000-8000-00000000c001",
    "kind": "unknown_sender",
    "status": "claimed",
    "direction": "in",
    "address": "erika@muster.example",
    "name": "Erika Muster",
    "subject": "Anfrage",
    "body_text": "Was kostet das?",
    "folder": "INBOX",
    "raw_path": "triage/x.eml",
    "attempts": 1,
    "claimed_until": "2026-10-24T10:10:00+00:00",
}


def test_mail_triage_is_claimed_through_the_crm_function(
    client: MagicMock, settings: Settings
) -> None:
    query = RecordingQuery([MAIL_ROW])
    client.schema.return_value.rpc.return_value = query

    claimed = Repositories(settings).claim_mail_triage(3, 600)

    client.schema.return_value.rpc.assert_called_once_with(
        "claim_mail_triage", {"p_limit": 3, "p_seconds": 600}
    )
    assert [(row.id, row.kind.value, row.body_text) for row in claimed] == [
        (UUID(MAIL_ROW["id"]), "unknown_sender", "Was kostet das?")
    ]


def test_mail_triage_results_and_failures_go_to_the_crm_functions(
    client: MagicMock, settings: Settings
) -> None:
    rpc = client.schema.return_value.rpc
    rpc.return_value = RecordingQuery([MAIL_ROW])
    repositories = Repositories(settings)
    mail_id = UUID(MAIL_ROW["id"])

    repositories.classify_mail_triage(mail_id, {"verdict": "customer"})
    repositories.fail_mail_triage(mail_id, "Claude Code ist nicht angemeldet")

    assert [call.args for call in rpc.call_args_list] == [
        ("classify_mail_triage", {"p_id": str(mail_id), "p_result": {"verdict": "customer"}}),
        ("fail_mail_triage", {"p_id": str(mail_id), "p_error": "Claude Code ist nicht angemeldet"}),
    ]


def test_claiming_mails_is_never_repeated_after_a_dropped_connection(
    client: MagicMock, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []

    class Dropping(RecordingQuery):
        def execute(self) -> Any:
            attempts.append(1)
            raise httpx.ConnectError("down")

    client.schema.return_value.rpc.return_value = Dropping([])

    with pytest.raises(StorageError, match="claim mails for triage"):
        Repositories(settings).claim_mail_triage(3, 600)
    assert attempts == [1]


def test_a_claimed_row_of_another_shape_is_handed_back_as_unreadable(
    client: MagicMock, settings: Settings
) -> None:
    odd = {**MAIL_ROW, "id": "00000000-0000-4000-8000-00000000c002", "kind": "newsletter"}
    client.schema.return_value.rpc.return_value = RecordingQuery([MAIL_ROW, odd, {"x": 1}])

    claimed = Repositories(settings).claim_mail_triage(3, 600)

    assert isinstance(claimed[0], MailTriage)
    assert isinstance(claimed[1], UnreadableMailError)
    assert claimed[1].mail_id == UUID(odd["id"])
    assert isinstance(claimed[2], UnreadableMailError)
    assert claimed[2].mail_id is None


def test_a_refusal_of_the_crm_keeps_its_own_reason(client: MagicMock, settings: Settings) -> None:
    refusal = APIError({"message": "Angebot nicht gefunden.", "code": "22023"})
    client.schema.return_value.rpc.return_value = RecordingQuery(refusal)

    with pytest.raises(StorageError) as caught:
        Repositories(settings).classify_mail_triage(UUID(MAIL_ROW["id"]), {"intent": "accept"})

    assert caught.value.reason == "Angebot nicht gefunden."


def test_an_error_postgres_raised_itself_has_no_reason_to_show(
    client: MagicMock, settings: Settings
) -> None:
    error = APIError(
        {"message": 'value "10000000000" is out of range for type integer', "code": "22003"}
    )
    client.schema.return_value.rpc.return_value = RecordingQuery(error)

    with pytest.raises(StorageError) as caught:
        Repositories(settings).classify_mail_triage(UUID(MAIL_ROW["id"]), {"intent": "accept"})

    assert caught.value.reason is None


ASSISTANT_ROW = {
    "id": "00000000-0000-4000-8000-00000000d001",
    "text": None,
    "audio_path": "anfragen/3f2b8c1e-0000-4000-8000-000000000001.wav",
    "status": "claimed",
    "attempts": 1,
    "claimed_at": "2026-10-30T10:00:00+00:00",
    "intent": None,
    "result_text": "",
    "created_at": "2026-10-30T09:59:00+00:00",
    "finished_at": None,
}


def test_an_assistant_request_is_claimed_through_the_crm_function(
    client: MagicMock, settings: Settings
) -> None:
    rpc = client.schema.return_value.rpc
    rpc.return_value = RecordingQuery([ASSISTANT_ROW])

    claimed = Repositories(settings).claim_assistant_request()

    rpc.assert_called_once_with("claim_assistant_request", {})
    assert claimed == AssistantRequest(
        id=UUID(ASSISTANT_ROW["id"]), text=None, audio_path=ASSISTANT_ROW["audio_path"], attempts=1
    )


def test_recordings_without_a_request_are_listed_through_the_crm_function(
    client: MagicMock, settings: Settings
) -> None:
    rpc = client.schema.return_value.rpc
    rpc.return_value = RecordingQuery([{"path": "anfragen/a.wav"}, {"path": "anfragen/b.wav"}])

    assert Repositories(settings).orphan_recordings() == ["anfragen/a.wav", "anfragen/b.wav"]
    rpc.assert_called_once_with("orphan_recordings", {})


def test_no_assistant_request_waiting_is_none(client: MagicMock, settings: Settings) -> None:
    client.schema.return_value.rpc.return_value = RecordingQuery([])

    assert Repositories(settings).claim_assistant_request() is None


def test_claiming_an_assistant_request_is_never_repeated(
    client: MagicMock, settings: Settings
) -> None:
    attempts: list[int] = []

    class Dropping(RecordingQuery):
        def execute(self) -> Any:
            attempts.append(1)
            raise httpx.ConnectError("down")

    client.schema.return_value.rpc.return_value = Dropping([])

    with pytest.raises(StorageError, match="claim assistant request"):
        Repositories(settings).claim_assistant_request()
    assert attempts == [1]


def test_assistant_transcript_intent_and_failure_go_to_the_crm_functions(
    client: MagicMock, settings: Settings
) -> None:
    rpc = client.schema.return_value.rpc
    rpc.return_value = RecordingQuery({"ok": True, "text": "Kontakt angelegt: Korn."})
    repositories = Repositories(settings)
    request_id = UUID(ASSISTANT_ROW["id"])
    intent: dict[str, object] = {"action": "create_contact", "name": "Korn"}

    repositories.save_assistant_transcript(request_id, "Leg Korn an")
    result = repositories.apply_assistant_intent(request_id, intent)
    repositories.fail_assistant_request(request_id, "Claude Code ist nicht angemeldet")

    assert (result.ok, result.text) == (True, "Kontakt angelegt: Korn.")
    assert [call.args for call in rpc.call_args_list] == [
        ("save_assistant_transcript", {"p_request": str(request_id), "p_text": "Leg Korn an"}),
        ("apply_assistant_intent", {"p_request": str(request_id), "p_intent": intent}),
        (
            "fail_assistant_request",
            {"p_request": str(request_id), "p_error": "Claude Code ist nicht angemeldet"},
        ),
    ]


def test_an_unexpected_answer_to_an_intent_is_a_storage_error(
    client: MagicMock, settings: Settings
) -> None:
    client.schema.return_value.rpc.return_value = RecordingQuery({"done": 1})

    with pytest.raises(StorageError, match="apply assistant intent"):
        Repositories(settings).apply_assistant_intent(
            UUID(ASSISTANT_ROW["id"]), {"action": "unclear", "question": "Wer?"}
        )


def test_a_claimed_assistant_row_of_another_shape_is_a_storage_error(
    client: MagicMock, settings: Settings
) -> None:
    client.schema.return_value.rpc.return_value = RecordingQuery([{"id": "kaputt"}])

    with pytest.raises(StorageError, match="claim assistant request"):
        Repositories(settings).claim_assistant_request()
