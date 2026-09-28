import os

import pytest

from valkiria.infrastructure.settings import Settings
from valkiria.infrastructure.synthetic_database import SyntheticPostgresExecutor

pytestmark = pytest.mark.skipif(os.getenv("RUN_SYNTHETIC_E2E") != "1", reason="E2E sintética deshabilitada; usar RUN_SYNTHETIC_E2E=1")


@pytest.fixture
def executor():
    url = Settings().secret("synthetic_database_url")
    if not url or not url.startswith("postgresql"):
        pytest.fail("La E2E de mutaciones requiere VALKIRIA_SYNTHETIC_DATABASE_URL apuntando a PostgreSQL")
    return SyntheticPostgresExecutor(url)


def stock(executor, vehicle_id):
    result, _ = executor.execute(script=f"SELECT stock FROM vehicles WHERE vehicle_id = {vehicle_id}", case_id=f"PG-CHECK-{vehicle_id}", trace_id="trace-check")
    assert result["status"] == "completed"
    return result["rows"][0]["stock"]


def test_update_with_limit_runs_on_postgresql_and_rolls_back(executor):
    before = stock(executor, 1)
    result, report = executor.execute(script="BEGIN; UPDATE vehicles SET stock = 0 WHERE vehicle_id = 1 LIMIT 1; ROLLBACK;", case_id="PG-MUT-001", trace_id="trace-pg-update")
    assert result["status"] == "completed", result
    assert result["affected_rows"] == 1
    assert result["dialect_rewrites"][0]["executed"].startswith("UPDATE vehicles SET stock = 0 WHERE ctid IN (")
    assert report is not None
    assert stock(executor, 1) == before


def test_limit_caps_affected_rows(executor):
    result, _ = executor.execute(script="BEGIN; UPDATE vehicles SET stock = stock + 1 WHERE stock >= 0 LIMIT 2; ROLLBACK;", case_id="PG-MUT-002", trace_id="trace-pg-cap")
    assert result["status"] == "completed", result
    assert result["affected_rows"] == 2


def test_delete_with_limit_runs_on_postgresql_and_rolls_back(executor):
    result, _ = executor.execute(script="BEGIN; DELETE FROM parts WHERE stock >= 0 LIMIT 1; ROLLBACK;", case_id="PG-MUT-003", trace_id="trace-pg-delete")
    assert result["status"] == "completed", result
    assert result["affected_rows"] == 1
    check, _ = executor.execute(script="SELECT COUNT(*) AS total FROM parts", case_id="PG-MUT-003-CHECK", trace_id="trace-pg-delete-check")
    assert check["rows"][0]["total"] == 2


def test_untranslatable_mutation_is_blocked_before_connecting(executor):
    result, report = executor.execute(script="BEGIN; UPDATE vehicles SET stock = 0 WHERE vehicle_id = 1 LIMIT 1 RETURNING *; ROLLBACK;", case_id="PG-MUT-004", trace_id="trace-pg-blocked")
    assert result["status"] == "blocked"
    assert "postgresql_limit_requires_simple_form_or_subquery" in result["static_analysis"]["findings"]
    assert report is None
