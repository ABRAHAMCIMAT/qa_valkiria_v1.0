"""Catálogo de herramientas y skills de Valkiria, conectado a los casos de uso reales (nada simulado)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from valkiria.application.automation_execution import (
    select_execution_tool,
    static_analyse_database_script,
    suggest_database_tool,
)
from valkiria.application.qa_artifacts import (
    MAX_AUTOMATION_BATCH,
    generate_pipeline_yaml,
    performance_plan,
)
from valkiria.assistant.capabilities import LIMITS, capability_summary
from valkiria.assistant.glossary import GLOSSARY, lookup
from valkiria.assistant.tools import Param, Summary, Tool, ToolBox, ToolContext
from valkiria.workflow.validators import MAX_CRITERIA_PER_MATRIX

if TYPE_CHECKING:
    from valkiria.application.use_cases import ValkiriaService
    from valkiria.memory.service import MemoryService
    from valkiria.workflow.engine import WorkflowEngine

# Explicación en lenguaje natural de cada hallazgo: el modelo no debe interpretar códigos por su cuenta.
SQL_FINDINGS = {
    "dangerous_statement": "Contiene sentencias destructivas o de administración (DROP, TRUNCATE, GRANT, REVOKE, etc.).",
    "mutation_requires_transaction": "Modifica datos sin abrir una transacción (BEGIN o START TRANSACTION).",
    "mutation_requires_rollback": "No incluye ROLLBACK para revertir los cambios de la prueba.",
    "update_requires_where": "Tiene un UPDATE sin WHERE: afectaría todas las filas.",
    "delete_requires_where": "Tiene un DELETE sin WHERE: borraría todas las filas.",
    "mutation_requires_row_limit": "La mutación no limita cuántas filas puede afectar.",
    "each_update_delete_requires_row_limit": "Cada UPDATE y DELETE necesita su propio límite de filas (LIMIT, TOP o FETCH FIRST).",
    "postgresql_limit_requires_simple_form_or_subquery": "En PostgreSQL el límite de filas solo se traduce en sentencias simples; usa una subconsulta.",
}

POLICIES = [
    "Solo entorno sintético; producción bloqueada (VALKIRIA_ALLOW_PRODUCTION=false).",
    "Entrega PR-only: sin commits directos.",
    "Aprobación humana sobre versión y hash exactos antes de publicar (RT-02).",
    f"Matriz: mínimo un caso positivo, negativo y de borde por criterio; máximo 30 casos y {MAX_CRITERIA_PER_MATRIX} criterios por generación (HU-004).",
    f"Automatización: lotes de máximo {MAX_AUTOMATION_BATCH} scripts (HU-009).",
    "Scripts SQL de mutación: transacción, ROLLBACK, WHERE y límite de filas en cada UPDATE/DELETE.",
    "Secretos en Azure Key Vault; nunca en el repositorio ni en las respuestas.",
]


def build_toolbox(*, service: ValkiriaService | None, memory: MemoryService | None, workflows: WorkflowEngine | None, synthetic_app_base_url: str,
                  http_timeout: float = 5.0, transport: httpx.AsyncBaseTransport | None = None) -> ToolBox:
    box = ToolBox()

    async def synthetic_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # Solo lectura (GET) sobre la aplicación sintética de Nissan; nunca crea ni cancela órdenes.
        async with httpx.AsyncClient(base_url=synthetic_app_base_url, timeout=http_timeout, transport=transport) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    async def capacidades(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return capability_summary(box)

    async def politicas(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {"politicas": POLICIES, "limites": list(LIMITS)}

    async def inventario(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        vehicles = (await synthetic_get("/vehicles"))["items"]
        placement = await synthetic_get("/inventory")
        # La disponibilidad se calcula aquí (stock > 0); no se deja a la interpretación del modelo.
        def label(v: dict[str, Any]) -> str:
            return " ".join(str(part) for part in (v.get("model"), v.get("year")) if part)

        available = [f"{label(v)} (stock {v.get('stock', 0)})" for v in vehicles if v.get("stock", 0) > 0]
        result = {"fuente": "app sintética de Nissan", "disponibles": available, "sin_stock": [label(v) for v in vehicles if v.get("stock", 0) <= 0],
                  "inventario_por_concesionario": placement["items"]}
        if not args.get("solo_disponibles"):
            result["vehiculos"] = vehicles
        return result

    async def concesionarios(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        dealers = (await synthetic_get("/dealers"))["items"]
        # Activo/inactivo se resuelve aquí para que el modelo no lo interprete mal.
        result = {"fuente": "app sintética de Nissan", "activos": [f"{d.get('name')} ({d.get('region', '')})" for d in dealers if d.get("active")],
                  "inactivos": [f"{d.get('name')} ({d.get('region', '')})" for d in dealers if not d.get("active")]}
        if not args.get("solo_activos"):
            result["concesionarios"] = dealers
        return result

    async def citas(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {"fuente": "app sintética de Nissan", "citas_servicio": (await synthetic_get("/service-appointments"))["items"]}

    async def glosario(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        found = lookup(args["termino"])
        return {"fuente": "glosario QA de Valkiria", "resultados": found} if found else {"encontrado": False, "terminos_disponibles": sorted(GLOSSARY)}

    async def analizar_sql(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        analysis = static_analyse_database_script(args["script"], args.get("motor"))
        problems = [SQL_FINDINGS.get(code, code) for code in analysis["findings"]]
        verdict = "Seguro para ejecutar en el entorno sintético." if analysis["passed"] else "NO es seguro todavía: corrige " + str(len(problems)) + " problema(s)."
        return {"veredicto": verdict, "problemas": problems, "tipo": "modifica datos" if analysis["statement_type"] == "mutation" else "solo lectura",
                "transaccion": analysis["transaction_detected"], "rollback": analysis["rollback_detected"], "codigos": analysis["findings"]}

    async def herramienta_bd(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return suggest_database_tool(args["motor"], args.get("lenguaje", "python"), args.get("ambiente", "qa"))

    async def herramienta_automatizacion(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return select_execution_tool(args["plataforma"], args.get("herramienta"))

    async def buscar_memoria(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        if not memory or not memory.enabled:
            return {"recuerdos": [], "nota": "La memoria de largo plazo está desactivada."}
        found = await memory.long_term.recall(args["consulta"], namespace=ctx.namespace, k=5)
        return {"recuerdos": [{"tipo": r.record.kind, "contenido": r.record.content, "relevancia": r.score} for r in found]}

    async def estado_flujo(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        workflow_id = args.get("workflow_id") or ctx.workflow_id
        if not workflows or not workflow_id:
            return {"error": "Indica el id del flujo (workflow_id)."}
        state, plan = await workflows.get(str(workflow_id))
        return {"id": state.id, "estado": plan.status, "objetivos": state.goals, "aprobaciones_pendientes": plan.pending_approvals, "datos_faltantes": plan.missing_inputs,
                "artefactos": {k: {"version": r.version, "aprobado": r.approved} for k, r in state.artifacts.items()},
                "fallos": {k: f.error_code for k, f in state.failures.items()}, "siguientes_acciones": plan.next_actions(state)[:5]}

    async def redactar_historia(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        story = await service.create_story(args["requerimiento"], ctx.actor)
        ctx.outputs["story"] = story.model_dump(mode="json")
        return {"historia": story.model_dump(mode="json", include={"id", "title", "description", "acceptance_criteria", "business_rules"}), "estado": "borrador"}

    async def iniciar_flujo(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        goals = [g.strip() for g in str(args.get("objetivos", "")).split(",") if g.strip()] or None
        state, plan = await workflows.start(request=args["peticion"], goals=goals, params={"namespace": ctx.namespace}, actor=ctx.actor, trace_id=ctx.trace_id)
        ctx.outputs["workflow_id"] = state.id
        return {"workflow_id": state.id, "estado": plan.status, "objetivos": state.goals, "siguientes_acciones": plan.next_actions(state)[:3]}

    async def performance(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        plan = performance_plan("consulta-directa", tool=args.get("herramienta", "jmeter"), scenario_type=args.get("tipo", "load"), users=args["usuarios"],
                                duration_seconds=args["duracion_segundos"], sla_ms=args["sla_ms"])
        ctx.outputs["performance_plan"] = plan | {"destino": "azure-load-testing", "ejecutado": False}
        return ctx.outputs["performance_plan"]

    async def pipeline(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        ctx.outputs["pipeline_yaml"] = generate_pipeline_yaml()
        return {"yaml": ctx.outputs["pipeline_yaml"], "nota": "Sin scripts aprobados, la etapa de pruebas queda con advertencia (HU-007)."}

    box.register(Tool("capacidades", "tool", "Capacidades de Valkiria", "Lista lo que Valkiria puede y no puede hacer.", capacidades,
                      keywords=("puedes", "capacidad", "ayuda", "funciones", "hacer")))
    box.register(Tool("politicas", "tool", "Políticas y límites", "Reglas de seguridad y límites de calidad (casos, lotes, SQL, producción).", politicas,
                      keywords=("politica", "regla", "limite", "seguridad", "produccion", "permitido")))
    box.register(Tool("glosario_qa", "tool", "Glosario de QA", "Definiciones verificadas de conceptos de QA: tipos de prueba, INVEST, técnicas de diseño de casos, SLA.", glosario,
                      (Param("termino", "string", "concepto a definir, por ejemplo 'prueba de estrés'"),), ("que es", "diferencia", "concepto", "definicion", "prueba", "significa", "tipo")))
    box.register(Tool("inventario_nissan", "tool", "Inventario Nissan (sintético)", "Vehículos, precios, stock y ubicación por concesionario en la app sintética.", inventario,
                      (Param("solo_disponibles", "boolean", "solo vehículos con stock", required=False),), ("vehiculo", "auto", "stock", "inventario", "precio", "modelo", "sentra", "versa", "kicks")))
    box.register(Tool("concesionarios_nissan", "tool", "Concesionarios Nissan (sintético)", "Concesionarios, región y si están activos.", concesionarios,
                      (Param("solo_activos", "boolean", "solo concesionarios activos", required=False),), ("concesionario", "agencia", "dealer", "region", "activo")))
    box.register(Tool("citas_servicio", "tool", "Citas de servicio (sintético)", "Citas de servicio registradas y su estado.", citas, keywords=("cita", "servicio", "taller")))
    box.register(Tool("analizar_script_sql", "tool", "Análisis estático de SQL", "Revisa un script SQL: sentencias peligrosas, transacción, ROLLBACK, WHERE y límite de filas.", analizar_sql,
                      (Param("script", "string", "script SQL completo", max_length=100_000), Param("motor", "string", "motor de base de datos", required=False,
                                                                                                    choices=("postgresql", "oracle", "sqlserver", "mysql"))),
                      ("sql", "script", "query", "update", "delete", "seguro", "base de datos")))
    box.register(Tool("herramienta_bd", "tool", "Herramienta de pruebas de BD", "Recomienda framework y estructura para probar una base de datos.", herramienta_bd,
                      (Param("motor", "string", "motor", choices=("postgresql", "oracle", "sqlserver", "mysql")), Param("lenguaje", "string", "lenguaje", required=False, choices=("python", "java", "node")),
                       Param("ambiente", "string", "ambiente", required=False, choices=("development", "integration", "qa", "staging"))), ("base de datos", "framework", "prueba", "bd")))
    box.register(Tool("herramienta_automatizacion", "tool", "Herramienta de automatización", "Elige lenguaje y herramienta compatibles para web, móvil, API, escritorio o BD.", herramienta_automatizacion,
                      (Param("plataforma", "string", "web, mobile, api, desktop o database"), Param("herramienta", "string", "herramienta preferida", required=False)),
                      ("automatizacion", "playwright", "selenium", "appium", "api", "movil", "web", "herramienta")))
    box.register(Tool("buscar_memoria", "tool", "Memoria del equipo", "Busca HU aprobadas, preferencias del PO, correcciones y lecciones anteriores.", buscar_memoria,
                      (Param("consulta", "string", "qué buscar"),), ("antes", "anterior", "recuerda", "memoria", "aprobada", "preferencia", "leccion")))
    box.register(Tool("estado_flujo", "tool", "Estado de un flujo", "Estado, aprobaciones pendientes, artefactos y siguientes pasos de un flujo por historia.", estado_flujo,
                      (Param("workflow_id", "string", "id del flujo", required=False),), ("flujo", "estado", "pendiente", "aprobacion", "avance")))
    if service:
        box.register(Tool("redactar_historia", "skill", "Redactar historia de usuario", "Redacta una HU en borrador con criterios Dado/Cuando/Entonces (HU-003B).", redactar_historia,
                          (Param("requerimiento", "string", "requerimiento en lenguaje natural"),), ("historia", "hu", "requerimiento", "redactar")))
    if workflows:
        box.register(Tool("iniciar_flujo_historia", "skill", "Flujo por historia", "Inicia el flujo con dependencias: HU, INVEST, matriz, riesgo, scripts, pipeline.", iniciar_flujo,
                          (Param("peticion", "string", "qué se necesita"), Param("objetivos", "string", "capacidades separadas por coma: story, invest, matrix, risk, automation, pipeline", required=False)),
                          ("flujo", "matriz", "invest", "riesgo", "scripts", "completo")))
    box.register(Tool("disenar_prueba_performance", "skill", "Diseño de prueba de performance", "Diseña un escenario para Azure Load Testing (JMeter o Locust), sin ejecutarlo.", performance,
                      (Param("usuarios", "integer", "usuarios concurrentes"), Param("duracion_segundos", "integer", "duración en segundos"), Param("sla_ms", "integer", "SLA p95 en ms"),
                       Param("herramienta", "string", "jmeter o locust", required=False, choices=("jmeter", "locust")),
                       Param("tipo", "string", "tipo de prueba", required=False, choices=("load", "stress", "spike", "soak"))), ("performance", "rendimiento", "carga", "estres", "jmeter", "locust", "usuarios")))
    box.register(Tool("generar_pipeline_azure", "skill", "Pipeline de Azure DevOps", "Genera el YAML base del pipeline de pruebas (HU-007).", pipeline,
                      keywords=("pipeline", "yaml", "azure devops", "ci", "cd")))
    for name, summarize in SUMMARIZERS.items():
        box.attach(name, summarize)
    return box


# --- Conclusiones verificables por herramienta (ver `Summary`) ---------------------------------------

def _names(items: list[str]) -> list[str]:
    # "Nissan Apodaca Sintético (Nuevo León)" → "Apodaca": el dato distintivo que la respuesta debe mencionar.
    return [item.split("(")[0].replace("Nissan", "").replace("Sintético", "").strip() or item for item in items]


def _sum_capacidades(r: dict[str, Any]) -> Summary:
    text = ("Esto es lo que puedo hacer:\n" + "\n".join(f"- {line}" for line in r["herramientas"] + r["skills"])
            + "\n\nFlujo por historia: " + "; ".join(r["flujo_por_historia"]) + ".\n\nTodavía no disponible:\n" + "\n".join(f"- {line}" for line in r["no_disponible"])
            + "\n\nLímites:\n" + "\n".join(f"- {line}" for line in r["limites"]))
    return Summary(text, verbatim=True)


def _sum_politicas(r: dict[str, Any]) -> Summary:
    return Summary("Políticas de Valkiria:\n" + "\n".join(f"- {p}" for p in r["politicas"]) + "\n\nLímites:\n" + "\n".join(f"- {p}" for p in r["limites"]), verbatim=True)


def _sum_glosario(r: dict[str, Any]) -> Summary:
    if not r.get("resultados"):
        return Summary("El concepto no está en el glosario verificado de Valkiria.", verbatim=True)
    return Summary("\n".join(f"- {x['termino'].capitalize()}: {x['definicion']}" for x in r["resultados"]), tuple(x["termino"].split()[-1] for x in r["resultados"][:2]))


def _sum_inventario(r: dict[str, Any]) -> Summary:
    available, empty = r["disponibles"], r["sin_stock"]
    text = ("Vehículos disponibles: " + ", ".join(available) + "." if available else "No hay vehículos con stock.") + (f" Sin stock: {', '.join(empty)}." if empty else "")
    anchors = tuple(item.split()[0] for item in available) or ("no hay",)
    return Summary(text + " (Fuente: app sintética de Nissan.)", anchors, tuple((item.split()[0], r"sin stock|agotad|no disponible|stock 0|sin existencia") for item in empty))


def _sum_concesionarios(r: dict[str, Any]) -> Summary:
    active, inactive = r["activos"], r["inactivos"]
    text = ("Concesionarios activos: " + ", ".join(active) + "." if active else "No hay concesionarios activos.") + (f" Inactivos: {', '.join(inactive)}." if inactive else "")
    return Summary(text + " (Fuente: app sintética de Nissan.)", tuple(_names(active)) or ("no hay",), tuple((name, "inactiv") for name in _names(inactive)))


def _sum_citas(r: dict[str, Any]) -> Summary:
    items = r["citas_servicio"]
    detail = "; ".join(f"cita {c.get('appointment_id')} en estado {c.get('status')}" for c in items)
    return Summary(f"Hay {len(items)} cita(s) de servicio registradas" + (f": {detail}." if items else ".") + " (Fuente: app sintética de Nissan.)", (str(len(items)),))


def _sum_sql(r: dict[str, Any]) -> Summary:
    text = r["veredicto"] + ("\n" + "\n".join(f"- {p}" for p in r["problemas"]) if r["problemas"] else "")
    return Summary(text, ("no es seguro",) if r["problemas"] else ("seguro",), (("seguro", r"no es seguro|inseguro|no seguro|corrige"),) if r["problemas"] else ())


def _sum_bd(r: dict[str, Any]) -> Summary:
    return Summary(f"Para {r['engine']} con {r['language']}: {r['framework']}; esquema con {r['schema_control']}; validación directa con {r['direct_validation']}; "
                   f"mutaciones con {r['transaction_policy']}.", (r["framework"].split()[0],))


def _sum_automatizacion(r: dict[str, Any]) -> Summary:
    return Summary(f"Para {r['platform']}: herramienta {r['tool']} con {r.get('language', 'el lenguaje del perfil')}. Opciones compatibles: {', '.join(r.get('tools', []))}.", (r["tool"],))


def _sum_memoria(r: dict[str, Any]) -> Summary:
    found = r.get("recuerdos", [])
    if not found:
        return Summary(r.get("nota", "No encontré recuerdos relevantes del equipo."), verbatim=True)
    return Summary("Recuerdos del equipo:\n" + "\n".join(f"- [{m['tipo']}] {m['contenido']}" for m in found), verbatim=True)


def _sum_flujo(r: dict[str, Any]) -> Summary:
    if "error" in r:
        return Summary(r["error"], verbatim=True)
    pending = ", ".join(r["aprobaciones_pendientes"]) or "ninguna"
    artifacts = ", ".join(f"{k} v{v['version']}{' (aprobado)' if v['aprobado'] else ''}" for k, v in r["artefactos"].items()) or "ninguno"
    return Summary(f"El flujo {r['id']} está en estado '{r['estado']}'. Aprobaciones pendientes: {pending}. Artefactos: {artifacts}."
                   + (f" Faltan datos: {', '.join(r['datos_faltantes'])}." if r["datos_faltantes"] else ""), (r["estado"],))


def _sum_historia(r: dict[str, Any]) -> Summary:
    story = r["historia"]
    return Summary(f"Redacté la HU '{story['title']}' en borrador con {len(story['acceptance_criteria'])} criterio(s) de aceptación.", verbatim=True)


def _sum_iniciar(r: dict[str, Any]) -> Summary:
    return Summary(f"Inicié el flujo {r['workflow_id']} con objetivos {', '.join(r['objetivos'])}; estado '{r['estado']}'.", verbatim=True)


def _sum_performance(r: dict[str, Any]) -> Summary:
    return Summary(f"Diseñé un escenario {r['scenario_type']} con {r['users']} usuarios durante {r['duration_seconds']} s y SLA p95 de {r['target_sla_ms']} ms, "
                   f"con {r['tool']} para Azure Load Testing. No se ejecutó (HU-008B).", verbatim=True)


def _sum_pipeline(r: dict[str, Any]) -> Summary:
    return Summary("Generé el YAML base del pipeline de Azure DevOps (etapas Build, Test y Publish con aprobación). " + r["nota"] + "\n\n" + r["yaml"], verbatim=True)


SUMMARIZERS = {
    "capacidades": _sum_capacidades, "politicas": _sum_politicas, "glosario_qa": _sum_glosario, "inventario_nissan": _sum_inventario,
    "concesionarios_nissan": _sum_concesionarios, "citas_servicio": _sum_citas, "analizar_script_sql": _sum_sql, "herramienta_bd": _sum_bd,
    "herramienta_automatizacion": _sum_automatizacion, "buscar_memoria": _sum_memoria, "estado_flujo": _sum_flujo, "redactar_historia": _sum_historia,
    "iniciar_flujo_historia": _sum_iniciar, "disenar_prueba_performance": _sum_performance, "generar_pipeline_azure": _sum_pipeline,
}
