from valkiria.api.errors import ResourceNotFoundError
from valkiria.application.automation_execution import (
    create_automation_batch,
    execute_database_script,
)


def test_production_never_creates_evidence():
    result, report = execute_database_script(engine="postgresql", environment="production", script="select 1", case_id="TC-1", output_format="pdf", actor="qa")
    assert result["blocked"] is True
    assert report is None
    assert result["report_generated"] is False


def test_batch_contract_keeps_pr_only():
    result = create_automation_batch(cases=[{"id": "TC-1"}], framework="playwright", platform="web", repository="org/repo", base_branch="main", matrix_status="draft")
    assert result["traceability"] is True
    assert result["delivery"] == "pull_request_only"
    assert result["direct_commit"] is False


def test_not_found_error_is_safe():
    error = ResourceNotFoundError("lote")
    assert error.status_code == 404
    assert error.code == "lote_not_found"
