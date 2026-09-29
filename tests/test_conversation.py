"""El conductor de la conversación: todo lo que Valkiria puede hacer desde el chat, sin romper el flujo."""

from fastapi.testclient import TestClient

from valkiria.api.app import create_app
from workflow_fakes import STORY, ScriptedLLM


def synthetic_app_transport():
    """La app sintética de Nissan real, en el mismo proceso: la ejecución de HU-010 se valida contra ella, sin simular respuestas."""
    import httpx

    from valkiria.synthetic_app.app import create_synthetic_app
    return httpx.ASGITransport(app=create_synthetic_app("sqlite+pysqlite:///:memory:"))


class Chat:
    def __init__(self, llm=None):
        self.llm = llm or ScriptedLLM()
        self.api = TestClient(create_app(llm=self.llm, synthetic_transport=synthetic_app_transport()))
        self.session = None

    def say(self, message=None, **action):
        body = {"session_id": self.session} | ({"message": message} if message else {"action": action})
        data = self.api.post("/v1/chat", json=body, headers={"X-Actor": "po"}).json()
        self.session = data.get("session_id") or self.session
        return data


def step(flow, key):
    return next(s for s in flow["steps"] if s["key"] == key)


def test_full_journey_through_every_capability():
    chat = Chat()
    created = chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    assert created["intent"] == "crear" and created["artifact"]["kind"] == "story" and created["flow"]["story"]["version"] == 1
    assert created["actions"][0] == {"type": "run", "goal": "invest", "label": "Evaluar INVEST"}

    invest = chat.say(type="run", goal="invest")
    assert invest["artifact"]["kind"] == "invest" and "Testeable" in invest["reply"]
    decide = next(a for a in invest["actions"] if a["type"] == "decide_suggestions")
    assert [s["name"] for s in decide["suggestions"]] == ["Testeable"]

    # La nueva versión resuelve la sugerencia: la reevaluación de INVEST sale limpia.
    chat.llm.invest = {"criteria": [{**c, "status": "cumple", "suggestion": None} for c in chat.llm.invest["criteria"]]}
    revised = chat.say(type="decide_suggestions", decisions={"Testeable": "approved"})
    assert revised["intent"] == "ajustar" and revised["flow"]["story"]["version"] == 2
    assert "reevalué INVEST sobre la v2" in revised["reply"] and step(revised["flow"], "invest")["version"] == 2

    approved = chat.say(type="approve", artifact="story")
    assert approved["intent"] == "aprobar" and approved["flow"]["story"]["approved"] and approved["resume"].startswith("Seguimos con la HU")

    matrix = chat.say(type="run", goal="matrix")
    assert matrix["artifact"]["kind"] == "matrix" and "casos" in matrix["reply"]
    assert chat.say(type="approve", artifact="matrix")["intent"] == "aprobar"
    exported = chat.say(type="export_matrix")
    assert exported["download"]["filename"].endswith(".xlsx")

    risk = chat.say("Evalúa el riesgo de la historia")
    assert risk["artifact"]["kind"] == "risk" and "alto" in risk["reply"]

    asks_repo = chat.say("Genera los scripts de automatización")
    assert asks_repo["intent"] == "aclarar" and "repositorio" in asks_repo["reply"] and "stack" in asks_repo["reply"]
    framework_choice = next(a for a in asks_repo["actions"] if a["type"] == "input")["params"][0]
    assert framework_choice["name"] == "framework" and "playwright" in framework_choice["choices"]
    scripts = chat.say("El repositorio es nissan-qa/valkiria-automation y el stack es RestAssured para la API")
    assert scripts["artifact"]["kind"] == "automation" and "RestAssured (api)" in scripts["reply"] and "pull request" in scripts["reply"]
    files = scripts["artifact"]["data"]["batches"][0]["scripts"]
    assert all(name.endswith("Test.java") for name in files) and all("given().baseUri" in code for code in files.values())
    early = chat.say("Ejecuta los scripts")
    assert early["intent"] == "aclarar" and "verifiques" in early["reply"]  # HU-010 exige la verificación humana antes
    data = chat.say("Valida los datos de la historia")
    queries = data["artifact"]["data"]["queries"]
    assert data["artifact"]["kind"] == "data_validation" and [q["status"] for q in queries] == ["lista", "lista", "bloqueada"]
    assert queries[0]["case_id"] == "TC-AC-01-P" and queries[1]["expect"] == "empty"
    assert queries[0]["preview_rows"] >= 1 and queries[0]["preview_matches"] and queries[1]["preview_matches"]  # lo que devuelve hoy, visible antes de aprobar
    assert chat.say(type="approve", artifact="data_validation")["intent"] == "aprobar"  # verificación humana de las consultas
    assert chat.say(type="approve", artifact="automation")["intent"] == "aprobar"  # verificación humana antes del PR
    execution = chat.say("Ejecuta los scripts")
    summary = execution["artifact"]["data"]["summary"]
    assert execution["artifact"]["kind"] == "execution" and summary["api"] == len(matrix["artifact"]["data"]["cases"]) and summary["database"] == 2
    assert execution["artifact"]["data"]["report_id"] and "consulta(s) de datos (HU-011)" in execution["reply"]
    db_results = [r for r in execution["artifact"]["data"]["results"] if r["kind"] == "database"]
    assert all(r["result"] == "pass" for r in db_results)  # la app sintética cumple ambas reglas de datos
    assert all(r["status"] is not None for r in execution["artifact"]["data"]["results"])  # llamadas reales a la app sintética
    report = chat.api.get(f"/v1/reports/{execution['artifact']['data']['report_id']}/download")
    assert report.status_code == 200 and report.headers["content-type"] == "application/pdf"

    pipeline = chat.say("Genera el pipeline de Azure DevOps")
    assert pipeline["artifact"]["kind"] == "pipeline" and "mvn -B test" in pipeline["artifact"]["data"]["yaml"]
    chat.say(type="approve", artifact="pipeline")

    perf = chat.say("Diseña la prueba de performance con 200 usuarios durante 10 minutos y SLA de 800 ms")
    assert perf["artifact"]["kind"] == "performance_design" and perf["artifact"]["data"]["users"] == 200 and perf["artifact"]["data"]["duration_seconds"] == 600
    chat.say(type="approve", artifact="performance_design")

    work_item = chat.say("Prepara el work item de Azure DevOps en el proyecto NissanQA")
    assert work_item["artifact"]["kind"] == "azure_work_item" and work_item["artifact"]["data"]["project"] == "NissanQA"
    chat.say(type="approve", artifact="azure_work_item")

    done = chat.say("¿Qué sigue?")
    states = {s["key"]: s["state"] for s in done["flow"]["steps"]}
    print("ESTADOS", states, done["reply"])
    assert all(states[k] == "done" for k in ("story", "invest", "story_revision", "approve_story", "matrix", "approve_matrix", "risk", "automation", "data_validation", "execution",
                                             "pipeline", "performance_design", "azure_work_item"))
    assert "completo" in done["reply"]


