import httpx
import pytest
from fastapi.testclient import TestClient

from valkiria.api.app import create_app
from valkiria.application.use_cases import ValkiriaService
from valkiria.assistant.agent import UNGROUNDED_NOTE, ReasoningAssistant, faithful
from valkiria.assistant.catalog import build_toolbox
from valkiria.assistant.routing import route_message
from valkiria.assistant.tools import Summary, ToolContext
from valkiria.infrastructure.memory import (
    InMemoryAudit,
    InMemoryMetrics,
    InMemoryStories,
)
from workflow_fakes import ScriptedLLM

INVENTORY = {"/vehicles": {"items": [{"vehicle_id": 1, "model": "Sentra", "stock": 10}, {"vehicle_id": 3, "model": "Versa", "stock": 3}]},
             "/inventory": {"items": [{"inventory_id": 1, "vehicle_id": 1, "model": "Sentra", "dealer_id": 1, "available": True}]},
             "/dealers": {"items": [{"dealer_id": 1, "name": "Nissan Apodaca Sintético", "region": "Nuevo León", "active": True},
                                    {"dealer_id": 2, "name": "Nissan Centro Sintético", "region": "Nuevo León", "active": False}]}}


def synthetic_transport():
    return httpx.MockTransport(lambda request: httpx.Response(200, json=INVENTORY[request.url.path]) if request.url.path in INVENTORY else httpx.Response(404))


def assistant_for(llm, **kwargs):
    service = ValkiriaService(llm, InMemoryAudit(), InMemoryMetrics(), InMemoryStories())
    toolbox = build_toolbox(service=service, memory=None, workflows=None, synthetic_app_base_url="http://synthetic.test", transport=synthetic_transport())
    return ReasoningAssistant(llm, toolbox, **kwargs)


@pytest.mark.parametrize("text, route", [
    ("Consultar vehículos Nissan disponibles por concesionario", "story"),
    ("Como asesor quiero ver el stock por agencia", "story"),
    ("Agrega un criterio para stock cero", "story"),
    ("¿Puedes agregar un criterio de borde?", "story"),
    ("¿Qué vehículos hay en inventario?", "assistant"),
    ("¿Qué es INVEST?", "assistant"),
    ("Muestra los concesionarios activos", "assistant"),
    ("Genera el pipeline de Azure", "assistant"),
    ("Despliega esto en producción", "assistant"),
    ("Reserva un vuelo a Cancún", "assistant"),
])
def test_routing_separates_story_work_from_open_requests(text, route):
    assert route_message(text) == route


async def test_answers_with_real_tool_observations():
    llm = ScriptedLLM()
    llm.assistant_script = [
        {"razon": "Necesito el inventario", "accion": "usar_herramienta", "herramienta": "inventario_nissan", "argumentos": {"solo_disponibles": True}},
        {"razon": "Ya tengo los datos", "accion": "responder", "respuesta": "Hay Sentra (10) y Versa (3) según inventario_nissan."},
    ]
    answer = await assistant_for(llm).ask("¿Qué vehículos hay disponibles?")
    assert answer.status == "answered" and answer.grounded and answer.tools_used == ["inventario_nissan"]
    assert "Sentra" in answer.steps[0].observation
    assert "Sentra" in llm.prompts[1][1]  # la observación real llega al modelo en el segundo paso


async def test_invalid_tool_and_arguments_are_reported_back_to_the_model():
    llm = ScriptedLLM()
    llm.assistant_script = [
        {"accion": "usar_herramienta", "herramienta": "borrar_base", "argumentos": {}},
        {"accion": "usar_herramienta", "herramienta": "herramienta_bd", "argumentos": {"motor": "mongodb"}},
        {"accion": "usar_herramienta", "herramienta": "herramienta_bd", "argumentos": {"motor": "oracle"}},
        {"accion": "responder", "respuesta": "Para Oracle usa la herramienta sugerida."},
    ]
    answer = await assistant_for(llm).ask("¿Qué herramienta uso para probar la base de datos del inventario?")
    assert "no existe" in answer.steps[0].error
    assert "debe ser uno de" in answer.steps[1].error
    assert answer.steps[2].observation and answer.tools_used == ["herramienta_bd"]
    assert "no existe" in llm.prompts[1][1]


