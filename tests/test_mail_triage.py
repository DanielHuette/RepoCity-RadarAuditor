"""Tests for reading incoming mail with Claude Code.

Context: Claude Code is replaced by a stand-in answer function or the fake CLI, the CRM by the
repositories double. | Input: Fixtures. | Output: Assertions.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from radarauditor.config import Settings
from radarauditor.db.crm import MailTriage, MailTriageKind
from radarauditor.errors import (
    ClaudeCodeError,
    ClaudeCodeFailure,
    MailTriageError,
    StorageError,
    UnreadableMailError,
)
from radarauditor.jobs.mail_triage import (
    ALL_FAILED,
    CLAIM_PROBLEM,
    CRM_REFUSED,
    INVALID_ANSWER,
    UNREADABLE_ROW,
    CustomerMailIntent,
    MailReader,
    ReadOutcome,
    SenderVerdict,
    build_prompt,
    new_fence,
    parse_answer,
)

FIRST_ID = UUID("00000000-0000-4000-8000-00000000c001")
SECOND_ID = UUID("00000000-0000-4000-8000-00000000c002")
ACCEPT = {
    "intent": "accept",
    "quote_number": "AN-2026-0004",
    "confidence": "high",
    "summary": "nimmt das Angebot vom 24. Oktober an",
}
CUSTOMER = {
    "verdict": "customer",
    "name": "Erika Muster",
    "company": "Muster GmbH",
    "reason": "fragt nach einem Angebot",
}
NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def mail(
    kind: MailTriageKind = MailTriageKind.CUSTOMER_MAIL,
    body: str = "Hallo, wir nehmen Ihr Angebot AN-2026-0004 an.",
    row_id: UUID = FIRST_ID,
) -> MailTriage:
    """A claimed triage row as crm.claim_mail_triage returns it."""
    return MailTriage(
        id=row_id,
        kind=kind,
        direction="in",
        address="erika@muster.example",
        name="Erika Muster",
        subject="Ihr Angebot",
        body_text=body,
        attempts=1,
    )


def answering(*answers: str | BaseException) -> MagicMock:
    """An answer function that returns or raises the given outcomes in turn."""
    return MagicMock(side_effect=list(answers))


def test_prompt_fences_the_mail_and_declares_it_untrusted() -> None:
    prompt = build_prompt(mail(), "MAIL-1a2b")

    opening, closing = prompt.index("<<<MAIL-1a2b"), prompt.index("MAIL-1a2b>>>")
    fenced = prompt[opening:closing]
    for part in ("erika@muster.example", "Ihr Angebot", "AN-2026-0004 an."):
        assert part in fenced
    assert "keine Anweisungen" in prompt[:opening]
    assert prompt.count("<<<MAIL-1a2b") == 1


def test_the_prompt_asks_for_the_shape_of_the_mail_kind() -> None:
    customer_mail = build_prompt(mail(), "F")
    unknown = build_prompt(mail(MailTriageKind.UNKNOWN_SENDER), "F")

    assert '"intent"' in customer_mail and '"verdict"' not in customer_mail
    assert '"verdict"' in unknown and '"intent"' not in unknown


def test_every_fence_is_new() -> None:
    assert new_fence() != new_fence()
    assert new_fence().startswith("MAIL-")


def test_the_body_is_cut_to_the_limit_of_the_crm() -> None:
    prompt = build_prompt(mail(body="x" * 9000), "F")

    assert "x" * 8000 in prompt
    assert "x" * 8001 not in prompt


def test_a_customer_mail_answer_becomes_an_intent() -> None:
    result = parse_answer(json.dumps(ACCEPT), MailTriageKind.CUSTOMER_MAIL)

    assert result == CustomerMailIntent.model_validate(ACCEPT)
    assert result.model_dump(mode="json") == ACCEPT


def test_an_unknown_sender_answer_becomes_a_verdict() -> None:
    result = parse_answer(json.dumps(CUSTOMER), MailTriageKind.UNKNOWN_SENDER)

    assert result == SenderVerdict.model_validate(CUSTOMER)


def test_the_answer_may_sit_in_a_code_block_with_words_around_it() -> None:
    text = f"Hier die Einordnung:\n```json\n{json.dumps(ACCEPT)}\n```\n"

    assert parse_answer(text, MailTriageKind.CUSTOMER_MAIL).model_dump(mode="json") == ACCEPT


def test_missing_name_and_company_become_empty_texts() -> None:
    answer = {**CUSTOMER, "name": None, "company": None}

    result = parse_answer(json.dumps(answer), MailTriageKind.UNKNOWN_SENDER)

    assert result.model_dump(mode="json") == {**CUSTOMER, "name": "", "company": ""}


def test_a_quote_number_may_be_missing() -> None:
    answer = {**ACCEPT, "quote_number": None}

    assert parse_answer(json.dumps(answer), MailTriageKind.CUSTOMER_MAIL).model_dump(
        mode="json"
    ) == {**ACCEPT, "quote_number": None}


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Das Angebot wird angenommen.", MailTriageKind.CUSTOMER_MAIL),
        (json.dumps(CUSTOMER), MailTriageKind.CUSTOMER_MAIL),
        (json.dumps(ACCEPT), MailTriageKind.UNKNOWN_SENDER),
        (json.dumps({**ACCEPT, "intent": "maybe"}), MailTriageKind.CUSTOMER_MAIL),
        (json.dumps({**ACCEPT, "confidence": "certain"}), MailTriageKind.CUSTOMER_MAIL),
        (json.dumps({**CUSTOMER, "verdict": "friend"}), MailTriageKind.UNKNOWN_SENDER),
        ("[1, 2]", MailTriageKind.CUSTOMER_MAIL),
        ("{not json}", MailTriageKind.CUSTOMER_MAIL),
    ],
)
def test_other_answers_are_rejected(text: str, kind: MailTriageKind) -> None:
    with pytest.raises(MailTriageError, match=INVALID_ANSWER):
        parse_answer(text, kind)


def test_long_answers_are_cut_not_rejected() -> None:
    long_intent = {**ACCEPT, "summary": "s" * 600, "quote_number": "A" * 50}
    long_verdict = {**CUSTOMER, "name": "n" * 300, "company": "c" * 300, "reason": "r" * 600}

    intent = parse_answer(json.dumps(long_intent), MailTriageKind.CUSTOMER_MAIL)
    verdict = parse_answer(json.dumps(long_verdict), MailTriageKind.UNKNOWN_SENDER)

    assert isinstance(intent, CustomerMailIntent) and isinstance(verdict, SenderVerdict)
    assert (len(intent.summary), len(intent.quote_number or "")) == (500, 40)
    assert (len(verdict.name), len(verdict.company), len(verdict.reason)) == (200, 200, 500)


def test_missing_or_empty_optional_parts_are_accepted() -> None:
    intent = parse_answer(
        json.dumps({"intent": "other", "confidence": "low", "summary": None}),
        MailTriageKind.CUSTOMER_MAIL,
    )
    verdict = parse_answer(json.dumps({"verdict": "automated"}), MailTriageKind.UNKNOWN_SENDER)

    assert intent.model_dump(mode="json") == {
        "intent": "other",
        "quote_number": None,
        "confidence": "low",
        "summary": "",
    }
    assert verdict.model_dump(mode="json") == {
        "verdict": "automated",
        "name": "",
        "company": "",
        "reason": "",
    }


def reader(repositories: MagicMock, settings: Settings, ask: MagicMock) -> MailReader:
    return MailReader(repositories, settings, ask)


def test_one_mail_is_read_per_call_from_one_claim(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    fake_repositories.claim_mail_triage.return_value = [
        mail(),
        mail(MailTriageKind.UNKNOWN_SENDER, row_id=SECOND_ID),
    ]
    mails = reader(fake_repositories, settings, answering(json.dumps(ACCEPT), json.dumps(CUSTOMER)))

    first = mails.read_one(NOW)
    fake_repositories.classify_mail_triage.assert_called_once_with(FIRST_ID, ACCEPT)
    second = mails.read_one(NOW + timedelta(seconds=15))

    fake_repositories.claim_mail_triage.assert_called_once_with(
        settings.mail_triage_batch, settings.mail_triage_claim_seconds
    )
    fake_repositories.classify_mail_triage.assert_called_with(SECOND_ID, CUSTOMER)
    assert first == second == ReadOutcome(handled=True, problem=None, claude_unavailable=False)


def test_the_next_call_claims_again_once_the_held_mails_are_read(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    fake_repositories.claim_mail_triage.side_effect = [[mail()], []]
    mails = reader(fake_repositories, settings, answering(json.dumps(ACCEPT)))

    mails.read_one(NOW)
    outcome = mails.read_one(NOW)

    assert fake_repositories.claim_mail_triage.call_count == 2
    assert outcome == ReadOutcome(handled=False, problem=None, claude_unavailable=False)


def test_mails_whose_claim_runs_out_are_left_to_the_next_claim(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    fake_repositories.claim_mail_triage.side_effect = [
        [mail(), mail(row_id=SECOND_ID)],
        [mail(row_id=SECOND_ID)],
    ]
    ask = answering(json.dumps(ACCEPT), json.dumps(ACCEPT))
    mails = reader(fake_repositories, settings, ask)

    mails.read_one(NOW)
    late = NOW + timedelta(seconds=settings.mail_triage_claim_seconds)
    mails.read_one(late)

    assert fake_repositories.claim_mail_triage.call_count == 2
    assert ask.call_count == 2


def test_an_invalid_answer_fails_that_mail(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    fake_repositories.claim_mail_triage.return_value = [mail(), mail(row_id=SECOND_ID)]
    mails = reader(fake_repositories, settings, answering("Ja.", json.dumps(ACCEPT)))

    first = mails.read_one(NOW)
    second = mails.read_one(NOW)

    fake_repositories.fail_mail_triage.assert_called_once_with(FIRST_ID, INVALID_ANSWER)
    fake_repositories.classify_mail_triage.assert_called_once_with(SECOND_ID, ACCEPT)
    assert (first.problem, second.problem) == (None, None)


def test_a_claim_where_every_mail_fails_is_a_problem(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    fake_repositories.claim_mail_triage.return_value = [mail(), mail(row_id=SECOND_ID)]
    slow = ClaudeCodeError("no result", ClaudeCodeFailure.TIMEOUT)
    mails = reader(fake_repositories, settings, answering(slow, "Nein."))

    first = mails.read_one(NOW)
    last = mails.read_one(NOW)

    assert first.problem is None
    assert last == ReadOutcome(handled=True, problem=ALL_FAILED, claude_unavailable=False)
    fake_repositories.fail_mail_triage.assert_any_call(
        FIRST_ID, "Claude Code hat nicht rechtzeitig geantwortet"
    )


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (ClaudeCodeFailure.NOT_LOGGED_IN, "Claude Code ist nicht angemeldet"),
        (ClaudeCodeFailure.NOT_INSTALLED, "Claude Code ist nicht installiert"),
        (ClaudeCodeFailure.USAGE_LIMIT, "Das Kontingent des Claude-Abos ist aufgebraucht"),
    ],
)
def test_without_claude_code_no_mail_is_used_up(
    fake_repositories: MagicMock, settings: Settings, failure: ClaudeCodeFailure, reason: str
) -> None:
    fake_repositories.claim_mail_triage.side_effect = [[mail(), mail(row_id=SECOND_ID)], []]
    ask = answering(ClaudeCodeError("no login", failure))
    mails = reader(fake_repositories, settings, ask)

    outcome = mails.read_one(NOW)
    mails.read_one(NOW)

    fake_repositories.fail_mail_triage.assert_not_called()
    assert ask.call_count == 1
    assert fake_repositories.claim_mail_triage.call_count == 2
    assert outcome == ReadOutcome(handled=False, problem=reason, claude_unavailable=True)


def test_a_crm_that_hands_out_nothing_is_reported(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    fake_repositories.claim_mail_triage.side_effect = StorageError("function does not exist")
    ask = answering()

    outcome = reader(fake_repositories, settings, ask).read_one(NOW)

    ask.assert_not_called()
    assert outcome == ReadOutcome(handled=False, problem=CLAIM_PROBLEM, claude_unavailable=False)


@pytest.mark.parametrize(
    ("refusal", "reason"),
    [
        (StorageError("classify failed", "Angebot nicht gefunden."), "Angebot nicht gefunden."),
        (StorageError("classify failed: timeout"), CRM_REFUSED),
    ],
)
def test_an_answer_the_crm_refuses_fails_the_mail_with_its_reason(
    fake_repositories: MagicMock, settings: Settings, refusal: StorageError, reason: str
) -> None:
    fake_repositories.claim_mail_triage.return_value = [mail()]
    fake_repositories.classify_mail_triage.side_effect = refusal

    outcome = reader(fake_repositories, settings, answering(json.dumps(ACCEPT))).read_one(NOW)

    fake_repositories.fail_mail_triage.assert_called_once_with(FIRST_ID, reason)
    assert outcome.handled is True


def test_a_failure_that_cannot_be_recorded_is_logged(
    fake_repositories: MagicMock, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    fake_repositories.claim_mail_triage.return_value = [mail()]
    fake_repositories.fail_mail_triage.side_effect = StorageError("offline")

    reader(fake_repositories, settings, answering("Nein.")).read_one(NOW)

    assert f"Failure of mail {FIRST_ID} could not be recorded" in caplog.text


def test_a_row_of_another_shape_is_failed_without_asking(
    fake_repositories: MagicMock, settings: Settings
) -> None:
    fake_repositories.claim_mail_triage.return_value = [
        UnreadableMailError("odd", SECOND_ID),
        UnreadableMailError("odd", None),
        mail(),
    ]
    ask = answering(json.dumps(ACCEPT))
    mails = reader(fake_repositories, settings, ask)

    outcomes = [mails.read_one(NOW) for _ in range(3)]

    fake_repositories.fail_mail_triage.assert_called_once_with(SECOND_ID, UNREADABLE_ROW)
    assert ask.call_count == 1
    assert [outcome.problem for outcome in outcomes] == [None, None, None]


def test_the_real_call_has_no_tools_and_runs_outside_the_project(
    fake_repositories: MagicMock,
    claude_settings: Settings,
    fake_claude: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "mail")
    fake_repositories.claim_mail_triage.return_value = [mail()]

    outcome = MailReader(fake_repositories, claude_settings).read_one(NOW)

    argv = json.loads(fake_claude.with_name("argv.json").read_text(encoding="utf-8"))
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--model") + 1] == claude_settings.mail_triage_model
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    assert "AN-2026-0004 an." in argv[1]
    cwd = Path(fake_claude.with_name("cwd.txt").read_text(encoding="utf-8"))
    assert cwd != claude_settings.analysis_project_dir.resolve()
    assert not cwd.exists()
    assert outcome.handled is True