def test_every_modification_creates_a_new_version():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    v2 = chat.say("Mejora la historia")
    v3 = chat.say("Agrega un criterio para cuando el concesionario está inactivo")
    assert v2["intent"] == v3["intent"] == "ajustar"
    assert (v2["flow"]["story"]["version"], v3["flow"]["story"]["version"]) == (2, 3)
    assert [v["version"] for v in v3["flow"]["story"]["versions"]] == [1, 2, 3]
    assert v3["changes"]["criteria_added"] >= 1 and "versión 3" in v3["reply"]


def test_modifying_the_story_marks_later_work_as_outdated():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say(type="run", goal="matrix")
    changed = chat.say("Mejora la historia")
    assert "desactualizad" in changed["reply"] or step(changed["flow"], "matrix")["version"] == 2


def test_questions_mid_flow_are_answered_first_and_the_flow_resumes():
    llm = ScriptedLLM()
    chat = Chat(llm)
    started = chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    llm.assistant_script = [{"accion": "responder", "respuesta": "Una matriz de pruebas relaciona criterios con casos."}]
    answer = chat.say("¿Qué es una matriz de pruebas?")
    assert answer["intent"] == "responder" and "matrix" not in [k for k in answer["flow"]["steps"] if k["state"] == "done"]
    assert answer["flow"]["workflow_id"] == started["flow"]["workflow_id"] and answer["resume"].startswith("Seguimos con la HU")
    assert step(answer["flow"], "matrix")["state"] == "pending"  # la pregunta no ejecutó el paso