async def test_declares_honestly_what_it_cannot_do_and_lists_alternatives():
    llm = ScriptedLLM()
    llm.assistant_script = [{"razon": "Ninguna herramienta reserva viajes", "accion": "no_puedo", "falta": "reservar vuelos"}]
    answer = await assistant_for(llm).ask("Reserva un vuelo a Cancún")
    assert answer.status == "unsupported" and answer.missing_capability == "reservar vuelos"
    assert answer.answer.startswith("Lo siento, no tengo la capacidad de reservar vuelos.")
    assert "Lo que sí puedo hacer:" in answer.answer and "Inventario Nissan" in answer.answer
    assert answer.capabilities["limites"]


async def test_refusing_after_getting_data_goes_to_verified_composition():
    # Caso real con Llama 3.2: consultó el inventario y luego dijo que no podía.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "inventario_nissan", "argumentos": {}},
                            {"accion": "no_puedo", "falta": "la capacidad de obtener datos de inventario"}]
    llm.compose_script = [{"respuesta": "Hay Sentra (10) y Versa (3) disponibles."}]
    answer = await assistant_for(llm).ask("¿Qué vehículos hay en inventario?")
    assert answer.status == "answered" and answer.grounded and answer.mode == "llm" and "Sentra" in answer.answer
    assert "Sentra (stock 10)" in llm.prompts[-1][1]  # la redacción recibe la conclusión calculada, no datos crudos

    stubborn = ScriptedLLM()
    stubborn.assistant_script = [{"accion": "usar_herramienta", "herramienta": "inventario_nissan", "argumentos": {}}, {"accion": "no_puedo"}]
    stubborn.compose_script = [{"respuesta": "No puedo consultar el inventario."}]
    kept = await assistant_for(stubborn).ask("¿Qué vehículos hay en inventario?")
    assert kept.status == "answered" and kept.mode == "tool" and kept.answer.startswith("Vehículos disponibles: Sentra (stock 10), Versa (stock 3).")


async def test_unfaithful_drafts_are_replaced_by_the_verified_conclusion():
    # Caso real: "Los concesionarios activos son aquellos listados como activos" (sin datos) y un inactivo presentado como activo.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "concesionarios_nissan", "argumentos": {}},
                            {"accion": "responder", "respuesta": "Los concesionarios activos son aquellos listados como activos."}]
    llm.compose_script = [{"respuesta": "Activos: Apodaca y Centro."}]
    answer = await assistant_for(llm).ask("¿Qué concesionarios están activos?")
    assert answer.mode == "tool" and answer.answer.startswith("Concesionarios activos: Nissan Apodaca Sintético")
    assert "Inactivos: Nissan Centro Sintético" in answer.answer


def test_faithfulness_rules():
    summary = Summary("Activos: Apodaca. Inactivos: Centro.", ("Apodaca",), (("Centro", "inactiv"),))
    assert faithful("El concesionario activo es Apodaca; Centro está inactivo.", [summary])
    assert not faithful("Los activos son Apodaca y Centro.", [summary])
    assert not faithful("Los concesionarios activos son los listados.", [summary])
    assert not faithful("No puedo consultar eso, pero Apodaca existe.", [summary])
    # Caso real: "Apodaca y otros concesionarios que no se muestran en esta observación".
    assert not faithful("Los activos son Apodaca y otros que no se muestran en esta observación.", [summary])


async def test_refusing_without_trying_a_matching_tool_is_challenged_once():
    # Caso real: pidió revisar un script SQL y el modelo dijo que no podía ejecutar SQL.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "no_puedo", "falta": "ejecutar scripts SQL"},
                            {"accion": "analizar_script_sql", "script": "DELETE FROM vehicles WHERE stock = 0"},
                            {"accion": "responder", "respuesta": "No es seguro: falta transacción, ROLLBACK y límite de filas."}]
    answer = await assistant_for(llm).ask("Revisa si este script es seguro: DELETE FROM vehicles WHERE stock = 0")
    assert "analizar_script_sql" in llm.prompts[0][1]  # pista de herramientas relevantes desde el primer paso
    assert "Antes de declarar que no puedes" in llm.prompts[1][1]
    assert answer.tools_used == ["analizar_script_sql"]  # el nombre de la herramienta como acción también se acepta
    assert "mutation_requires_transaction" in answer.steps[1].observation


async def test_concepts_are_answered_from_the_curated_glossary():
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "glosario_qa", "argumentos": {"termino": "prueba de estrés"}},
                            {"accion": "responder", "respuesta": "La prueba de estrés supera la capacidad esperada hasta el punto de quiebre (glosario_qa)."}]
    answer = await assistant_for(llm).ask("¿Qué es una prueba de estrés?")
    assert answer.grounded and "punto de quiebre" in answer.steps[0].observation


