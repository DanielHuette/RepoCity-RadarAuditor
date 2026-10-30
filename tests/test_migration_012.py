"""Text checks of migration 012 (start_field_audit split into parts, check ids of any area).

Context: Guards the shape of the file: same signature and rights as 011, short parts with the same
rights, the general id pattern and statements that can run twice. Behaviour is proven against a real
database in test_migration_database.py. | Input: supabase/migrations. | Output: Assertions.
"""

import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
SQL = (MIGRATIONS / "012_field_audit_parts.sql").read_text(encoding="utf-8")
SQL_011 = (MIGRATIONS / "011_url_only_audit.sql").read_text(encoding="utf-8")
START_ARGUMENTS = "text, text, text, text, text[], jsonb, text[]"
PARTS = {
    "normalize_field_audit_selection": "text[]",
    "save_field_audit_customer": "text, text, text, text, text",
    "place_field_audit_location": "uuid, text, text, text, text, text, boolean",
    "move_field_audit_keywords": "uuid, text, text",
    "follow_field_audit_correction": "uuid, uuid, text, text, text",
    "add_field_audit_keywords": "uuid, text, text[]",
    "add_field_audit_competitors": "uuid, jsonb",
    "queue_field_audit": "uuid, text[]",
}
MAX_FUNCTION_LINES = 45
MAX_BODY_CODE_LINES = 30


def _function(sql: str, name: str) -> str:
    start = sql.index(f"create or replace function public.{name}(")
    end = sql.index("end $$;", start)
    return sql[start:end]


def _signature(sql: str, name: str) -> str:
    head = _function(sql, name).split("\nlanguage ", 1)[0]
    return re.sub(r"\s+", " ", head).strip()


def _without_comments(text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def test_header_names_context_input_and_output() -> None:
    header = SQL.split("\n\n", 1)[0]

    assert header.startswith("-- ")
    for part in ("Context:", "| Input:", "| Output:"):
        assert part in header


def test_start_keeps_the_signature_of_011() -> None:
    assert _signature(SQL, "start_field_audit") == _signature(SQL_011, "start_field_audit")


@pytest.mark.parametrize("name", ["start_field_audit", *PARTS])
def test_every_function_runs_as_invoker_with_a_fixed_search_path(name: str) -> None:
    head = _function(SQL, name).split(" as $$", 1)[0]

    assert "security invoker set search_path = public, pg_temp" in head
    assert "security definer" not in head


@pytest.mark.parametrize(
    ("name", "arguments"), [("start_field_audit", START_ARGUMENTS), *PARTS.items()]
)
def test_only_authenticated_and_service_role_may_execute(name: str, arguments: str) -> None:
    function = f"public.{name}({arguments})"

    assert f"revoke all on function {function} from public, anon;" in SQL
    assert f"grant execute on function {function} to authenticated, service_role;" in SQL


@pytest.mark.parametrize(
    "name", [name for name in PARTS if name != "normalize_field_audit_selection"]
)
def test_every_writing_part_checks_the_administrator(name: str) -> None:
    assert "perform public.require_admin();" in _function(SQL, name)


def test_start_checks_first_and_hands_every_step_to_a_part() -> None:
    body = _without_comments(_function(SQL, "start_field_audit"))

    assert body.index("perform public.require_admin();") < body.index(
        "public.normalize_field_audit"
    )
    for name in PARTS:
        assert f"public.{name}(" in body
    assert "perform public.link_crm_customer(" in body
    assert "insert into" not in body
    assert "update " not in body


@pytest.mark.parametrize("name", ["start_field_audit", *PARTS])
def test_functions_stay_short(name: str) -> None:
    function = _function(SQL, name)
    body = _without_comments(function.split("\nbegin\n", 1)[1])
    code_lines = [line for line in body.splitlines() if line.strip()]

    assert len(function.splitlines()) <= MAX_FUNCTION_LINES
    assert len(code_lines) <= MAX_BODY_CODE_LINES


def test_check_ids_of_every_area_pass_function_and_column_rule() -> None:
    code = _without_comments(SQL)

    assert "[a-n]" not in code
    assert "c.id is null or c.id !~ '^[a-z]\\d+$'" in _function(
        SQL, "normalize_field_audit_selection"
    )
    assert "Unbekannte Prüfung in der Auswahl" in _function(SQL, "normalize_field_audit_selection")
    constraint = re.search(r"array_to_string\(disabled_checks, ',', '~'\) ~ '([^']+)'", SQL)
    assert constraint is not None
    pattern = re.compile(constraint.group(1).replace("\\d", r"\d"))
    for valid in ["a1", "o1", "z12", "a1,n13,o4"]:
        assert pattern.fullmatch(valid)
    for invalid in ["a", "a1,", "~", "a1,~", "A1", "o", "a1;drop", "1a"]:
        assert not pattern.fullmatch(invalid)


def test_column_rule_is_replaced_and_the_comment_stays() -> None:
    assert (
        "alter table public.audits drop constraint if exists audits_disabled_checks_ids;\n"
        "alter table public.audits add constraint audits_disabled_checks_ids" in SQL
    )
    assert "comment on column public.audits.disabled_checks is" in SQL
    assert "empty means every check runs" in SQL


def test_every_statement_can_run_twice() -> None:
    code = _without_comments(SQL).lower()

    assert not re.search(r"create function", code)
    assert not re.search(r"add column (?!if not exists)", code)
    assert not re.search(r"drop (?:function|constraint) (?!if exists)", code)
    for added in re.findall(r"add constraint (\w+)", code):
        assert f"drop constraint if exists {added};" in code


def test_no_data_is_deleted_and_no_column_changes() -> None:
    code = _without_comments(SQL).lower()

    assert "delete from" not in code
    assert "alter column" not in code
    assert "drop table" not in code
