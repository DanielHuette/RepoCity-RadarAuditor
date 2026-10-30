"""Checks of area k: assistenten (ids k1..).

Context: One pure function per catalogue check; each returns outcome and measured values.
Input: RawAudit and thresholds.
Output: (CheckOutcome, measured dict).
"""

import re
from collections import Counter
from urllib.robotparser import RobotFileParser

from radarauditor.collectors.patterns import host_of
from radarauditor.collectors.snapshot import AssistantAnswer, RawAudit
from radarauditor.db.models import CheckOutcome
from radarauditor.scoring.checks.common import (
    CheckFunction,
    CheckReturn,
    Thresholds,
    at_least_outcome,
    combine,
    customer_host,
    dispatch,
    fold,
    nap_value,
    not_measurable,
    number,
    phone_key,
    postal_code,
    share_outcome,
)

RECOMMENDATION_MARKERS = ("empfiehlst", "guter", "top 3")
PHONE_PATTERN = re.compile(r"(?:\+49|0)[\d\s/()-]{6,}\d")
AI_CRAWLERS = (
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "PerplexityBot",
    "Google-Extended",
    "CCBot",
)
TOP_CITED_HOSTS = 5
# Route ids the app shows as the paid interface behind an assistant (app/src/lib/routes.ts).
PAID_ROUTE_IDS = {"chatgpt": "openai_api", "gemini": "gemini_api", "perplexity": "perplexity_api"}


def is_recommendation_question(question: str) -> bool:
    """True for questions asking for a business recommendation rather than describing a problem."""
    folded = fold(question)
    return any(marker in folded for marker in RECOMMENDATION_MARKERS)


def answer_routes(answers: list[AssistantAnswer]) -> dict[str, str]:
    """Route per assistant: its paid interface when any answer came from it, else the one used.

    Paid answers name the interface ("openai_api", "gemini_api", "perplexity_api"); free ones are
    "browser" or "claude_code". Answers without a recorded route (older audits) are left out.
    """
    used: dict[str, set[str]] = {}
    for answer in answers:
        if answer.route is not None:
            used.setdefault(answer.provider, set()).add(answer.route)
    return {
        provider: PAID_ROUTE_IDS.get(provider, "api") if "api" in kinds else min(kinds)
        for provider, kinds in used.items()
    }


def _with_routes(result: CheckReturn, answers: list[AssistantAnswer]) -> CheckReturn:
    outcome, measured = result
    routes = answer_routes(answers)
    return outcome, ({**measured, "routes": routes} if routes else measured)


def _mention_rate(answers: list[AssistantAnswer], minimum: float) -> CheckReturn:
    """Share of answers naming the business, overall and per provider."""
    mentioned = sum(1 for answer in answers if answer.mentioned)
    rate = mentioned / len(answers)
    providers: dict[str, list[AssistantAnswer]] = {}
    for answer in answers:
        providers.setdefault(answer.provider, []).append(answer)
    by_provider = {
        provider: round(sum(1 for item in items if item.mentioned) / len(items), 3)
        for provider, items in providers.items()
    }
    measured: dict[str, object] = {
        "mention_rate": round(rate, 3),
        "mentioned": mentioned,
        "answers": len(answers),
        "by_provider": by_provider,
        "min_rate": minimum,
    }
    return _with_routes((at_least_outcome(rate, minimum), measured), answers)


