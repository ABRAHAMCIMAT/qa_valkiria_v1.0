"""Incremento 3: los resultados alimentan el flujo (borradores de defecto, PR bloqueado, ejecución desactualizada) y el pipeline de Azure DevOps."""

import yaml

from test_conversation import Chat
from valkiria.application.defects import suggest, triage
from valkiria.application.pipeline_files import REPORT_RESULTS, pipeline_files

REQUIREMENT = "Necesito que los asesores consulten vehículos disponibles por concesionario"


class FailingWebRunner:
    """Runner web de prueba: el primer caso falla en un paso, el segundo en el resultado esperado; el resto pasa."""

    async def run(self, *, base_url, cases):
        results = []
        for n, case in enumerate(cases):
            result = {"case_id": case["id"], "kind": "web", "result": "pass", "status": "pass", "request": "GET /ui", "steps_executed": 3, "steps_total": 3,
                      "expected": {"texts": ["Concesionario inactivo"], "error_alert": True}}
            if n == 0:
                result |= {"result": "fail", "status": "fail", "failed_step": "Hacer clic en Exportar", "detail": "No se encontró el elemento del paso en la pantalla"}
            if n == 1:
                result |= {"result": "fail", "status": "fail", "failed_step": "Resultado esperado", "detail": "No se ve «Concesionario inactivo»"}
            results.append(result)
        return results


def flow_with_failures(token=None, monkeypatch=None):
    if token:
        monkeypatch.setenv("VALKIRIA_PIPELINE_CALLBACK_TOKEN", token)
    chat = Chat(web_runner=FailingWebRunner())
    chat.say(REQUIREMENT)
    chat.say(type="run", goal="matrix")
    chat.say("Genera los scripts en Playwright para web en el repositorio nissan-qa/web-tests")
    chat.say(type="approve", artifact="automation")
    return chat


def step(response, key):
    return next(s for s in response["flow"]["steps"] if s["key"] == key)


def test_each_failed_case_becomes_a_defect_draft_with_a_suggested_classification():
    chat = flow_with_failures()
    run = chat.say("Ejecuta las pruebas")
    assert run["artifact"]["data"]["summary"]["failed"] == 2 and "borrador(es) de defecto" in run["reply"]
    review = run["actions"][0]
    assert review["type"] == "review_defects" and [d["suggested"] for d in review["defects"]] == ["test_issue", "defect"]
    assert step(run, "triage")["state"] == "action"
    triage_record = chat.say(type="run", goal="triage")["artifact"]["data"]
    first = triage_record["defects"][0]
    assert first["case_id"] == run["artifact"]["data"]["results"][0]["case_id"] and first["criterion_id"]  # ligado al caso y al criterio
    assert first["azure_bug"]["System.WorkItemType"] == "Bug" and "caso:" in first["azure_bug"]["System.Tags"]  # vista previa de Azure Boards


def test_the_pull_request_is_blocked_until_every_failure_is_reviewed():
    chat = flow_with_failures()
    chat.say("Ejecuta las pruebas")
    chat.say("Genera el pipeline de Azure DevOps")
    blocked = chat.say(type="approve", artifact="pipeline")
    assert blocked["intent"] == "aclarar" and "fallos sin revisar" in blocked["reply"] and blocked["actions"][0]["type"] == "review_defects"
    assert not any(a.get("artifact") == "pipeline" for a in blocked["flow"]["next"] if a["type"] == "approve")
    ids = [d["id"] for d in blocked["actions"][0]["defects"]]
    partial = chat.say(type="review_defects", decisions={ids[0]: "test_issue"})
    assert partial["intent"] == "aclarar" and ids[1] in partial["reply"]  # cada fallo requiere decisión
    reviewed = chat.say(type="review_defects", decisions={ids[0]: "test_issue", ids[1]: "defect"})
    assert reviewed["intent"] == "aprobar" and "1 defecto(s) confirmados" in reviewed["reply"] and "ya puede aprobarse" in reviewed["reply"]
    assert chat.say(type="approve", artifact="pipeline")["intent"] == "aprobar"


def test_changing_the_scripts_marks_the_execution_as_outdated_without_rerunning_it():
    chat = flow_with_failures()
    first = chat.say("Ejecuta las pruebas")["artifact"]["version"]
    changed = chat.say("Genera los scripts en Selenium para web en el repositorio nissan-qa/web-tests")
    assert changed["artifact"]["kind"] == "automation" and changed["artifact"]["version"] == 2
    assert step(changed, "execution")["state"] == "stale" and step(changed, "execution")["version"] == first  # no se repitió sola
    chat.say(type="approve", artifact="automation")
    again = chat.say("Ejecuta las pruebas")
    assert again["artifact"]["version"] == first + 1 and step(again, "execution")["state"] == "done"


