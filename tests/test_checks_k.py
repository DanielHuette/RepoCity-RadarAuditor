"""Tests for the checks of area k (assistenten).

Context: Pure rule tests on RawAudit fixtures. | Input: Fixtures. | Output: Assertions.
"""

from collections.abc import Callable

import pytest

from radarauditor.collectors.snapshot import AnswerRoute, AssistantAnswer, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.k_assistenten import (
    answer_routes,
    is_recommendation_question,
    run,
)

THRESHOLDS: dict[str, dict[str, float | int | str]] = {
    "k1": {"min_rate": 0.3},
    "k2": {"min_rate": 0.1},
    "k3": {},
    "k4": {},
    "k5": {},
}
RECOMMENDATION = "Welchen Dachdecker in Münster empfiehlst du?"
PROBLEM = "Mein Dach ist undicht, was tun?"
Edit = Callable[..., RawAudit]


def _answer(
    question: str,
    mentioned: bool,
    provider: str = "openai",
    excerpt: str = "",
    sources: list[str] | None = None,
) -> AssistantAnswer:
    return AssistantAnswer(
        provider=provider,
        question=question,
        mentioned=mentioned,
        cited_sources=sources or [],
        answer_excerpt=excerpt,
    )


def _with_answers(raw: RawAudit, answers: list[AssistantAnswer]) -> RawAudit:
    return raw.model_copy(update={"assistant_answers": answers})


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_full_audit_passes(raw_audit_full: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_full, THRESHOLDS[check_id])[0] is CheckOutcome.PASSED


@pytest.mark.parametrize("check_id", list(THRESHOLDS))
def test_minimal_audit_not_measurable(raw_audit_minimal: RawAudit, check_id: str) -> None:
    assert run(check_id, raw_audit_minimal, THRESHOLDS[check_id])[0] is (
        CheckOutcome.NOT_MEASURABLE
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (RECOMMENDATION, True),
        ("Wer ist ein guter Dachdecker in Münster?", True),
        ("Nenne die Top 3 Dachdecker in Münster", True),
        (PROBLEM, False),
    ],
)
def test_question_classification(question: str, expected: bool) -> None:
    assert is_recommendation_question(question) is expected


def test_k1_rate_per_provider(raw_audit_full: RawAudit) -> None:
    answers = [
        _answer(RECOMMENDATION, mentioned=index < 2, provider="openai") for index in range(5)
    ] + [_answer(RECOMMENDATION, mentioned=False, provider="gemini") for _ in range(5)]
    answers.append(_answer(PROBLEM, mentioned=True))
    outcome, measured = run("k1", _with_answers(raw_audit_full, answers), THRESHOLDS["k1"])
    assert outcome is CheckOutcome.FAILED
    assert measured["mention_rate"] == 0.2
    assert measured["answers"] == 10
    assert measured["by_provider"] == {"openai": 0.4, "gemini": 0.0}


def test_k1_within_tolerance_is_partial(raw_audit_full: RawAudit) -> None:
    answers = [_answer(RECOMMENDATION, mentioned=index < 6) for index in range(25)]
    outcome, measured = run("k1", _with_answers(raw_audit_full, answers), THRESHOLDS["k1"])
    assert measured["mention_rate"] == 0.24
    assert outcome is CheckOutcome.PARTIAL


def test_k2_problem_questions_only(raw_audit_full: RawAudit) -> None:
    answers = [_answer(RECOMMENDATION, mentioned=True), _answer(PROBLEM, mentioned=False)]
    outcome, measured = run("k2", _with_answers(raw_audit_full, answers), THRESHOLDS["k2"])
    assert outcome is CheckOutcome.FAILED
    assert measured["answers"] == 1
    only_recommendations = _with_answers(raw_audit_full, answers[:1])
    assert run("k2", only_recommendations, THRESHOLDS["k2"])[1] == {"reason": "no_problem_answers"}