def recommendation_mentions(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """k1: the business is named in at least min_rate of answers to recommendation questions."""
    answers = [a for a in raw.assistant_answers if is_recommendation_question(a.question)]
    if not answers:
        return not_measurable("no_recommendation_answers")
    return _mention_rate(answers, number(thresholds, "min_rate"))


def problem_mentions(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """k2: the business is named in at least min_rate of answers to problem questions."""
    answers = [a for a in raw.assistant_answers if not is_recommendation_question(a.question)]
    if not answers:
        return not_measurable("no_problem_answers")
    return _mention_rate(answers, number(thresholds, "min_rate"))


def _reference_facts(raw: RawAudit) -> tuple[str | None, str | None]:
    """Phone and postal code of the business from the Google profile, else the website footer."""
    if raw.place is not None:
        return phone_key(raw.place.phone), postal_code(raw.place.formatted_address)
    if raw.website is not None:
        nap = raw.website.footer_nap
        return phone_key(nap_value(nap, "phone")), postal_code(nap_value(nap, "postal_code"))
    return None, None


def correct_facts(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """k3: answers that name the business and quote a phone or postal code quote the right one."""
    phone, code = _reference_facts(raw)
    if phone is None and code is None:
        return not_measurable("no_reference_facts")
    checked: list[AssistantAnswer] = []
    wrong: list[str] = []
    for answer in raw.assistant_answers:
        if not answer.mentioned:
            continue
        phones = {phone_key(match) for match in PHONE_PATTERN.findall(answer.answer_excerpt)}
        codes = set(re.findall(r"\b\d{5}\b", answer.answer_excerpt))
        verdicts = []
        if phone is not None and phones:
            verdicts.append(phone in phones)
        if code is not None and codes:
            verdicts.append(code in codes)
        if verdicts:
            checked.append(answer)
            if not all(verdicts):
                wrong.append(answer.answer_excerpt[:200])
    if not checked:
        return not_measurable("no_checkable_answers")
    measured: dict[str, object] = {
        "answers_checked": len(checked),
        "answers_with_wrong_facts": len(wrong),
        "wrong_examples": wrong[:3],
    }
    outcome = share_outcome(len(checked) - len(wrong), len(checked))
    return _with_routes((outcome, measured), checked)


def cited_sources(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """k4: the business is present on the hosts assistants cite most."""
    cited = Counter(
        host
        for answer in raw.assistant_answers
        for source in answer.cited_sources
        if (host := host_of(source)) is not None
    )
    if not cited:
        return not_measurable("no_cited_sources")
    present_hosts = {
        host
        for hit in raw.directories
        if hit.found and (host := host_of(hit.listing_url)) is not None
    }
    own = customer_host(raw)
    if own is not None:
        present_hosts.add(own)
    top_hosts = [host for host, _ in cited.most_common(TOP_CITED_HOSTS)]
    missing = [host for host in top_hosts if host not in present_hosts]
    measured: dict[str, object] = {
        "top_cited_hosts": {host: cited[host] for host in top_hosts},
        "missing_on": missing,
    }
    outcome = share_outcome(len(top_hosts) - len(missing), len(top_hosts))
    citing = [answer for answer in raw.assistant_answers if answer.cited_sources]
    return _with_routes((outcome, measured), citing)


def ai_crawlers_allowed(raw: RawAudit, thresholds: Thresholds) -> CheckReturn:
    """k5: robots.txt lets the assistants' crawlers read the start page."""
    if raw.website is None:
        return not_measurable("no_website")
    robots = raw.website.robots_txt
    if robots is None:
        return CheckOutcome.PASSED, {"blocked_crawlers": [], "has_robots_txt": False}
    parser = RobotFileParser()
    parser.parse(robots.splitlines())
    start = str(raw.website.start_url)
    blocked = [agent for agent in AI_CRAWLERS if not parser.can_fetch(agent, start)]
    outcome = combine(
        CheckOutcome.FAILED if agent in blocked else CheckOutcome.PASSED for agent in AI_CRAWLERS
    )
    return outcome, {"blocked_crawlers": blocked, "has_robots_txt": True}


CHECKS: dict[str, CheckFunction] = {
    "k1": recommendation_mentions,
    "k2": problem_mentions,
    "k3": correct_facts,
    "k4": cited_sources,
    "k5": ai_crawlers_allowed,
}


def run(check_id: str, raw: RawAudit, thresholds: dict[str, float | int | str]) -> CheckReturn:
    """Dispatch to the function for check_id within this area."""
    return dispatch(CHECKS, check_id, raw, thresholds)