def test_pipeline_includes_data_validation_and_reports_back_to_valkiria():
    chat = flow_with_failures()
    chat.say(type="run", goal="data_validation")
    chat.say(type="approve", artifact="data_validation")
    pipeline = chat.say("Genera el pipeline de Azure DevOps")["artifact"]["data"]
    stages = [s["stage"] for s in yaml.safe_load(pipeline["yaml"])["stages"]]
    assert stages == ["Build", "Test", "DataValidation", "ReportToValkiria", "Publish"]
    assert "$(valkiria-pipeline-token)" in pipeline["yaml"] and "$(synthetic-database-url)" in pipeline["yaml"]  # secretos del grupo ligado a Key Vault
    queries = yaml.safe_load(pipeline["files"]["valkiria/queries.json"])
    assert queries and all(q["sql"].lower().startswith("select") for q in queries)  # solo las consultas aprobadas y de solo lectura
    assert "valkiria/report_results.py" in pipeline["files"]


def test_pipeline_results_become_a_new_execution_with_its_review(monkeypatch):
    chat = flow_with_failures(token="token-de-prueba", monkeypatch=monkeypatch)
    run = chat.say("Ejecuta las pruebas")
    cases = [r["case_id"] for r in run["artifact"]["data"]["results"]]
    workflow_id = run["flow"]["workflow_id"]
    body = {"run_id": "4711", "results": [
        {"name": f"{cases[0]}: escenario", "classname": "specs", "result": "fail", "message": "Timeout 5000ms", "file": "test-results.xml"},
        {"name": f"test_{cases[1].lower().replace('-', '_')}", "classname": "tests.test", "result": "pass"},  # nombre de pytest (Selenium)
        {"name": "sin caso", "result": "skipped"}]}
    url = f"/v1/workflows/{workflow_id}/pipeline-results"
    assert chat.api.post(url, json=body).status_code == 401
    assert chat.api.post(url, json=body, headers={"Authorization": "Bearer otro"}).status_code == 401
    view = chat.api.post(url, json=body, headers={"Authorization": "Bearer token-de-prueba"}).json()
    execution = view["artifacts"]["execution"]
    assert execution["version"] == run["artifact"]["version"] + 1 and execution["produced_by"] == "azure_pipeline"
    assert [(r["case_id"], r["result"]) for r in execution["payload"]["results"]] == [(cases[0], "fail"), (cases[1], "pass")]
    triage_record = view["artifacts"]["triage"]
    assert triage_record["based_on"] == {"execution": execution["version"]} and len(triage_record["payload"]["defects"]) == 1
    status = chat.say("¿Qué sigue?")
    assert "recibí del pipeline" not in status["reply"]  # la conversación continúa; la revisión nueva queda como siguiente paso
    assert any(a["type"] == "review_defects" for a in status.get("actions", []) + status["flow"]["next"])


def test_pipeline_results_endpoint_is_disabled_without_a_token():
    chat = flow_with_failures()
    run = chat.say("Ejecuta las pruebas")
    response = chat.api.post(f"/v1/workflows/{run['flow']['workflow_id']}/pipeline-results", json={"run_id": "1", "results": [{"name": "x", "result": "pass"}]},
                             headers={"Authorization": "Bearer algo"})
    assert response.status_code == 503


def test_suggestions_distinguish_product_defects_from_test_issues_and_environment():
    assert suggest({"kind": "api", "status": 500, "expected_status": [200]})[0] == "defect"
    assert suggest({"kind": "api", "status": None})[0] == "environment"
    assert suggest({"kind": "database", "result": "error"})[0] == "test_issue"
    assert suggest({"kind": "web", "failed_step": "Resultado esperado", "detail": "No se ve «Error de conexión»"})[0] == "test_issue"  # mensaje inexistente
    assert suggest({"kind": "web", "failed_step": "Resultado esperado", "detail": "No se ve «Vehículo sin stock»"})[0] == "defect"