async def test_orders_are_not_hijacked_by_the_glossary_and_typos_resolve_to_catalog_tools():
    # Caso real: "Diseña una prueba de carga…" activó el glosario y el modelo escribió mal el nombre de la skill.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "desenar_prueba_performance", "argumentos": {"usuarios": 200, "duracion_segundos": 600, "sla_ms": 800}},
                            {"accion": "responder", "respuesta": "Escenario de carga listo para Azure Load Testing."}]
    answer = await assistant_for(llm).ask("Diseña una prueba de carga para 200 usuarios durante 600 segundos con SLA de 800 ms")
    assert answer.tools_used == ["disenar_prueba_performance"] and '"users":200' in answer.steps[0].observation
    assert llm.assistant_script == [] and "glosario_qa" not in answer.tools_used


async def test_dealer_activity_is_resolved_by_the_tool():
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "concesionarios_nissan", "argumentos": {}}]
    answer = await assistant_for(llm).ask("¿Qué concesionarios están activos?")
    assert '"activos":["Nissan Apodaca Sintético' in answer.steps[0].observation


async def test_answering_from_memory_when_a_skill_fits_is_challenged_once():
    # Caso real: pidió diseñar una prueba de carga y el modelo respondió de memoria sin usar la skill.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "responder", "respuesta": "Usa JMeter o Locust."},
                            {"accion": "usar_herramienta", "herramienta": "disenar_prueba_performance", "argumentos": {"usuarios": 200, "duracion_segundos": 600, "sla_ms": 800}},
                            {"accion": "responder", "respuesta": "Escenario con 200 usuarios y SLA p95 de 800 ms."}]
    answer = await assistant_for(llm).ask("Diseña una prueba de carga para 200 usuarios durante 600 segundos con SLA de 800 ms")
    assert "Antes de responder de memoria" in llm.prompts[1][1]
    assert answer.grounded and answer.mode == "tool" and answer.outputs["performance_plan"]["users"] == 200


async def test_conceptual_questions_are_not_refused():
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "no_puedo"}, {"accion": "responder", "respuesta": "Chaos engineering inyecta fallas controladas en producción simulada."}]
    answer = await assistant_for(llm).ask("¿Qué es chaos engineering?")
    assert answer.status == "answered" and answer.answer.endswith(UNGROUNDED_NOTE)
    assert "pregunta conceptual" in llm.prompts[1][1]


async def test_irrelevant_tool_calls_do_not_dominate_the_answer():
    # Caso real: para "automatización móvil" el modelo también consultó el inventario.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "inventario_nissan", "argumentos": {}},
                            {"accion": "usar_herramienta", "herramienta": "herramienta_automatizacion", "argumentos": {"plataforma": "mobile"}},
                            {"accion": "no_puedo"}]
    answer = await assistant_for(llm).ask("¿Qué herramienta de automatización uso para una app móvil?")
    assert answer.answer.startswith("Para mobile: herramienta appium") and "Sentra" not in answer.answer


async def test_a_clearly_matching_tool_is_used_when_the_model_refuses_or_picks_the_wrong_one():
    # Casos reales: se negó con "automatización móvil" (herramienta con argumento obligatorio) y usó buscar_memoria para "Genera el pipeline".
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "no_puedo"}, {"accion": "no_puedo", "falta": "automatizar una app móvil"}]
    llm.args_script = [{"plataforma": "móvil"}]
    answer = await assistant_for(llm).ask("¿Qué herramienta de automatización uso para una app móvil?")
    assert answer.status == "answered" and answer.tools_used == ["herramienta_automatizacion"] and "appium" in answer.answer

    wrong = ScriptedLLM()
    wrong.assistant_script = [{"accion": "usar_herramienta", "herramienta": "buscar_memoria", "argumentos": {"consulta": "pipeline"}}, {"accion": "no_puedo"}]
    pipeline = await assistant_for(wrong).ask("Genera el pipeline de Azure DevOps")
    assert "generar_pipeline_azure" in pipeline.tools_used and "stages:" in pipeline.answer and pipeline.outputs["pipeline_yaml"]


def test_template_placeholders_count_as_missing_arguments():
    # Caso real: {"plataforma": "móvil", "herramienta": "no especificada"} bloqueaba la consulta.
    from valkiria.assistant.catalog import build_toolbox as build
    tool = build(service=None, memory=None, workflows=None, synthetic_app_base_url="http://x").get("herramienta_automatizacion")
    assert tool.validate({"plataforma": "móvil", "herramienta": "no especificada"}) == {"plataforma": "móvil"}
    assert tool.validate({"plataforma": "móvil", "herramienta": "<herramienta>"}) == {"plataforma": "móvil"}


