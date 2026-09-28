import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from valkiria.api.app import create_app
from valkiria.application.automation_execution import (
    create_automation_batch,
    execute_database_script,
    export_test_cases_to_excel,
    select_execution_tool,
    static_analyse_database_script,
    suggest_database_tool,
)


def test_hu010_generates_traceable_scripts_for_max_15_cases():
    cases = [{"id": "TC-001", "scenario": "login"}, {"id": "TC-002", "scenario": "logout"}]
    result = create_automation_batch(cases=cases, framework="playwright", platform="web", repository="org/repo", base_branch="main", matrix_status="draft")
    assert result["traceability"] is True
    assert result["pr_required"] is True
    assert set(result["scripts"]) == {"TC-001.spec.ts", "TC-002.spec.ts"}
    assert result["language"] == "typescript"
    assert result["execution_tool"] == "playwright"
    assert all("assert True" not in script for script in result["scripts"].values())

def test_hu010_suggests_split_over_15():
    cases = [{"id": f"TC-{i:03d}"} for i in range(16)]
    result = create_automation_batch(cases=cases, framework="selenium", platform="web", repository="org/repo", base_branch="main", matrix_status="in_progress")
    assert result["status"] == "requires_split"
    assert result["max_cases"] == 15


def test_platform_selects_language_and_compatible_tool():
    web = select_execution_tool("web")
    mobile = select_execution_tool("movil")
    assert (web["language"], web["tool"]) == ("typescript", "playwright")
    assert (mobile["language"], mobile["tool"]) == ("javascript", "appium")
    with pytest.raises(ValueError, match="tool_not_supported_for_platform"):
        select_execution_tool("mobile", "playwright")


def test_manual_cases_export_as_valid_excel_package():
    artifact = export_test_cases_to_excel([{"id": "TC-001", "scenario": "Inicio de sesión", "steps": ["Abrir", "Ingresar"]}], story_id="HU-010")
    content = base64.b64decode(artifact["content_base64"])
    with zipfile.ZipFile(io.BytesIO(content)) as workbook:
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()
        assert b"TC-001" in workbook.read("xl/worksheets/sheet1.xml")


def test_disabled_playwright_runner_does_not_report_a_fake_success():
    client = TestClient(create_app())
    batch = client.post("/v1/automation/batches/generate", json={"cases": [{"id": "TC-001", "scenario": "smoke"}], "framework": "playwright", "platform": "web", "repository": "org/repo", "base_branch": "main", "matrix_status": "draft"}).json()
    response = client.post(f"/v1/automation/batches/{batch['id']}/execute")
    assert response.status_code == 409
    assert "playwright_runner_not_enabled" in response.json()["error"]["message"]


def test_database_evidence_can_be_downloaded_as_pdf():
    client = TestClient(create_app())
    execution = client.post("/v1/database/scripts/execute", json={"engine": "postgresql", "environment": "qa", "profile": "synthetic_postgresql", "case_id": "TC-DB-001", "output_format": "pdf", "script": "select vehicle_id from vehicles limit 1"}).json()
    response = client.get(f"/v1/reports/{execution['report_id']}/download")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF-1.4")

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