def test_web_cases_that_never_submit_or_bounce_between_screens_are_flagged_as_test_issues():
    # Caso real en vivo: pasos que capturan datos y nunca presionan «Registrar orden», esperando «Orden aprobada».
    never_submits = {"id": "TC-AC-01-P", "scenario": "Registrar orden", "expected_result": "Llega el mensaje Orden aprobada",
                     "steps": ["Ir a registrar orden", "Seleccionar Vehiculo Sentra 2025"]}
    result = {"kind": "web", "failed_step": "Resultado esperado", "detail": "No se ve «Orden aprobada»"}
    assert suggest(result, never_submits)[0] == "test_issue" and "nunca presiona" in suggest(result, never_submits)[1]
    bounces = {"id": "TC-AC-02-P", "scenario": "", "expected_result": "Orden aprobada",
               "steps": ["Abrir Consulta de inventario", "Seleccionar Vehiculo Sentra 2025", "Marcar Solo disponibles", "Hacer clic en Registrar orden"]}
    assert "van y vienen" in suggest(result, bounces)[1]


def test_identical_failures_keep_their_previous_decision():
    results = [{"case_id": "TC-1", "kind": "api", "status": 500, "expected_status": [200], "result": "fail"}]
    first = triage(results, {}, execution_version=1, report_id=None, story_title="HU")
    from valkiria.application.defects import carry_decisions
    again = triage(results, {}, execution_version=2, report_id=None, story_title="HU", previous=carry_decisions([first[0] | {"decision": "defect"}]))
    assert again[0]["decision"] == "defect"


def test_report_script_collects_junit_from_every_stack(tmp_path):
    (tmp_path / "test-results.xml").write_text('<testsuites><testsuite><testcase name="TC-AC-01-P: ok" time="0.5"/>'
                                               '<testcase name="TC-AC-01-N: x"><failure message="No se ve">detalle</failure></testcase></testsuite></testsuites>')
    (tmp_path / "surefire-reports").mkdir()
    (tmp_path / "surefire-reports" / "TEST-a.xml").write_text('<testsuite><testcase name="TC-AC-02-P" classname="x"><error message="boom"/></testcase>'
                                                             '<testcase name="t"><skipped/></testcase></testsuite>')
    namespace: dict = {}
    exec(compile(REPORT_RESULTS, "report_results.py", "exec"), namespace)  # noqa: S102 - código generado por Valkiria, bajo prueba
    collected = namespace["collect"](str(tmp_path))
    assert [(r["name"], r["result"]) for r in collected] == [("TC-AC-02-P", "error"), ("t", "skipped"), ("TC-AC-01-P: ok", "pass"), ("TC-AC-01-N: x", "fail")]
    assert collected[3]["message"] == "No se ve" and collected[2]["duration_ms"] == 500.0


def test_pipeline_files_only_carry_ready_queries():
    files = pipeline_files([{"id": "DV-01", "sql": "SELECT 1", "expect": "rows", "status": "lista"}, {"id": "DV-02", "sql": "DELETE FROM x", "status": "bloqueada"}])
    assert '"DV-01"' in files["valkiria/queries.json"] and "DV-02" not in files["valkiria/queries.json"]
    assert "SET TRANSACTION READ ONLY" in files["valkiria/validate_data.py"]
    assert pipeline_files(None).keys() == {"valkiria/report_results.py"}


def test_a_failed_step_is_not_retried_by_unrelated_actions():
    # Caso real en vivo: la validación de datos falló (JSON truncado) y se reintentaba 3 veces en cada aprobación posterior (~140 s cada una).
    from valkiria.providers.openai_compatible import LLMProviderError
    from workflow_fakes import ScriptedLLM, prompt_kind

    llm = ScriptedLLM()
    original, calls = llm.generate_json, {"sql": 0}

    async def sql_always_fails(*, system, user, schema):
        if prompt_kind(system) == "sql":
            calls["sql"] += 1
            raise LLMProviderError("llm_invalid_response")
        return await original(system=system, user=user, schema=schema)

    llm.generate_json = sql_always_fails
    chat = Chat(llm, web_runner=FailingWebRunner())
    chat.say(REQUIREMENT)
    chat.say(type="run", goal="matrix")
    failed = chat.say("Valida los datos de la historia")
    assert failed["intent"] == "error" and failed["actions"][0]["label"] == "Reintentar"
    tried = calls["sql"]
    chat.say(type="approve", artifact="matrix")
    chat.say("Genera los scripts en Playwright para web en el repositorio nissan-qa/web-tests")
    chat.say(type="approve", artifact="automation")
    assert calls["sql"] == tried  # nada ajeno lo reintenta
    chat.say(type="run", goal="data_validation")
    assert calls["sql"] > tried  # pedirlo de nuevo sí lo reintenta