def test_courtesy_mid_flow_never_restarts_the_story():
    llm = ScriptedLLM()
    chat = Chat(llm)
    started = chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    llm.smalltalk_reply = "¡De nada! Seguimos cuando quieras."
    thanks = chat.say("¡Gracias, muy bien!")
    assert thanks["intent"] == "conversar" and thanks["flow"]["workflow_id"] == started["flow"]["workflow_id"]
    assert thanks["flow"]["story"]["version"] == 1 and thanks["flow"]["story"]["title"] == STORY["title"]


def test_a_requirement_like_message_asks_instead_of_restarting():
    chat = Chat()
    started = chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    doubt = chat.say("Los gerentes necesitan ver el reporte de ventas mensual por región para decidir promociones")
    assert doubt["intent"] == "aclarar" and doubt["flow"]["workflow_id"] == started["flow"]["workflow_id"]
    assert [a["type"] for a in doubt["actions"]] == ["edit_story", "new_story", "resume"]
    adjusted = chat.say("Ajusta la actual")
    assert adjusted["intent"] == "ajustar" and adjusted["flow"]["story"]["version"] == 2

    chat.say("Los gerentes necesitan ver el reporte de ventas mensual por región para decidir promociones")
    fresh = chat.say("Nueva")
    assert fresh["intent"] == "crear" and fresh["flow"]["workflow_id"] != started["flow"]["workflow_id"]


def test_only_an_explicit_request_starts_a_new_story():
    chat = Chat()
    first = chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    new = chat.say("Empecemos una nueva historia: que los clientes agenden citas de servicio desde el portal")
    assert new["intent"] == "crear" and new["flow"]["workflow_id"] != first["flow"]["workflow_id"]
    assert "dejo guardada" in new["reply"]
    ask = chat.say("Nueva historia")
    assert "nuevo requerimiento" in ask["reply"] and ask["flow"]["workflow_id"] is None


def test_continue_runs_the_recommended_next_step():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    nxt = chat.say("Siguiente")
    assert nxt["artifact"]["kind"] == "invest"


def test_steps_that_need_approval_explain_the_dependency():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    risk = chat.say("Evalúa el riesgo")
    assert risk["intent"] == "aclarar" and "apruebes la HU v1" in risk["reply"]
    assert not step(risk["flow"], "risk")["ready"]


def test_assumptions_are_confirmed_before_approving():
    llm = ScriptedLLM()
    llm.story = {**STORY, "assumptions": ["El asesor ya inició sesión"]}
    chat = Chat(llm)
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    ask = chat.say("Apruebo la historia")
    assert ask["intent"] == "aclarar" and "El asesor ya inició sesión" in ask["reply"]
    ok = chat.say("Confirmo los supuestos y apruebo la historia")
    assert ok["intent"] == "aprobar" and ok["flow"]["story"]["approved"]


def test_policy_requests_mid_flow_are_refused_and_the_flow_continues():
    chat = Chat()
    started = chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    refusal = chat.say("Despliega esta historia en producción")
    assert refusal["intent"] == "no_puedo" and refusal["flow"]["workflow_id"] == started["flow"]["workflow_id"] and refusal["resume"]


def test_broad_requirements_are_split_and_the_po_chooses():
    llm = ScriptedLLM()
    llm.split_response = {"split": [{"title": "Consultar mis autos"}, {"title": "Agendar cita de servicio"}]}
    chat = Chat(llm)
    split = chat.say("Necesito un portal donde los clientes vean sus autos, agenden citas, paguen en línea y chateen con asesores")
    assert split["intent"] == "dividir" and [a["type"] for a in split["actions"]] == ["choose_split", "choose_split"]
    chosen = chat.say(type="choose_split", index=1)
    assert chosen["intent"] == "crear" and chosen["flow"]["story"]["version"] == 1


def test_model_failures_keep_the_flow_and_allow_retry():
    llm = ScriptedLLM()
    chat = Chat(llm)
    started = chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    llm.failures["edit"] = -1
    llm.edit_response = None
    original = llm.generate_json

    async def failing(*, system, user, schema):
        from valkiria.providers.openai_compatible import LLMProviderError
        from workflow_fakes import prompt_kind
        if prompt_kind(system) == "edit":
            raise LLMProviderError("llm_timeout", "timeout")
        return await original(system=system, user=user, schema=schema)

    llm.generate_json = failing
    failed = chat.say("Mejora la historia")
    assert failed["intent"] == "error" and failed["retryable"]
    llm.generate_json = original
    again = chat.say("Mejora la historia")
    assert again["flow"]["workflow_id"] == started["flow"]["workflow_id"] and again["flow"]["story"]["version"] == 2


