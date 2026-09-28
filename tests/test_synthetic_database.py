from valkiria.infrastructure.synthetic_database import SyntheticSQLiteExecutor


def test_synthetic_nissan_query_is_deterministic_and_traceable():
    executor = SyntheticSQLiteExecutor()
    result, report = executor.execute(script="SELECT vehicle_id, model, stock FROM vehicles WHERE stock > 0", case_id="HU011-TC-001", trace_id="trace-synthetic")
    assert result["status"] == "completed"
    assert result["engine"] == "sqlite"
    assert result["trace_id"] == "trace-synthetic"
    assert [row["model"] for row in result["rows"]] == ["Sentra", "Versa"]
    assert report["format"] == "pdf"


def test_dangerous_sql_is_blocked_before_execution():
    executor = SyntheticSQLiteExecutor()
    result, report = executor.execute(script="DROP TABLE vehicles", case_id="HU011-TC-002", trace_id="trace-danger")
    assert result["status"] == "blocked"
    assert result["static_analysis"]["passed"] is False
    assert report is None
    check, _ = executor.execute(script="SELECT COUNT(*) AS total FROM vehicles", case_id="HU011-TC-003", trace_id="trace-check")
    assert check["rows"][0]["total"] == 3


def test_mutation_rolls_back_instead_of_persisting():
    executor = SyntheticSQLiteExecutor()
    result, _ = executor.execute(script="BEGIN; UPDATE vehicles SET stock = 0 WHERE vehicle_id = 1 LIMIT 1; ROLLBACK;", case_id="HU011-TC-003B", trace_id="trace-rollback")
    assert result["status"] == "completed"
    check, _ = executor.execute(script="SELECT stock FROM vehicles WHERE vehicle_id = 1", case_id="HU011-TC-003C", trace_id="trace-check-rollback")
    assert check["rows"][0]["stock"] == 10


def test_same_case_and_script_is_idempotent():
    executor = SyntheticSQLiteExecutor()
    first, _ = executor.execute(script="SELECT COUNT(*) AS total FROM vehicles", case_id="HU011-TC-004", trace_id="trace-a")
    second, _ = executor.execute(script="SELECT COUNT(*) AS total FROM vehicles", case_id="HU011-TC-004", trace_id="trace-b")
    assert first["id"] == second["id"]
    assert second["idempotent_replay"] is True
    assert second["trace_id"] == "trace-b"