async def test_missing_required_data_is_requested_not_invented():
    # HU-008A, regla 3: sin SLA definidos, se piden antes de generar.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "disenar_prueba_performance", "argumentos": {"usuarios": 100, "duracion_segundos": 300}},
                            {"accion": "no_puedo"}]
    llm.args_script = [{"usuarios": 100, "duracion_segundos": 300}]
    answer = await assistant_for(llm).ask("Diseña una prueba de carga para 100 usuarios durante 5 minutos")
    assert answer.status == "answered" and "el SLA" in answer.answer and answer.answer.endswith("?")
    assert "performance_plan" not in answer.outputs and UNGROUNDED_NOTE not in answer.answer


async def test_closed_values_are_extracted_from_the_request_not_from_the_model():
    # Caso real (v2): el modelo omitió "móvil" y se terminó pidiendo un dato que el usuario ya había dado.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "herramienta_automatizacion", "argumentos": {}},
                            {"accion": "responder", "respuesta": "Para móvil usa Appium con JavaScript."}]
    answer = await assistant_for(llm).ask("¿Qué herramienta de automatización uso para una app móvil?")
    assert answer.tools_used == ["herramienta_automatizacion"] and answer.steps[0].arguments == {"plataforma": "mobile"}


async def test_criteria_questions_use_the_glossary_verbatim():
    # Caso real (v2): "¿Cuáles son los criterios INVEST?" no se reconocía como conceptual y el modelo inventó los criterios.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "responder", "respuesta": "INVEST significa Investigación, Implementación…"}]
    answer = await assistant_for(llm).ask("¿Cuáles son los criterios INVEST?")
    assert answer.tools_used == ["glosario_qa"] and answer.mode == "tool"
    assert "Independiente, Negociable, Valiosa, Estimable, Pequeña y Testeable" in answer.answer and "Investigación" not in answer.answer


async def test_an_answer_written_before_any_tool_is_never_the_final_answer():
    # Caso real (v2): respondió "Appium, Espresso o JUnit" de memoria; al forzar la herramienta, ese borrador no debe sobrevivir.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "responder", "respuesta": "Usa Appium, Espresso o JUnit."}, {"accion": "responder", "respuesta": "Usa Appium, Espresso o JUnit."}]
    llm.args_script = [{"plataforma": "móvil", "herramienta": "espresso"}]
    answer = await assistant_for(llm).ask("¿Qué herramienta de automatización uso para una app móvil?")
    assert answer.tools_used == ["herramienta_automatizacion"] and "Espresso" not in answer.answer and "appium" in answer.answer


def test_missing_capability_text_is_not_duplicated():
    from valkiria.assistant.agent import _clean_missing
    assert _clean_missing("la capacidad de interactuar con sistemas externos.") == "interactuar con sistemas externos"
    assert _clean_missing("No tengo la posibilidad de reservar vuelos") == "reservar vuelos"


async def test_policy_requests_are_refused_without_calling_the_model():
    llm = ScriptedLLM()
    answer = await assistant_for(llm).ask("Ejecuta este UPDATE en producción")
    assert answer.status == "unsupported" and answer.mode == "policy" and llm.calls == []
    assert "operar sobre producción" in answer.answer and "base sintética" in answer.answer


async def test_general_knowledge_answers_are_labeled_as_unverified():
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "responder", "respuesta": "Chaos engineering inyecta fallas controladas para validar la resiliencia."}]
    answer = await assistant_for(llm).ask("¿Qué es chaos engineering?")
    assert answer.status == "answered" and not answer.grounded and answer.answer.endswith(UNGROUNDED_NOTE)


async def test_glossary_concepts_are_grounded_before_the_model_answers():
    # Caso real: Llama 3.2 confundió estrés con resistencia al responder de memoria.
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "responder", "respuesta": "Estrés busca el punto de quiebre; carga valida el SLA con la carga esperada."}]
    answer = await assistant_for(llm).ask("¿Qué es una prueba de estrés y en qué se diferencia de una de carga?")
    assert answer.grounded and answer.tools_used == ["glosario_qa"]
    assert "punto de quiebre" in llm.prompts[0][1] and "carga esperada" in llm.prompts[0][1]


