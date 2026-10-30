"""Text checks of migration 011 (audits started with only the website address).

Context: No database runs in the tests, so the migration is checked as text: signature, column,
validation, grants and that every statement can run twice. | Input: supabase/migrations. |
Output: Assertions.
"""

import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
SQL = (MIGRATIONS / "011_url_only_audit.sql").read_text(encoding="utf-8")
NEW_ARGUMENTS = "text, text, text, text, text[], jsonb, text[]"
CHECK_ID = re.compile(r"^[a-n]\d+$")


def _function(name: str) -> str:
    start = SQL.index(f"create or replace function public.{name}(")
    end = SQL.index("end $$;", start)
    return SQL[start:end]


def _without_comments(text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def test_signature_needs_only_the_website() -> None:
    """Seven arguments; everything after the website has a default."""
    head = _function("start_field_audit").split(") returns uuid", 1)[0]

    assert (
        re.sub(r"\s+", " ", head)
        .strip()
        .endswith(
            "start_field_audit( p_website text, p_name text default null, "
            "p_city text default null, p_industry text default null, "
            "p_keywords text[] default '{}', "
            "p_competitors jsonb default '[]', p_disabled_checks text[] default '{}'"
        )
    )


def test_old_function_is_dropped_and_grants_follow_the_new_one() -> None:
    """The six-argument version goes; the new one keeps the grants of 006."""
    assert (
        "drop function if exists public.start_field_audit(text, text, text, text, text[], jsonb);"
        in SQL
    )
    assert (
        f"revoke all on function public.start_field_audit({NEW_ARGUMENTS}) from public, anon;"
        in SQL
    )
    assert (
        f"grant execute on function public.start_field_audit({NEW_ARGUMENTS}) "
        "to authenticated, service_role;" in SQL
    )
    assert "security invoker" in _function("start_field_audit")
    assert "perform public.require_admin();" in _function("start_field_audit")
    assert "perform public.require_admin();" in _function("link_crm_customer")


def test_audits_get_their_own_selection_of_checks() -> None:
    """Column with an empty default, guarded, and a check constraint on the ids."""
    assert (
        "alter table public.audits add column if not exists disabled_checks text[] "
        "not null default '{}';" in SQL
    )
    assert "drop constraint if exists audits_disabled_checks_ids" in SQL
    assert "insert into public.audits (customer_id, kind, disabled_checks)" in SQL


def test_selection_is_validated_like_catalogue_ids() -> None:
    """The function rejects anything but ids such as "a2"; the column constraint agrees."""
    body = _function("start_field_audit")
    assert "c.id is null or c.id !~ '^[a-n]\\d+$'" in body
    assert "Unbekannte Prüfung in der Auswahl" in body
    constraint = re.search(r"array_to_string\(disabled_checks, ',', '~'\) ~ '([^']+)'", SQL)
    assert constraint is not None
    pattern = re.compile(constraint.group(1).replace("\\d", r"\d"))
    for valid in ["a1", "a1,n13,m4"]:
        assert pattern.fullmatch(valid)
    for invalid in ["z1", "a", "a1,", "~", "a1,~", "A1", "a1;drop"]:
        assert not pattern.fullmatch(invalid)


@pytest.mark.parametrize("check_id", ["a1", "f15", "n1", "m4"])
def test_catalogue_ids_pass_the_validation(check_id: str) -> None:
    """Ids of config/pruefkatalog.yaml, including the new area n, match the rule."""
    assert CHECK_ID.fullmatch(check_id)


def test_missing_values_reuse_or_create_a_provisional_customer() -> None:
    """Only the website is required; the host stands in for the name, city and industry stay ''."""
    body = _without_comments(_function("start_field_audit"))

    assert "'Name, Ort und Branche sind Pflicht.'" not in body
    assert "where public.website_host(c.website) = v_host" in body
    assert "coalesce(nullif(v_name, ''), v_host)" in body
    assert "name = coalesce(nullif(v_name, ''), name)" in body
    assert "city = coalesce(nullif(v_city, ''), city)" in body
    assert "industry = coalesce(nullif(v_industry, ''), industry)" in body


def test_no_crm_customer_with_a_provisional_name_or_without_city() -> None:
    """The CRM link waits until the worker identified name and city."""
    body = _function("link_crm_customer")

    assert "btrim(v_customer.city) = ''" in body
    assert "public.website_host(v_customer.website)" in body
    assert "perform public.link_crm_customer(v_customer.id);" in _function("start_field_audit")


def test_not_null_constraints_stay() -> None:
    """Empty texts mark unknown values, so no column loses NOT NULL and no data changes type."""
    code = _without_comments(SQL).lower()

    assert "drop not null" not in code
    assert "alter column" not in code
    assert "delete from" not in code


def test_every_statement_is_guarded() -> None:
    """Running the file twice changes nothing: every create, add and drop is guarded."""
    code = _without_comments(SQL).lower()

    assert not re.search(r"create function", code)
    assert not re.search(r"add column (?!if not exists)", code)
    assert not re.search(r"drop (?:function|constraint) (?!if exists)", code)


def test_a_correction_resets_the_wrongly_identified_location() -> None:
    """A different given name or city clears address, phone, place and position of the location."""
    body = _without_comments(_function("start_field_audit"))
    correction = body.split("elsif v_corrected then", 1)[1].split("else", 1)[0]

    for reset in (
        "street = ''",
        "postal_code = ''",
        "phone = ''",
        "google_place_id = null",
        "latitude = null",
        "longitude = null",
    ):
        assert reset in correction
    assert "lower(v_old_name) <> v_host" in body
    assert "v_old_city <> ''" in body


def test_a_corrected_city_moves_keywords_and_the_untouched_crm_customer() -> None:
    """Keywords of the old city and an unchanged CRM customer follow the correction."""
    body = _without_comments(_function("start_field_audit"))

    assert "where k.customer_id = v_customer.id and btrim(k.city) = v_old_city" in body
    assert "update crm.customers cc set" in body
    assert "when btrim(cc.name) = v_old_name" in body
