import pytest

from valkiria.application.automation_execution import static_analyse_database_script
from valkiria.application.sql_dialect import (
    POSTGRESQL_LIMIT_NOT_TRANSLATABLE,
    split_statements,
    translate_for_postgresql,
)


def test_update_with_limit_is_rewritten_to_ctid_subquery():
    translation = translate_for_postgresql("UPDATE vehicles SET stock = 0 WHERE vehicle_id = 1 LIMIT 1")
    assert translation.finding is None
    assert translation.rewritten is True
    assert translation.executed == "UPDATE vehicles SET stock = 0 WHERE ctid IN (SELECT ctid FROM vehicles WHERE vehicle_id = 1 LIMIT 1 FOR UPDATE)"


def test_delete_with_limit_is_rewritten_to_ctid_subquery():
    translation = translate_for_postgresql("delete from public.vehicles where stock > 0 limit 2")
    assert translation.executed == "DELETE FROM public.vehicles WHERE ctid IN (SELECT ctid FROM public.vehicles WHERE stock > 0 LIMIT 2 FOR UPDATE)"


def test_rewrite_preserves_literals_and_multiline_text():
    statement = "UPDATE customers\n   SET name = 'Cliente  con  espacios'\n WHERE customer_id IN (1, 2)\n LIMIT 1"
    translation = translate_for_postgresql(statement)
    assert "'Cliente  con  espacios'" in translation.executed
    assert "WHERE customer_id IN (1, 2) LIMIT 1 FOR UPDATE" in translation.executed


@pytest.mark.parametrize("statement", [
    "SELECT * FROM vehicles LIMIT 1",
    "INSERT INTO parts SELECT * FROM parts LIMIT 1",
    "UPDATE vehicles SET stock = 0 WHERE vehicle_id IN (SELECT vehicle_id FROM vehicles WHERE stock > 0 LIMIT 1)",
])
def test_statements_already_valid_in_postgresql_are_not_changed(statement):
    translation = translate_for_postgresql(statement)
    assert translation.finding is None
    assert translation.rewritten is False


@pytest.mark.parametrize("statement", [
    "UPDATE vehicles SET stock = 0 WHERE vehicle_id = 1 LIMIT 1 RETURNING *",
    "UPDATE vehicles v SET stock = 0 WHERE v.vehicle_id = 1 LIMIT 1",
    "UPDATE vehicles SET stock = 0 FROM dealers WHERE dealers.dealer_id = 1 LIMIT 1",
    "DELETE FROM vehicles USING dealers WHERE dealers.active LIMIT 1",
    "UPDATE vehicles SET stock = (SELECT 1) WHERE vehicle_id = 1 LIMIT 1",
    "UPDATE customers SET name = 'where' WHERE customer_id = 1 LIMIT 1",
    "UPDATE vehicles SET stock = 0 -- comentario\n WHERE vehicle_id = 1 LIMIT 1",
    "UPDATE vehicles SET stock = 0 LIMIT 1",
])
def test_ambiguous_forms_are_rejected_instead_of_rewritten(statement):
    translation = translate_for_postgresql(statement)
    assert translation.finding == POSTGRESQL_LIMIT_NOT_TRANSLATABLE
    assert translation.executed == statement


def test_split_statements_handles_single_line_scripts_and_literals():
    assert split_statements("BEGIN; UPDATE t SET a = 'x;y' WHERE id = 1 LIMIT 1; ROLLBACK;") == ["BEGIN", "UPDATE t SET a = 'x;y' WHERE id = 1 LIMIT 1", "ROLLBACK"]


def test_static_analysis_reports_postgresql_rewrites():
    analysis = static_analyse_database_script("BEGIN; UPDATE vehicles SET stock = 0 WHERE vehicle_id = 1 LIMIT 1; ROLLBACK;", engine="postgresql")
    assert analysis["passed"] is True
    assert analysis["dialect_rewrites"][0]["original"] == "UPDATE vehicles SET stock = 0 WHERE vehicle_id = 1 LIMIT 1"


def test_static_analysis_blocks_untranslatable_postgresql_mutation():
    analysis = static_analyse_database_script("BEGIN; UPDATE vehicles SET stock = 0 WHERE vehicle_id = 1 LIMIT 1 RETURNING *; ROLLBACK;", engine="postgresql")
    assert analysis["passed"] is False
    assert POSTGRESQL_LIMIT_NOT_TRANSLATABLE in analysis["findings"]


def test_static_analysis_without_engine_keeps_previous_behavior():
    analysis = static_analyse_database_script("BEGIN; UPDATE vehicles SET stock = 0 WHERE vehicle_id = 1 LIMIT 1 RETURNING *; ROLLBACK;")
    assert analysis["passed"] is True
    assert analysis["dialect_rewrites"] == []


def test_each_update_requires_its_own_row_limit():
    analysis = static_analyse_database_script("BEGIN; UPDATE vehicles SET stock = 0 WHERE vehicle_id = 1; SELECT * FROM vehicles LIMIT 1; ROLLBACK;")
    assert analysis["passed"] is False
    assert "each_update_delete_requires_row_limit" in analysis["findings"]
