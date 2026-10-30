"""Stand-in for the Claude Code CLI in tests of the headless analysis.

Context: Copied into a temporary folder with an executable shebang by the fake_claude fixture.
Input: The arguments of `claude -p "/auswerten <folder>" ...`, `claude -p "/optimieren <folder>"`,
the heartbeat's answer check `claude -p "Antworte nur mit OK." ...`, an assistant question
`claude -p "<question>" ...`, a mail to classify or `claude auth status`; mode from
FAKE_CLAUDE_MODE (ok, invalid, not_logged_in, error, garbage, sleep, empty, mail).
Output: findings.json and texte.json (or aenderungen.json) in the folder or an answer text, one JSON
result on stdout, argv.json (and cwd.txt for questions) next to the script.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

PING_PROMPT = "Antworte nur mit OK."
TEXTS = {
    "introduction": "Sie werden gefunden.",
    "summary": "Gut. Lückenhaft. Teuer.",
    "forecast_note": "Eine Schätzung.",
    "subscription_reason": "Ihre Wettbewerber holen auf.",
    "offer_text": "Erstoptimierung.",
    "talk_text": "Sie stehen auf der Karte hinter drei Nachbarn.",
}
MAIL_ANSWER = {
    "intent": "accept",
    "quote_number": "AN-2026-0004",
    "confidence": "high",
    "summary": "nimmt das Angebot an",
}
ANSWER = (
    "Empfehlenswert ist die **Dachdeckerei Hollenbeck GmbH** in Münster.\n\n"
    "Sources:\n- [Hollenbeck](https://hollenbeck.de)\n"
    "- [Gelbe Seiten](https://www.gelbeseiten.de/dachdecker/muenster)"
)


def main() -> None:
    """Behave like Claude Code in the mode the test chose."""
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    if sys.argv[1:3] == ["auth", "status"]:
        print(json.dumps({"loggedIn": mode != "not_logged_in", "authMethod": "claude.ai"}))
        return
    Path(__file__).with_name("argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
    if sys.argv[2].startswith("/optimieren "):
        optimise(Path(sys.argv[2].removeprefix("/optimieren ")), mode)
        return
    if mode == "sleep":
        time.sleep(30)
    if mode == "not_logged_in":
        fail("Not logged in · Please run /login")
    if mode == "error":
        fail("API Error: 529 Overloaded")
    if mode == "garbage":
        print("this is not json")
        return
    if sys.argv[2] == PING_PROMPT:
        succeed("OK")
        return
    if not sys.argv[2].startswith("/auswerten "):
        Path(__file__).with_name("cwd.txt").write_text(os.getcwd(), encoding="utf-8")
        answers = {"empty": "", "mail": json.dumps(MAIL_ANSWER)}
        succeed(answers.get(mode, ANSWER))
        return
    folder = Path(sys.argv[2].removeprefix("/auswerten "))
    audit_id = json.loads((folder / "audit.json").read_text(encoding="utf-8"))["audit"]["id"]
    finding = {
        "id": str(uuid.uuid4()),
        "audit_id": audit_id,
        "check_id": "a1",
        "severity": 3,
        "title": "Ohne HTTPS.",
        "explanation": "Kein Zertifikat.",
        "customer_impact": "Warnung.",
        "fix": "Zertifikat.",
        "fix_payload": None,
    }
    texts = TEXTS if mode == "ok" else {"introduction": ""}
    (folder / "findings.json").write_text(json.dumps([finding]), encoding="utf-8")
    (folder / "texte.json").write_text(json.dumps(texts), encoding="utf-8")
    succeed("1 Befund")


def succeed(result: str) -> None:
    """Print the JSON result of a successful run."""
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result}))


def fail(result: str) -> None:
    """Print the JSON result of a failed run and exit with code 1."""
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": result}))
    sys.exit(1)


def optimise(folder: Path, mode: str) -> None:
    """Draft one WordPress description (when reachable) and one manual change."""
    changes: list[dict[str, object]] = [
        {"target": "manual:Branchenbuch", "check_id": None, "before": None, "after": "Eintragen."}
    ]
    wordpress = folder / "wordpress.json"
    if wordpress.exists():
        item = json.loads(wordpress.read_text(encoding="utf-8"))["items"][0]
        changes.append(
            {
                "target": f"wordpress:{item['type']}:{item['id']}:meta_description",
                "check_id": "a1",
                "before": item["meta_description"],
                "after": "Dachdecker in Münster: Steildach und Reparatur. Jetzt Termin anfragen.",
            }
        )
    if mode == "invalid":
        changes.append({"target": "ftp:server", "check_id": None, "before": None, "after": "x"})
    (folder / "aenderungen.json").write_text(json.dumps({"changes": changes}), encoding="utf-8")
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "2"}))


if __name__ == "__main__":
    main()
