"""Reglas de negocio de las HU que dependen de la salida del modelo y que el código garantiza (épica v3.0)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from valkiria.api.app import create_app
from valkiria.application.prompts import PROMPT_VERSION
from valkiria.application.use_cases import ValkiriaService, _normalize_risk, risk_level
from valkiria.infrastructure.memory import (
    InMemoryAudit,
    InMemoryMetrics,
    InMemoryStories,
)
from valkiria.providers.openai_compatible import OpenAICompatibleLLM
from valkiria.workflow.engine import WorkflowConflict, WorkflowEngine
from valkiria.workflow.state import InMemoryWorkflowStore
from workflow_fakes import STORY, ScriptedLLM


def engine_for(llm):
    async def no_sleep(_):
        return None

    return WorkflowEngine(InMemoryWorkflowStore(), ValkiriaService(llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories()), sleep=no_sleep)


# --- HU-005: el nivel se deriva de la puntuación -----------------------------------------------------

@pytest.mark.parametrize("total, level", [(3, "low"), (6, "low"), (7, "medium"), (11, "medium"), (12, "high"), (15, "high")])
def test_risk_level_follows_the_score_bands(total, level):
    assert risk_level(total) == level


def test_model_level_is_overridden_by_the_scores():
    risk = _normalize_risk({"scores": {"complejidad": 5, "dependencias": 4, "criticidad": 4}, "level": "low", "justification": "j", "mitigation": "m"})
    assert risk["level"] == "high" and risk["score_total"] == 13
    clamped = _normalize_risk({"complejidad": 9, "dependencias": 0, "criticidad": "3", "level": "medium", "justification": "j", "mitigation": "m"})
    assert clamped["scores"] == {"complejidad": 5, "dependencias": 1, "criticidad": 3} and clamped["level"] == "medium"
    assert "scores" not in _normalize_risk({"level": "alto", "justification": "j", "mitigation": "m"})


# --- HU-003B: supuestos confirmados antes de aprobar y requerimientos amplios ---------------------------

async def test_story_assumptions_must_be_confirmed_before_approval():
    llm = ScriptedLLM()
    llm.story = {**STORY, "assumptions": ["El asesor ya inició sesión"], "split": ["Reservar un vehículo"]}
    engine = engine_for(llm)
    state, plan = await engine.start(request=None, goals=["story", "risk"], params={"requirement": "Consultar y reservar vehículos"}, actor="po")
    record = state.artifacts["story"]
    assert record.assumptions == ["El asesor ya inició sesión"]
    assert any("dividirlo" in w and "Reservar un vehículo" in w for w in record.warnings)
    assert plan.next_actions(state)[0]["confirm_assumptions"] == ["El asesor ya inició sesión"]
    with pytest.raises(WorkflowConflict, match="assumptions_confirmation_required"):
        await engine.approve(state.id, artifact="story", version=1, content_hash=record.content_hash, decision="approved", actor="po")
    state, plan = await engine.approve(state.id, artifact="story", version=1, content_hash=record.content_hash, decision="approved", actor="po", assumptions_confirmed=True)
    assert state.artifacts["story"].approval.details["assumptions_confirmed"] == ["El asesor ya inició sesión"] and plan.status == "completed"


async def test_broad_requirements_are_proposed_as_a_split_in_chat():
    llm = ScriptedLLM()
    llm.chat_response = {"intent": "dividir", "reply": "Te propongo dividirlo en tres historias.", "assumptions": [], "story": None,
                         "split": [{"title": "Consultar mis autos", "description": "d"}, {"title": "Agendar cita de servicio"}, "Pagar en línea"]}
    service = ValkiriaService(llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories())
    result = await service.converse("Un portal para ver autos, agendar citas y pagar", [], None, "po")
    assert result["intent"] == "dividir" and result["story"] is None
    assert [item["title"] for item in result["split"]] == ["Consultar mis autos", "Agendar cita de servicio", "Pagar en línea"]
    assert result["reply"].endswith("?")  # el PO elige cuáles crear

    llm.chat_response = {"intent": "dividir", "reply": "", "story": STORY, "split": ["Solo una"]}
    single = await service.converse("Consultar vehículos", [], None, "po")
    assert single["intent"] == "crear" and single["story"]  # una sola propuesta no es una división


# --- RT-06: modelo y versión del prompt en cada artefacto ------------------------------------------------

async def test_artifacts_record_model_and_prompt_version():
    state, _ = await engine_for(ScriptedLLM()).start(request=None, goals=["story"], params={"requirement": "Consultar vehículos"}, actor="po")
    record = state.artifacts["story"]
    assert record.model == "llama3.2:3b-instruct-q4_K_M" and record.prompt_version == PROMPT_VERSION


# --- RT-04: la conversación no se rompe ------------------------------------------------------------------

def test_chat_answers_politely_with_trace_id_when_the_model_fails():
    llm = ScriptedLLM()
    llm.failures["chat"] = -1
    api = TestClient(create_app(llm=llm))
    response = api.post("/v1/chat", json={"message": "Necesito que los asesores vean el stock por agencia"}, headers={"X-Trace-Id": "trace-rt04"})
    body = response.json()
    assert response.status_code == 200 and body["intent"] == "error" and body["retryable"] is True
    assert body["reply"].startswith("Disculpa") and "trace-rt04" in body["reply"]


def test_courtesy_is_answered_conversationally_not_with_tools():
    llm = ScriptedLLM()
    llm.chat_response = {"intent": "conversar", "reply": "¡Hola! Qué gusto saludarte. ¿En qué historia trabajamos hoy?", "assumptions": [], "story": None, "split": []}
    api = TestClient(create_app(llm=llm))
    for message in ("hola", "¡Gracias, quedó muy bien!", "No entiendes nada, eso no es lo que pedí"):
        body = api.post("/v1/chat", json={"message": message}).json()
        assert body["intent"] == "conversar" and "assistant" not in body
    assert "assistant" not in llm.calls


# --- RT-05: sin credenciales ni correos hacia el modelo ---------------------------------------------------

async def test_sensitive_data_is_redacted_before_reaching_the_model(monkeypatch):
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    llm = OpenAICompatibleLLM("http://ollama.test/v1", "m")
    await llm.generate_json(system="s", user="La API usa password=Secreta1 y el contacto es juan@nissan.test", schema={})
    assert "Secreta1" not in sent["body"] and "juan@nissan.test" not in sent["body"]
    assert llm.timeout_seconds == 60  # RT-04


# --- HU-002: sugerencia accionable en todo criterio no cumplido -------------------------------------------

async def test_missing_invest_suggestions_are_completed():
    # Caso real (v2): Llama 3.2 omite "suggestion" en los criterios parcial o no_cumple.
    llm = ScriptedLLM()
    llm.invest = {"criteria": [{"name": n, "status": "cumple", "justification": "Cita"} for n in ("Independiente", "Negociable", "Valiosa", "Estimable", "Testeable")]
                  + [{"name": "Pequeña", "status": "no_cumple", "justification": "Mezcla consultar y reservar"}]}
    from valkiria.domain.models import UserStory
    service = ValkiriaService(llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories())
    evaluation = await service.evaluate_invest(UserStory.model_validate(STORY), "po")
    by_name = {c.name: c for c in evaluation.criteria}
    assert by_name["Pequeña"].suggestion == "Agregar un criterio para stock igual a 1."
    assert by_name["Valiosa"].suggestion is None and llm.calls.count("suggestion") == 1


# --- Regresiones de la conversación en vivo con Llama 3.2 ---------------------------------------------------

BROAD = "Necesito un portal donde los clientes vean sus autos, agenden citas, paguen en línea y chateen con asesores"


async def test_broad_requirement_is_split_even_when_the_model_says_crear():
    # En vivo, con una HU en curso, el modelo eligió "crear" para un requerimiento con 4 funcionalidades.
    from valkiria.application.use_cases import looks_broad
    from valkiria.domain.models import UserStory
    assert looks_broad(BROAD) and not looks_broad("Necesito que los asesores vean el stock por agencia para cerrar ventas más rápido")
    llm = ScriptedLLM()
    llm.chat_response = {"intent": "crear", "reply": "Redacté la historia.", "assumptions": [], "story": STORY}
    service = ValkiriaService(llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories())
    result = await service.converse(BROAD, [], UserStory.model_validate(STORY), "po")
    assert result["intent"] == "dividir" and result["story"] is None and len(result["split"]) == 3 and result["reply"].endswith("?")


async def test_a_copied_current_story_is_not_returned_as_the_new_one():
    # En vivo, el modelo devolvió la HU en curso como si fuera la nueva.
    from valkiria.domain.models import UserStory
    llm = ScriptedLLM()
    current = UserStory.model_validate(STORY)
    llm.chat_response = {"intent": "crear", "reply": "Redacté la historia.", "assumptions": [], "story": STORY}
    llm.story = {**STORY, "title": "Agendar cita de servicio"}
    service = ValkiriaService(llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories())
    result = await service.converse("Quiero agendar citas de servicio desde el portal", [], current, "po")
    assert result["story"]["title"] == "Agendar cita de servicio"
    assert "Historia actual" not in llm.prompts[-1][1]  # se redactó solo con el requerimiento nuevo


async def test_copied_example_capability_and_refusal_drafts_become_the_standard_polite_answer():
    from test_assistant import assistant_for
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "no_puedo", "falta": "traducir documentos"}] * 2  # se revisa una vez antes de aceptar la negativa
    answer = await assistant_for(llm).ask("¿Me recomiendas un restaurante?")
    assert "traducir" not in answer.answer and answer.answer.startswith("Lo siento, eso no está dentro de lo que puedo hacer.")
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "responder", "respuesta": "No, no puedo recomendar un restaurante."}]
    refusal = await assistant_for(llm).ask("¿Me recomiendas un restaurante?")
    assert refusal.status == "unsupported" and "Lo que sí puedo hacer" in refusal.answer and "pruebas de BD:" not in refusal.answer


async def test_each_task_has_an_output_token_budget(monkeypatch):
    from valkiria.application import prompts
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    llm = OpenAICompatibleLLM("http://ollama.test/v1", "m", token_budget=prompts.token_budget)
    await llm.generate_json(system=prompts.ASSISTANT_SYSTEM.format(max_steps=4, catalog=""), user="u", schema={})
    assert '"max_tokens":500' in sent["body"]
    assert prompts.token_budget(prompts.MATRIX_SYSTEM) == 3500


async def test_empty_model_split_still_triggers_the_focused_split():
    # Caso real en vivo: el modelo eligió "dividir" con la lista vacía y el código lo degradaba a "crear" copiando la HU en curso.
    from valkiria.domain.models import UserStory
    llm = ScriptedLLM()
    llm.chat_response = {"intent": "dividir", "reply": "Es amplio.", "assumptions": [], "story": None, "split": []}
    service = ValkiriaService(llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories())
    result = await service.converse(BROAD, [], UserStory.model_validate(STORY), "po")
    assert result["intent"] == "dividir" and len(result["split"]) == 3 and "split" in llm.calls