def test_k3_wrong_phone(raw_audit_full: RawAudit) -> None:
    answers = [
        _answer(RECOMMENDATION, True, excerpt="Muster, Telefon 0251 999999."),
        _answer(RECOMMENDATION, True, excerpt="Muster, Telefon +49 251 123456, 48143 Münster."),
        _answer(RECOMMENDATION, True, excerpt="Muster ist gut."),
        _answer(RECOMMENDATION, False, excerpt="Schmidt, Telefon 0251 555555."),
    ]
    outcome, measured = run("k3", _with_answers(raw_audit_full, answers), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["answers_checked"] == 2
    assert measured["answers_with_wrong_facts"] == 1
    unchecked = _with_answers(raw_audit_full, answers[2:])
    assert run("k3", unchecked, {})[1] == {"reason": "no_checkable_answers"}


def test_k4_missing_on_cited_hosts(raw_audit_full: RawAudit) -> None:
    answers = [
        _answer(RECOMMENDATION, True, sources=["https://www.yelp.de/x", "https://www.yelp.de/y"]),
        _answer(RECOMMENDATION, True, sources=["https://www.gelbeseiten.de/z"]),
    ]
    outcome, measured = run("k4", _with_answers(raw_audit_full, answers), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["top_cited_hosts"] == {"yelp.de": 2, "gelbeseiten.de": 1}
    assert measured["missing_on"] == ["yelp.de"]


def test_k5_blocked_crawlers(raw_audit_full: RawAudit, with_website: Edit) -> None:
    robots = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    outcome, measured = run("k5", with_website(raw_audit_full, robots_txt=robots), {})
    assert outcome is CheckOutcome.PARTIAL
    assert measured["blocked_crawlers"] == ["GPTBot"]
    everything = with_website(raw_audit_full, robots_txt="User-agent: *\nDisallow: /\n")
    assert run("k5", everything, {})[0] is CheckOutcome.FAILED
    absent = with_website(raw_audit_full, robots_txt=None)
    assert run("k5", absent, {}) == (
        CheckOutcome.PASSED,
        {"blocked_crawlers": [], "has_robots_txt": False},
    )


def _routed(provider: str, route: AnswerRoute, **fields: object) -> AssistantAnswer:
    answer = _answer(RECOMMENDATION, mentioned=True, provider=provider)
    return answer.model_copy(update={"route": route, **fields})


def test_answer_routes_name_the_paid_interface_when_any_answer_used_it() -> None:
    """A paid answer among browser answers marks the assistant; free routes stay as they are."""
    answers = [
        _routed("chatgpt", "browser"),
        _routed("chatgpt", "api"),
        _routed("gemini", "browser"),
        _routed("perplexity", "api"),
        _routed("claude", "claude_code"),
        _answer(RECOMMENDATION, mentioned=True, provider="openai"),
    ]

    assert answer_routes(answers) == {
        "chatgpt": "openai_api",
        "gemini": "browser",
        "perplexity": "perplexity_api",
        "claude": "claude_code",
    }


def test_k1_to_k4_record_the_routes(raw_audit_full: RawAudit) -> None:
    """Every answer-based check carries the routes of the answers it used."""
    assert raw_audit_full.place is not None
    phone = raw_audit_full.place.phone
    answers = [
        _routed("chatgpt", "api", answer_excerpt=f"Tel. {phone}", cited_sources=["https://x.de"]),
        _routed("gemini", "browser", question=PROBLEM, answer_excerpt=f"Tel. {phone}"),
    ]
    raw = _with_answers(raw_audit_full, answers)

    assert run("k1", raw, THRESHOLDS["k1"])[1]["routes"] == {"chatgpt": "openai_api"}
    assert run("k2", raw, THRESHOLDS["k2"])[1]["routes"] == {"gemini": "browser"}
    assert run("k3", raw, THRESHOLDS["k3"])[1]["routes"] == {
        "chatgpt": "openai_api",
        "gemini": "browser",
    }
    assert run("k4", raw, THRESHOLDS["k4"])[1]["routes"] == {"chatgpt": "openai_api"}


def test_answers_without_routes_record_none(raw_audit_full: RawAudit) -> None:
    """Older audits without routes keep their measured values unchanged."""
    raw = _with_answers(raw_audit_full, [_answer(RECOMMENDATION, mentioned=True)])

    assert "routes" not in run("k1", raw, THRESHOLDS["k1"])[1]
