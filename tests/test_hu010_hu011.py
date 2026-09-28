import pytest
from valkiria.application.automation_execution import create_automation_batch, execute_database_script, static_analyse_database_script, suggest_database_tool

def test_hu010_generates_traceable_scripts_for_max_15_cases():
    cases = [{"id": "TC-001", "scenario": "login"}, {"id": "TC-002", "scenario": "logout"}]
    result = create_automation_batch(cases=cases, framework="playwright", platform="web", repository="org/repo", base_branch="main", matrix_status="draft")
    assert result["traceability"] is True
    assert result["pr_required"] is True
    assert set(result["scripts"]) == {"TC-001", "TC-002"}

def test_hu010_suggests_split_over_15():
    cases = [{"id": f"TC-{i:03d}"} for i in range(16)]
    result = create_automation_batch(cases=cases, framework="selenium", platform="web", repository="org/repo", base_branch="main", matrix_status="in_progress")
    assert result["status"] == "requires_split"
    assert result["max_cases"] == 15

def test_hu011_suggests_database_tool():
    result = suggest_database_tool("postgresql", "python", "qa")
    assert result["framework"] == "pytest + SQLAlchemy"
    assert result["execution_allowed"] is True

def test_hu011_blocks_production_without_generating_report():
    result, report = execute_database_script(engine="postgresql", environment="production", script="select 1", case_id="TC-DB-001", output_format="pdf", actor="qa")
    assert result["status"] == "blocked"
    assert result["report_generated"] is False
    assert report is None

def test_hu011_requires_rollback_for_mutation():
    analysis = static_analyse_database_script("update users set active = true limit 1")
    assert analysis["passed"] is False
    assert "mutation_requires_transaction" in analysis["findings"]

def test_hu011_generates_report_for_safe_qa_script():
    result, report = execute_database_script(engine="postgresql", environment="qa", script="select id from users limit 10", case_id="TC-DB-001", output_format="word", actor="qa")
    assert result["status"] == "completed"
    assert result["report_generated"] is True
    assert report["format"] == "word"