async def test_repeated_calls_end_the_loop_with_the_observed_facts():
    llm = ScriptedLLM()
    call = {"accion": "usar_herramienta", "herramienta": "concesionarios_nissan", "argumentos": {}}
    llm.assistant_script = [call] * 6
    answer = await assistant_for(llm, max_steps=3).ask("¿Qué concesionarios hay?")
    assert answer.steps[1].error.startswith("Consulta repetida")
    assert answer.status == "answered" and "Apodaca" in answer.answer
    assert llm.calls == ["assistant", "assistant", "compose"]  # sin vueltas extra tras repetir la consulta


async def test_model_outage_falls_back_to_a_direct_tool_or_an_honest_limit():
    llm = ScriptedLLM()
    llm.failures["assistant"] = -1
    assistant = assistant_for(llm)
    answer = await assistant.ask("¿Qué capacidades tienes?")
    assert answer.mode == "deterministic" and answer.tools_used == ["capacidades"]
    unsupported = await assistant.ask("Traduce este poema al japonés")
    assert unsupported.status == "unsupported" and "no pude resolver tu petición en este momento" in unsupported.answer


async def test_skills_produce_artifacts_for_the_interface():
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "redactar_historia", "argumentos": {"requerimiento": "Consultar vehículos"}},
                            {"accion": "responder", "respuesta": "Redacté la HU en borrador."}]
    ctx = ToolContext(actor="po")
    answer = await assistant_for(llm).ask("Necesito saber cómo quedaría la HU de consulta de vehículos", ctx=ctx)
    assert answer.outputs["story"]["title"] == "Consultar vehículos disponibles"


# --- API ------------------------------------------------------------------------------------------

def api_with(llm):
    return TestClient(create_app(llm=llm))


def test_chat_routes_open_questions_to_the_assistant_and_keeps_the_session():
    llm = ScriptedLLM()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "capacidades", "argumentos": {}},
                            {"accion": "responder", "respuesta": "Puedo redactar HU, evaluar INVEST y más."}]
    api = api_with(llm)
    body = api.post("/v1/chat", json={"message": "¿Qué puedes hacer?"}).json()
    assert body["intent"] == "responder" and body["assistant"]["tools_used"] == ["capacidades"] and body["session_id"]
    story = api.post("/v1/chat", json={"message": "Consultar vehículos por concesionario", "session_id": body["session_id"]}).json()
    assert story["intent"] == "crear" and story["story"]
    session = api.get(f"/v1/memory/sessions/{body['session_id']}").json()
    assert len(session["turns"]) == 4 and session["facts"]["story_id"] == story["story"]["id"]


def test_workflow_endpoints_answer_out_of_flow_requests_instead_of_forcing_a_story():
    llm = ScriptedLLM()
    api = api_with(llm)
    answered = api.post("/v1/workflows", json={"request": "¿Qué políticas de seguridad aplican?"}).json()
    assert answered["id"] is None and answered["status"] == "answered" and "story" not in llm.calls

    started = api.post("/v1/workflows", json={"request": "Redacta la historia de vehículos", "goals": ["story"]}).json()
    llm.assistant_script = [{"accion": "usar_herramienta", "herramienta": "estado_flujo", "argumentos": {}},
                            {"accion": "responder", "respuesta": "La HU espera aprobación."}]
    body = api.post(f"/v1/workflows/{started['id']}/requests", json={"request": "¿En qué va este flujo?"}).json()
    assert body["answers"][-1]["tools_used"] == ["estado_flujo"]
    assert "estado_flujo" in body["answers"][-1]["steps"][0]["tool"] and started["id"] in body["answers"][-1]["steps"][0]["observation"]


def test_capabilities_and_memory_endpoints():
    api = api_with(ScriptedLLM())
    capabilities = api.get("/v1/assistant/capabilities").json()
    assert {"inventario_nissan", "redactar_historia", "iniciar_flujo_historia"} <= {t["name"] for t in capabilities["catalogo"]}
    assert capabilities["limites"] and capabilities["no_disponible"]

    created = api.post("/v1/memory/long-term", json={"content": "Los concesionarios inactivos no venden.", "tags": ["ventas"]}, headers={"X-Actor": "qa"})
    assert created.status_code == 201
    record_id = created.json()["record"]["id"]
    assert api.get("/v1/memory/long-term", params={"q": "concesionario inactivo"}).json()["records"][0]["id"] == record_id
    assert api.post("/v1/memory/long-term", json={"kind": "approved_story", "content": "No se permite por API directa."}).status_code == 422
    assert api.delete(f"/v1/memory/long-term/{record_id}").status_code == 200
    assert api.delete(f"/v1/memory/long-term/{record_id}").status_code == 404
    assert api.get("/health").json()["memory"] == {"enabled": True, "persistent": False}