def test_continue_never_approves_on_behalf_of_the_user():
    # Caso real en vivo: "¿Qué sigue?" aprobó el Work Item. RT-02 exige confirmación explícita.
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say(type="run", goal="invest")
    chat.say(type="decide_suggestions", decisions={"Testeable": "rejected"})
    asked = chat.say("¿Qué sigue?")
    assert asked["intent"] == "aclarar" and "aprobación" in asked["reply"] and not asked["flow"]["story"]["approved"]
    assert asked["actions"][0]["type"] == "approve"


def test_typed_resume_returns_to_the_flow():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say("Los gerentes necesitan ver el reporte de ventas mensual por región para decidir promociones")
    back = chat.say("Ninguna, sigamos donde íbamos")
    assert back["intent"] == "conversar" and back["reply"].startswith("Seguimos con la HU") and back["flow"]["story"]["version"] == 1


def test_questions_about_the_current_story_use_the_flow_state():
    llm = ScriptedLLM()
    chat = Chat(llm)
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say(type="run", goal="matrix")
    llm.assistant_script = [{"accion": "responder", "respuesta": "No sé."}] * 2
    answer = chat.say("¿Cuántos casos tiene la matriz?")
    assert "matriz v1: 6 casos" in answer["reply"] and answer["assistant"]["tools_used"] == ["estado_flujo"]


def test_panel_tools_use_the_conversation_context():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say(type="run", goal="matrix")
    chat.say("Genera los scripts en RestAssured para la API en el repositorio nissan-qa/api-tests")
    bd = chat.say(type="tool", name="herramienta_bd")
    assert "java" in bd["reply"] and "JDBC" in bd["reply"]  # el lenguaje sale del stack elegido (RestAssured → Java)
    memory = chat.say(type="tool", name="buscar_memoria")
    assert memory["intent"] == "responder" and memory["resume"]
    asks = chat.say(type="tool", name="analizar_script_sql")
    assert asks["actions"][0]["type"] == "tool_input" and asks["actions"][0]["params"][0]["multiline"]
    analysis = chat.say(type="tool_input", name="analizar_script_sql", params={"script": "DELETE FROM vehicles"})
    assert "NO es seguro" in analysis["reply"]


def test_database_validation_is_derived_from_the_story_and_read_only():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    result = chat.say(type="tool", name="validar_bd")
    queries = result["artifact"]["data"]["queries"]
    assert result["artifact"]["kind"] == "database_validation" and queries[0]["status"] == "completed" and queries[0]["row_count"] >= 1
    assert queries[2]["status"] == "bloqueada"  # la mutación nunca se ejecuta (HU-011)
    assert queries[0]["report_id"]  # evidencia descargable


def test_high_risk_suggests_a_performance_test_without_forcing_it():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say(type="approve", artifact="story")
    risk = chat.say("Evalúa el riesgo")
    assert "sin ser obligatorio" in risk["reply"] and "performance" in risk["reply"]
    labels = [a["label"] for a in risk["actions"]]
    assert {"Prueba de carga", "Prueba de estrés", "Prueba de picos"} <= set(labels)
    stress = next(a for a in risk["actions"] if a["label"] == "Prueba de estrés")
    designed = chat.say(type="input", goal="performance_design", preset=stress["preset"],
                        params={"performance_users": 500, "performance_duration_seconds": 300, "performance_sla_ms": 1000})
    assert designed["artifact"]["data"]["scenario_type"] == "stress"


def test_web_scripts_explain_why_they_cannot_run_here_and_offer_an_api_stack():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say(type="run", goal="matrix")
    chat.say("Genera los scripts en Playwright para web en el repositorio nissan-qa/web-tests")
    chat.say(type="approve", artifact="automation")
    web = chat.say("Ejecuta los scripts")
    assert web["intent"] == "aclarar" and "sin interfaz web" in web["reply"]
    regenerate = web["actions"][0]
    assert regenerate["goal"] == "automation" and regenerate["preset"] == {"platform": "api"}
    api = chat.say(type="input", goal="automation", preset=regenerate["preset"], params={"framework": "postman-newman"})
    assert api["artifact"]["data"]["platform"] == "api" and api["artifact"]["version"] == 2


