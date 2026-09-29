"""El conductor de la conversación: todo lo que Valkiria puede hacer desde el chat, sin romper el flujo."""

from fastapi.testclient import TestClient

from valkiria.api.app import create_app
from workflow_fakes import STORY, ScriptedLLM


class Chat:
    def __init__(self, llm=None):
        self.llm = llm or ScriptedLLM()
        self.api = TestClient(create_app(llm=self.llm))
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
    assert asks_repo["intent"] == "aclarar" and "repositorio" in asks_repo["reply"]
    scripts = chat.say("El repositorio es nissan-qa/valkiria-automation")
    assert scripts["artifact"]["kind"] == "automation" and "pull request" in scripts["reply"]

    pipeline = chat.say("Genera el pipeline de Azure DevOps")
    assert pipeline["artifact"]["kind"] == "pipeline"

    perf = chat.say("Diseña la prueba de performance con 200 usuarios durante 10 minutos y SLA de 800 ms")
    assert perf["artifact"]["kind"] == "performance_design" and perf["artifact"]["data"]["users"] == 200 and perf["artifact"]["data"]["duration_seconds"] == 600

    work_item = chat.say("Prepara el work item de Azure DevOps en el proyecto NissanQA")
    assert work_item["artifact"]["kind"] == "azure_work_item" and work_item["artifact"]["data"]["project"] == "NissanQA"
    chat.say(type="approve", artifact="azure_work_item")

    done = chat.say("¿Qué sigue?")
    states = {s["key"]: s["state"] for s in done["flow"]["steps"]}
    print("ESTADOS", states, done["reply"])
    assert all(states[k] == "done" for k in ("story", "invest", "story_revision", "approve_story", "matrix", "approve_matrix", "risk", "automation",
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
    llm.chat_response = {"intent": "crear", "reply": "¡De nada! Seguimos cuando quieras.", "assumptions": [], "story": {**STORY, "title": "Otra HU"}}
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
    llm.chat_response = {"intent": "crear", "reply": "Redacté la historia.", "assumptions": ["El asesor ya inició sesión"], "story": STORY}
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
    llm.chat_response = {"intent": "dividir", "reply": "Te propongo dividirlo.", "assumptions": [], "story": None,
                         "split": [{"title": "Consultar mis autos"}, {"title": "Agendar cita de servicio"}]}
    chat = Chat(llm)
    split = chat.say("Necesito un portal donde los clientes vean sus autos, agenden citas, paguen en línea y chateen con asesores")
    assert split["intent"] == "dividir" and [a["type"] for a in split["actions"]] == ["choose_split", "choose_split"]
    llm.chat_response = None
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