def test_story_with_too_many_criteria_offers_a_way_forward():
    # Caso real en vivo: la HU quedó con 12 criterios y el flujo no ofrecía salida.
    llm = ScriptedLLM()
    llm.story = {**STORY, "acceptance_criteria": [{"id": f"AC-{i:02d}", "text": f"Dado {i}, cuando consulto, entonces veo {i}"} for i in range(1, 13)]}
    chat = Chat(llm)
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    blocked = chat.say("Genera la matriz de pruebas")
    assert blocked["intent"] == "aclarar" and "12 criterios" in blocked["reply"]
    assert [a["type"] for a in blocked["actions"]] == ["edit_story", "new_story"]


def test_new_invest_suggestions_are_optional_once_the_story_is_approved():
    # Caso real en vivo: con la HU aprobada, "¿qué sigue?" volvía a las sugerencias INVEST.
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say(type="run", goal="invest")
    chat.say(type="approve", artifact="story")
    nxt = chat.say("¿Qué sigue?")
    assert nxt["artifact"]["kind"] == "matrix"
    assert any(a["label"] == "Revisar nuevas sugerencias INVEST (opcional)" for a in nxt["flow"]["next"])


def test_data_validation_is_suggested_when_the_story_has_data_rules():
    chat = Chat()  # la HU de prueba tiene la regla "Solo vehículos con stock"
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    after_matrix = chat.say(type="run", goal="matrix")
    suggested = next(a for a in after_matrix["flow"]["next"] if a.get("goal") == "data_validation")
    assert suggested["label"].endswith("(sugerida)") and "reglas de datos" in suggested["why"]


def test_execution_can_run_only_verified_data_queries():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say(type="run", goal="matrix")
    chat.say(type="run", goal="data_validation")
    chat.say(type="approve", artifact="data_validation")
    run = chat.say("Ejecuta la validación")
    summary = run["artifact"]["data"]["summary"]
    assert run["artifact"]["kind"] == "execution" and summary == {"total": 2, "passed": 2, "failed": 0, "errors": 0, "api": 0, "database": 2, "web": 0}


def test_web_scripts_with_data_queries_run_the_data_and_explain_the_web_part():
    chat = Chat()
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say(type="run", goal="matrix")
    chat.say("Genera los scripts en Playwright para web en el repositorio nissan-qa/web-tests")
    chat.say(type="approve", artifact="automation")
    chat.say(type="run", goal="data_validation")
    chat.say(type="approve", artifact="data_validation")
    run = chat.say("Ejecuta las pruebas")
    assert run["artifact"]["data"]["summary"]["database"] == 2 and "Los scripts web no se ejecutaron" in run["reply"]


def test_invalid_data_queries_are_corrected_before_asking_for_approval():
    # Caso real en vivo: una consulta del modelo falló en la base sintética (columna inexistente).
    llm = ScriptedLLM()
    original = llm.generate_json
    calls = {"n": 0}

    async def sql_with_bad_column(*, system, user, schema):
        from workflow_fakes import prompt_kind
        if prompt_kind(system) == "sql" and calls["n"] == 0:
            calls["n"] += 1
            return {"queries": [{"purpose": "Stock por concesionario", "criterion_id": "AC-01", "case_id": "TC-AC-01-P", "expect": "rows",
                                 "sql": "SELECT dealer_name, stock FROM vehicles LIMIT 20"}]}
        return await original(system=system, user=user, schema=schema)

    llm.generate_json = sql_with_bad_column
    chat = Chat(llm)
    chat.say("Necesito que los asesores consulten vehículos disponibles por concesionario")
    chat.say(type="run", goal="matrix")
    data = chat.say("Valida los datos de la historia")
    query = data["artifact"]["data"]["queries"][0]
    assert query["status"] == "lista" and query.get("corrected") and "dealer_name" not in query["sql"]


def test_flow_steps_without_a_story_ask_for_the_requirement():
    # Caso real en vivo: sin HU, "Genera los scripts…" terminó creando una "historia" sobre scripts.
    chat = Chat()
    for message in ("Genera la matriz de pruebas", "Genera los scripts en Postman para la API en el repositorio nissan-qa/api-tests", "Valida los datos de la historia"):
        reply = chat.say(message)
        assert reply["intent"] == "aclarar" and "primero necesito una historia" in reply["reply"] and reply["flow"]["workflow_id"] is None
