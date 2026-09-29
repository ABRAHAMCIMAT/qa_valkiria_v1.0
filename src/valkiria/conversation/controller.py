"""Conductor de la conversación: el agente principal de Valkiria.

Cada sesión de chat está ligada a un flujo por historia y el conductor siempre sabe en qué paso va. El FLUJO lo decide
el código (determinista y auditable); el modelo solo redacta (HU, versiones, respuestas) y responde preguntas con
herramientas. Prioridad de cada mensaje:

1. Acciones de la interfaz (botones): ejecutar un paso, aprobar, decidir sugerencias, aportar datos, dividir, nueva HU.
2. Una aclaración pendiente ("¿ajusto la HU actual o empiezo una nueva?").
3. Políticas (producción, commits directos, credenciales…): se rechazan con amabilidad y el flujo sigue.
4. Cortesía (saludo, agradecimiento, molestia): se conversa y se retoma el paso actual.
5. "Nueva historia" explícita: es lo único que reinicia.
6. Sin HU en curso: el requerimiento se convierte en HU (o se propone dividirlo).
7. Con HU en curso:
   a. modificar la HU ("mejórala", "agrega…") → siempre crea la versión N+1;
   b. aprobar / aplicar sugerencias / "siguiente" / pedir un paso ("genera la matriz") → avanza el flujo respetando dependencias;
   c. preguntas u otras peticiones dentro de la función de Valkiria → se atienden primero (prioridad al humano) y se retoma el flujo;
   d. algo que parece un requerimiento nuevo → se pregunta si ajustar la HU actual o empezar otra; nunca se reinicia solo.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from valkiria.application.automation_execution import (
    export_test_cases_to_excel,
    static_analyse_database_script,
)
from valkiria.application.defects import DECISIONS as DEFECT_DECISIONS
from valkiria.application.prompts import (
    SMALLTALK_SYSTEM,
    SQL_VALIDATION_SYSTEM,
    STORY_EDIT_SYSTEM,
    STORY_SPLIT_SYSTEM,
)
from valkiria.application.script_generation import FRAMEWORKS as FRAMEWORK_NAMES
from valkiria.application.use_cases import (
    ValkiriaService,
    _normalize_story,
    _split_item,
    _story_changes,
    looks_broad,
)
from valkiria.assistant.capabilities import policy_block
from valkiria.assistant.routing import is_conversational, is_question, route_message
from valkiria.assistant.tools import ToolContext
from valkiria.conversation.flow import (
    LABELS,
    RUN_LABELS,
    flow_view,
    param_spec,
    performance_reason,
    resume_line,
)
from valkiria.domain.models import UserStory
from valkiria.infrastructure.logging import event, get_logger
from valkiria.memory.service import MemoryService, describe, recall_prompt
from valkiria.memory.text import fold
from valkiria.workflow.engine import WorkflowConflict, WorkflowEngine, WorkflowNotFound
from valkiria.workflow.graph import detect_goals
from valkiria.workflow.planner import INPUT_QUESTIONS, actionable_suggestions
from valkiria.workflow.state import WorkflowState

_EDIT = re.compile(r"\b(mejora|mejorar|mejorala|mejoremos|agrega|agregar|agregale|anade|anadir|quita|quitar|cambia|cambiar|ajusta|ajustar|modifica|modificar|"
                   r"corrige|corregir|actualiza|actualizar|reescribe|reescribir|reformula|precisa|elimina|eliminar|completa|detalla|simplifica|incluye|incluir|"
                   r"renombra|pon|haz que)\b")
_NEW_STORY = re.compile(r"\b(nueva historia|otra historia|historia nueva|nuevo requerimiento|empezar de nuevo|empecemos (de nuevo|otra|una nueva)|"
                        r"comenzar de nuevo|reinicia|reiniciar|olvida (esta|la) historia|otra hu|nueva hu)\b")
_CONTINUE = re.compile(r"^\s*(siguiente|continua|continuemos|sigamos|sigue|adelante|dale|ok,? (sigue|continua|adelante)|que sigue|siguiente paso|"
                       r"cual es el siguiente paso|y ahora que|procede)\b")
_APPROVE = re.compile(r"\b(apruebo|aprobada|aprobado|apruebala|aprueba la (historia|hu|matriz)|aprobar la (historia|hu|matriz)|la apruebo|esta aprobada|de acuerdo, aprueba)\b")
_SUPPOSITIONS_OK = re.compile(r"\b(confirmo|confirmados|confirmo los supuestos|los supuestos (estan bien|son correctos)|acepto los supuestos)\b")
_APPLY_ALL = re.compile(r"\b(aplica|aplicar|acepta|aceptar|acepto|aprueba|apruebo)\b.*\bsugerencias?\b")
_COMMAND = re.compile(r"\b(genera|generar|generame|evalua|evaluar|evaluala|crea|crear|haz|hacer|ejecuta|ejecutar|disena|disenar|prepara|preparar|redacta|"
                      r"valida|validar|verifica|verificar|deriva|derivar|corre|correr|"
                      r"calcula|analiza|analizar|arma|armar|construye|dame|quiero|necesito|puedes|podrias|sigue con|continua con|pasa a)\b")
_RESUME = re.compile(r"^\s*(ninguna|ninguno|nada|sigamos|continuemos donde|volvamos|regresemos|olvidalo|dejalo asi|no,? sigamos|mejor sigamos)\b")
_KEEP = re.compile(r"^\s*(ajusta|ajustala|la actual|actual|ajustar|modifica la actual|1)\b")
_NEW = re.compile(r"^\s*(nueva|una nueva|empieza una nueva|empezar otra|otra|2)\b")


def extract_params(message: str) -> dict[str, Any]:
    """Datos que el usuario escribe en texto libre para pasos que los requieren (HU-009, HU-008A, HU-006)."""
    text = fold(message)
    params: dict[str, Any] = {}
    repo = re.search(r"\b([a-z0-9][\w.-]*/[\w.-]+)\b", message, re.IGNORECASE)
    if repo and "/" in repo.group(1) and not repo.group(1).startswith("http"):
        params["repository"] = repo.group(1)
    users = re.search(r"(\d+)\s*(usuarios|users|vus?)\b", text)
    if users:
        params["performance_users"] = int(users.group(1))
    duration = re.search(r"(\d+)\s*(segundos|segs?|s|minutos|mins?|horas?)\b", text)
    if duration:
        factor = 3600 if duration.group(2).startswith("hora") else 60 if duration.group(2).startswith("min") else 1
        params["performance_duration_seconds"] = int(duration.group(1)) * factor
    sla = re.search(r"(sla|p95)[^\d]{0,15}(\d+)\s*ms|(\d+)\s*ms", text)
    if sla:
        params["performance_sla_ms"] = int(sla.group(2) or sla.group(3))
    framework = re.search(r"\b(playwright|selenium|rest ?assured|postman|newman)\b", text)
    if framework:
        params["framework"] = {"rest assured": "restassured", "restassured": "restassured", "postman": "postman-newman", "newman": "postman-newman"}.get(framework.group(1), framework.group(1))
    if re.search(r"\b(api|rest|endpoints?|servicios? web)\b", text):
        params["platform"] = "api"
    elif re.search(r"\b(web|navegador|interfaz|ui|pantalla)\b", text):
        params["platform"] = "web"
    kind = re.search(r"\b(estres|picos?|spike|resistencia|soak|carga)\b", text)
    if kind:
        params["performance_type"] = {"estres": "stress", "pico": "spike", "picos": "spike", "spike": "spike", "resistencia": "soak", "soak": "soak"}.get(kind.group(1), "load")
    project = re.search(r"proyecto\s+([\w.-]+)", message, re.IGNORECASE)
    if project:
        params["azure_project"] = project.group(1)
    return params


class ConversationController:
    def __init__(self, *, service: ValkiriaService, workflows: WorkflowEngine, assistant, memory: MemoryService, database_executor=None, reports=None):
        self.service = service
        self.workflows = workflows
        self.assistant = assistant
        self.memory = memory
        self.database_executor = database_executor
        self.reports = reports
        self.logger = get_logger("valkiria.conversation")

    # --- Estado de la sesión ------------------------------------------------------------------------------

    async def _session(self, session_id: str, workflow_id: str | None) -> tuple[dict[str, Any], WorkflowState | None]:
        session = await self.memory.session(session_id)
        facts = dict(session.facts) if session else {}
        workflow_id = workflow_id or facts.get("workflow_id")
        state = None
        if workflow_id:
            try:
                state, _ = await self.workflows.get(workflow_id)
            except WorkflowNotFound:
                facts.pop("workflow_id", None)
        return facts, state

    # --- Entrada principal ----------------------------------------------------------------------------------

    async def handle(self, *, session_id: str, message: str | None, action: dict[str, Any] | None, actor: str, namespace: str,
                     trace_id: str | None, workflow_id: str | None = None) -> dict[str, Any]:
        facts, state = await self._session(session_id, workflow_id)
        ctx = _Turn(self, session_id=session_id, actor=actor, namespace=namespace, trace_id=trace_id, facts=facts, state=state)
        if action:
            result = await ctx.act(action)
        else:
            result = await ctx.understand(message or "")
        view = flow_view(ctx.state)
        if result.get("reply_suffix"):
            result["reply"] += result.pop("reply_suffix")
        result.setdefault("actions", view["next"] if ctx.state else [])
        if result.pop("with_resume", False):
            result["resume"] = resume_line(view)
        await self._remember(session_id, message or action.get("label") or action.get("type", ""), result, ctx)
        return result | {"flow": view, "session_id": session_id, "trace_id": trace_id}

    async def _remember(self, session_id: str, said: str, result: dict[str, Any], ctx: _Turn) -> None:
        facts = {"workflow_id": ctx.state.id if ctx.state else None, "pending": ctx.facts.get("pending"), "split": ctx.facts.get("split"),
                 "last_sql": ctx.facts.get("last_sql")}
        try:
            await self.memory.add_turn(session_id, "user", said)
            await self.memory.add_turn(session_id, "assistant", result.get("reply", ""), facts=facts, intent=result.get("intent"))
            # Los datos que se vaciaron deben quitarse explícitamente (add_turn ignora los None).
            session = await self.memory.session(session_id)
            if session:
                for key, value in facts.items():
                    if value is None:
                        session.facts.pop(key, None)
                await self.memory.short_term.store.save(session)
        except Exception as exc:  # noqa: BLE001 - la memoria enriquece, pero no debe romper la conversación
            event(self.logger, logging.WARNING, "memoria_no_guardada", trace_id=ctx.trace_id, error_type=type(exc).__name__)


class _Turn:
    """Un turno de la conversación con acceso al flujo, la memoria y el asistente."""

    def __init__(self, controller: ConversationController, *, session_id: str, actor: str, namespace: str, trace_id: str | None,
                 facts: dict[str, Any], state: WorkflowState | None):
        self.c = controller
        self.session_id = session_id
        self.actor = actor
        self.namespace = namespace
        self.trace_id = trace_id
        self.facts = facts
        self.state = state

    # --- Comprensión del mensaje ------------------------------------------------------------------------

    async def understand(self, message: str) -> dict[str, Any]:
        # "¿Qué sigue?", "¡Apruebo!": los signos de apertura no deben ocultar la intención.
        text = re.sub(r"^[^\w]+", "", fold(message).strip())
        pending = self.facts.get("pending")
        if pending and pending.get("kind") == "new_or_adjust":
            if _KEEP.search(text):
                self.facts["pending"] = None
                return await self.edit_story(pending["message"])
            if _NEW.search(text):
                self.facts["pending"] = None
                return await self.new_story(pending["message"])
            self.facts["pending"] = None
            if _RESUME.search(text):
                return self.resume()
        if policy_block(message):
            return await self.ask(message)
        if is_conversational(message):
            return await self.chat(message)
        if _NEW_STORY.search(text):
            requirement = _NEW_STORY.sub("", message).strip(" .,:;-")
            return await self.new_story(requirement if len(requirement.split()) >= 4 else None)
        if not self.state:
            return await self.first_contact(message)
        return await self.in_flow(message, text)

    async def first_contact(self, message: str) -> dict[str, Any]:
        steps = [g for g in detect_goals(message) if g != "story"]
        if steps and (_COMMAND.search(re.sub(r"^[^\w]+", "", fold(message))) or not is_question(message)):
            # Un paso del flujo sin historia en curso: se explica la dependencia en lugar de inventar una HU sobre el pedido.
            return {"intent": "aclarar", "reply": f"Para {RUN_LABELS.get(steps[0], LABELS.get(steps[0], steps[0])).lower()} primero necesito una historia de usuario. "
                                                  "Cuéntame el requerimiento: quién lo usa, qué necesita hacer y qué resultado espera."}
        if route_message(message) == "assistant":
            return await self.ask(message)
        return await self.create_story(message)

    async def in_flow(self, message: str, text: str) -> dict[str, Any]:
        story = self.state.artifacts.get("story")
        if _APPLY_ALL.search(text) and self._pending_suggestions():
            return await self.decide_suggestions({name: "approved" for name in self._pending_suggestions()})
        if _APPROVE.search(text):
            target = "matrix" if "matriz" in text else "story"
            return await self.approve(target, assumptions_confirmed=bool(_SUPPOSITIONS_OK.search(text)) or "supuesto" in text)
        if _CONTINUE.search(text):
            return await self.continue_flow()
        if _RESUME.search(text):
            return self.resume()
        goals = [g for g in detect_goals(message) if g != "story"]
        wants_step = bool(goals) and (bool(_COMMAND.search(text)) or not is_question(message))
        if story and _EDIT.search(text) and not wants_step:
            return await self.edit_story(message)
        if wants_step:
            # "Ejecuta los scripts" menciona dos pasos (scripts y ejecución): lo que se pide es ejecutar.
            goal = "execution" if "execution" in goals and re.search(r"\b(ejecut\w*|corre|correr|run)\b", text) else goals[0]
            return await self.run(goal, extract_params(message))
        params = extract_params(message)
        waiting = self._waiting_inputs()
        if params and waiting and set(params) & set(waiting[1]):
            return await self.run(waiting[0], params)
        if route_message(message) == "assistant" or is_question(message):
            return await self.ask(message)
        # Parece un requerimiento, pero ya hay una HU en curso: se pregunta en lugar de reiniciar.
        self.facts["pending"] = {"kind": "new_or_adjust", "message": message}
        return {"intent": "aclarar", "with_resume": False,
                "reply": (f"Ya estamos trabajando en la HU «{story.payload.get('title')}» v{story.version}. ¿Quieres que ajuste esa historia con lo que "
                          "me dices o prefieres que empecemos una historia nueva?"),
                "actions": [{"type": "edit_story", "label": "Ajustar la HU actual", "instruction": message},
                            {"type": "new_story", "label": "Empezar una HU nueva", "requirement": message},
                            {"type": "resume", "label": "Ninguna, sigamos donde íbamos"}]}

    # --- Acciones explícitas (botones) -------------------------------------------------------------------

    async def act(self, action: dict[str, Any]) -> dict[str, Any]:
        kind = action.get("type")
        self.facts["pending"] = None
        if kind == "run":
            return await self.run(str(action.get("goal")), dict(action.get("params") or {}))
        if kind == "input":
            return await self.run(str(action.get("goal")), dict(action.get("preset") or {}) | dict(action.get("params") or {}))
        if kind == "approve":
            return await self.approve(str(action.get("artifact", "story")), assumptions_confirmed=bool(action.get("assumptions_confirmed")))
        if kind == "reject":
            return await self.reject(str(action.get("artifact", "story")), str(action.get("comment") or ""))
        if kind == "decide_suggestions":
            return await self.decide_suggestions(dict(action.get("decisions") or {}))
        if kind == "review_defects":
            return await self.review_defects(dict(action.get("decisions") or {}))
        if kind == "edit_story":
            return await self.edit_story(str(action.get("instruction") or "Mejora la historia"))
        if kind == "new_story":
            return await self.new_story(action.get("requirement"))
        if kind == "choose_split":
            options = self.facts.get("split") or []
            index = int(action.get("index", -1))
            if not 0 <= index < len(options):
                return {"intent": "aclarar", "reply": "Esa opción ya no está disponible. ¿Me dices qué historia quieres que redacte?"}
            chosen = options[index]
            self.facts["split"] = None
            return await self.create_story(f"Redacta la historia: {chosen['title']}. {chosen.get('description', '')}".strip(), fresh=True)
        if kind == "export_matrix":
            return self.export_matrix()
        if kind in {"tool", "tool_input"}:
            return await self.tool(str(action.get("name")), dict(action.get("args") or action.get("params") or {}))
        if kind == "resume":
            return self.resume()
        return {"intent": "aclarar", "reply": "No reconocí esa acción. ¿Me dices qué quieres hacer?"}

    # --- Creación y modificación de la historia ----------------------------------------------------------

    async def create_story(self, requirement: str, *, fresh: bool = False) -> dict[str, Any]:
        """HU-003B con tareas acotadas: aclarar si es vago, dividir si es amplio, redactar con el prompt dedicado."""
        if len(re.findall(r"\w+", requirement)) < 5:
            # Regla 3: si falta el actor, la acción o el resultado, se hacen hasta 2 preguntas concretas.
            return {"intent": "conversar", "reply": "Con gusto lo convierto en una historia de usuario, pero necesito un poco más de detalle para no inventar: "
                                                    "¿quién lo usaría y qué necesita lograr?"}
        recalled = await self.c.memory.recall(requirement, task="story", namespace=self.namespace)
        memory_info = {"recalled": describe(recalled)}
        if looks_broad(requirement):
            proposed = await self.c.service.llm.generate_json(system=STORY_SPLIT_SYSTEM, user=requirement, schema={"type": "object"})
            split = [item for item in (_split_item(i) for i in (proposed.get("split") or [])[:5]) if item["title"]]
            if len(split) >= 2:
                self.facts["split"] = split
                return {"intent": "dividir", "split": split, "memory": memory_info,
                        "reply": (f"Este requerimiento abarca varias funcionalidades independientes, así que te propongo dividirlo en {len(split)} historias "
                                  "para que cada una se pueda probar por separado (HU-003B). ¿Cuál quieres que redacte primero?"),
                        "actions": [{"type": "choose_split", "index": i, "label": f"Redactar: {item['title']}"} for i, item in enumerate(split)]}
        story, extras = await self.c.service.draft_story(requirement, self.actor, memory=recall_prompt(recalled))
        payload = story.model_dump(mode="json", include={"id", "title", "description", "business_rules", "acceptance_criteria", "version"})
        warnings = ([f"Requerimiento amplio: otras HU sugeridas: {'; '.join(extras['split'])}."] if extras["split"] else [])
        self.state, _ = await self.c.workflows.adopt_story(story=payload, requirement=requirement, actor=self.actor, namespace=self.namespace,
                                                           assumptions=extras["assumptions"], warnings=warnings, trace_id=self.trace_id)
        record = self.state.artifacts["story"]
        reply = f"Redacté la HU «{story.title}» con {len(story.acceptance_criteria)} criterios de aceptación, como borrador para tu revisión."
        if extras["assumptions"]:
            reply += f" Tomé {len(extras['assumptions'])} supuesto(s) que deberás confirmar antes de aprobarla."
        if extras["split"]:
            reply += f" Cubrí el flujo principal; también sugiero como otras historias: {'; '.join(extras['split'])}."
        reply += " ¿Quieres ajustar algo o seguimos con la evaluación INVEST?"
        return {"intent": "crear", "reply": reply, "story": record.payload, "artifact": self._artifact("story"), "assumptions": record.assumptions,
                "memory": memory_info}

    async def edit_story(self, instruction: str) -> dict[str, Any]:
        """Toda modificación de la HU produce la versión N+1, con la lista de cambios frente a la anterior."""
        record = self.state.artifacts["story"]
        current = UserStory.model_validate(record.payload)
        user = ("Historia actual (versión " + str(record.version) + "):\n"
                + current.model_dump_json(include={"title", "description", "business_rules", "acceptance_criteria"}) + f"\n\nInstrucción del PO:\n{instruction}")
        data = await self.c.service.llm.generate_json(system=STORY_EDIT_SYSTEM, user=user, schema=UserStory.model_json_schema())
        fields = _normalize_story(data)
        try:
            revised = UserStory.model_validate({**fields, "id": current.id, "version": current.version + 1})
        except ValueError:
            return {"intent": "aclarar", "with_resume": True,
                    "reply": "Intenté aplicar el cambio, pero no logré una versión completa de la historia. ¿Me dices con un poco más de detalle qué quieres cambiar?"}
        changes = _story_changes(current, revised)
        if not any(v for k, v in changes.items() if k not in {"from_version", "to_version"}):
            return {"intent": "conversar", "with_resume": True,
                    "reply": "Revisé la historia con tu indicación y no encontré nada que cambiar sin alterar su alcance. ¿Qué te gustaría mejorar en concreto?"}
        payload = revised.model_dump(mode="json", include={"title", "description", "business_rules", "acceptance_criteria"})
        self.state, _ = await self.c.workflows.revise_story(self.state.id, payload=payload, actor=self.actor, instruction=instruction)
        new = self.state.artifacts["story"]
        summary = [str(c).strip() for c in (data.get("changes") or []) if str(c).strip()][:3] if isinstance(data.get("changes"), list) else []
        stale = [LABELS[k] for k in ("invest", "matrix", "risk", "automation") if k in self.state.artifacts and self.state.artifacts[k].based_on.get("story") not in (None, new.version)]
        reply = f"Listo, generé la versión {new.version} de la historia." + (f" Cambios: {' '.join(summary)}" if summary else "")
        if stale:
            reply += f" Como la HU cambió, {', '.join(s.lower() for s in stale)} {'quedó' if len(stale) == 1 else 'quedaron'} desactualizad{'a' if len(stale) == 1 else 'as'}."
        return {"intent": "ajustar", "reply": reply, "story": new.payload, "changes": changes, "artifact": self._artifact("story", changes=changes)}

    async def new_story(self, requirement: str | None) -> dict[str, Any]:
        if self.state:
            previous = self.state.artifacts.get("story")
            self.facts["previous_workflows"] = [*(self.facts.get("previous_workflows") or []), self.state.id][-5:]
            self.state = None
            note = f"De acuerdo, dejo guardada la HU «{previous.payload.get('title')}» v{previous.version} y empezamos una nueva. " if previous else ""
        else:
            note = ""
        if not requirement:
            return {"intent": "conversar", "reply": note + "¿Cuál es el nuevo requerimiento? Cuéntame quién lo usa, qué necesita hacer y qué resultado espera."}
        result = await self.create_story(requirement, fresh=True)
        result["reply"] = note + result["reply"]
        return result

    # --- Avance del flujo --------------------------------------------------------------------------------

    async def run(self, goal: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.state:
            return {"intent": "aclarar", "reply": f"Para {RUN_LABELS.get(goal, goal).lower()} primero necesito una historia de usuario. ¿Cuál es el requerimiento?"}
        before = {k: r.version for k, r in self.state.artifacts.items()}
        try:
            self.state, plan = await self.c.workflows.request(self.state.id, request=None, goals=[goal], params=params, actor=self.actor)
        except WorkflowConflict as exc:
            return {"intent": "aclarar", "reply": f"No pude avanzar: {exc}.", "with_resume": True}
        step = next((s for s in plan.steps if s.capability == goal), None)
        produced = [k for k, r in self.state.artifacts.items() if before.get(k) != r.version and k != "story"]
        if goal in self.state.failures:
            failure = self.state.failures[goal]
            if failure.error_code == "execution_requires_verification":
                pending = [k for k in ("automation", "data_validation") if k in self.state.artifacts and not self.state.artifacts[k].approved]
                reply = failure.message + (" Tienes pendiente de verificar: " + ", ".join(_the(k) for k in pending) + "." if pending else
                                           " Primero genera los scripts o las consultas de datos.")
                return {"intent": "aclarar", "reply": reply, "actions": [{"type": "approve", "artifact": k, "label": f"Verificar y aprobar {_the(k)}"} for k in pending]
                        or [{"type": "run", "goal": "data_validation", "label": RUN_LABELS["data_validation"]}]}
            if failure.error_code == "matrix_requires_split":
                # HU-004 regla 2: con más de 10 criterios se sugiere dividir la HU; se ofrece hacerlo sin salir del flujo.
                return {"intent": "aclarar", "reply": f"{failure.message} Te propongo reducir la HU a su flujo principal (máximo 6 criterios) como una nueva versión; "
                                                      "lo demás puede ir en otras historias.",
                        "actions": [{"type": "edit_story", "label": "Reducir la HU a su flujo principal",
                                     "instruction": "Reduce la historia a su flujo principal con máximo 6 criterios de aceptación; conserva lo esencial."},
                                    {"type": "new_story", "label": "Empezar otra HU con el resto"}]}
            if failure.error_code == "web_execution_unavailable":
                return {"intent": "aclarar", "reply": failure.message,
                        "actions": [{"type": "input", "goal": "automation", "label": "Regenerar los scripts con un stack de API", "preset": {"platform": "api"},
                                     "params": [param_spec("framework")]}]}
            return {"intent": "error", "retryable": True, "reply": f"No pude completar {LABELS.get(goal, goal).lower()}: {failure.message} ¿Lo intentamos de nuevo?",
                    "actions": [{"type": "run", "goal": goal, "label": "Reintentar"}]}
        if goal in produced or (step and step.action == "reuse" and goal in self.state.artifacts):
            result = {"intent": "paso", "reply": self._describe(goal, fresh=goal in produced) + self._also(produced, goal), "artifact": self._artifact(goal)}
            if goal == "execution" and self.c.reports is not None:
                # Evidencia descargable de la ejecución (HU-010).
                report = self.state.artifacts["execution"].payload.get("report")
                if report:
                    await self.c.reports.save(report)
                    result["artifact"] = {**result["artifact"], "data": {k: v for k, v in result["artifact"]["data"].items() if k != "report"} | {"report_id": report["id"]}}
            if goal == "risk":
                result |= self._suggest_performance()
            if goal in {"execution", "triage"}:
                review = next((x for x in flow_view(self.state)["next"] if x["type"] == "review_defects"), None)
                if review:
                    result["actions"] = [review]
            return result
        if step and step.missing_inputs:
            questions = " ".join(q for q in (_question(n) for n in step.missing_inputs))
            return {"intent": "aclarar", "reply": f"Con gusto preparo {LABELS.get(goal, goal).lower()}. {questions}",
                    "actions": [{"type": "input", "goal": goal, "label": RUN_LABELS.get(goal, goal), "params": [param_spec(n) for n in step.missing_inputs]}]}
        if step and step.action == "blocked":
            if any(b.startswith("approval:story") for b in step.blocked_by):
                story = self.state.artifacts["story"]
                extra = " Antes confirma los supuestos del borrador." if story.assumptions else ""
                return {"intent": "aclarar", "reply": f"Para {LABELS.get(goal, goal).lower()} necesito que apruebes la HU v{story.version} (RT-02).{extra}"}
            return {"intent": "aclarar", "reply": f"Aún no puedo hacer {LABELS.get(goal, goal).lower()}: {step.reason}"}
        if step and step.action == "unavailable":
            return {"intent": "no_puedo", "reply": f"{LABELS.get(goal, goal)} todavía no está disponible en Valkiria."}
        return {"intent": "paso", "reply": step.reason if step else "Listo."}

    async def approve(self, artifact: str, *, assumptions_confirmed: bool) -> dict[str, Any]:
        if not self.state or artifact not in self.state.artifacts:
            return {"intent": "aclarar", "reply": "Todavía no hay nada que aprobar. ¿Seguimos con el siguiente paso?", "with_resume": True}
        record = self.state.artifacts[artifact]
        if record.approved:
            return {"intent": "conversar", "reply": f"{_the(artifact).capitalize()} v{record.version} ya está aprobado.", "with_resume": True}
        if artifact == "triage":
            return self._review_prompt("Para aprobar la revisión clasifica cada fallo:")
        if artifact == "story" and record.assumptions and not assumptions_confirmed:
            return {"intent": "aclarar", "reply": "Antes de aprobar, confirma los supuestos del borrador (HU-003B): " + "; ".join(record.assumptions)
                    + ". ¿Son correctos? Si alguno no lo es, dime cómo corregirlo y genero una nueva versión.",
                    "actions": [{"type": "approve", "artifact": "story", "label": "Confirmo los supuestos y apruebo", "assumptions_confirmed": True},
                                {"type": "edit_story", "label": "Corregir un supuesto", "instruction": "Corrige los supuestos de la historia"}]}
        try:
            self.state, _ = await self.c.workflows.approve(self.state.id, artifact=artifact, version=record.version, content_hash=record.content_hash, decision="approved",
                                                           actor=self.actor, assumptions_confirmed=assumptions_confirmed)
        except WorkflowConflict as exc:
            if str(exc).startswith("unreviewed_failures"):
                return self._review_prompt(f"Todavía no puedo aprobar {_the(artifact)}: la última ejecución tiene fallos sin revisar y el pull request "
                                           "no se aprueba con fallos pendientes (HU-010). Clasifica cada uno:")
            raise
        return {"intent": "aprobar", "reply": f"Aprobé {_the(artifact)} v{record.version} a tu nombre (RT-02). Lo dejé registrado y lo tomaré en cuenta "
                "para las siguientes historias.", "with_resume": True}

    async def reject(self, artifact: str, comment: str) -> dict[str, Any]:
        record = self.state.artifacts.get(artifact) if self.state else None
        if not record:
            return {"intent": "aclarar", "reply": "No hay nada que rechazar todavía."}
        self.state, _ = await self.c.workflows.approve(self.state.id, artifact=artifact, version=record.version, content_hash=record.content_hash,
                                                       decision="rejected", actor=self.actor, comment=comment)
        return {"intent": "rechazar", "reply": f"Registré el rechazo de {_the(artifact)} v{record.version}. ¿Qué te gustaría cambiar?"}

    async def decide_suggestions(self, decisions: dict[str, str]) -> dict[str, Any]:
        invest = self.state.artifacts.get("invest") if self.state else None
        if not invest:
            return {"intent": "aclarar", "reply": "Primero hay que evaluar la HU con INVEST.", "actions": [{"type": "run", "goal": "invest", "label": RUN_LABELS["invest"]}]}
        pending = self._pending_suggestions()
        decisions = {name: ("approved" if decisions.get(name) == "approved" else "rejected") for name in pending}
        before = self.state.artifacts["story"].version
        self.state, _ = await self.c.workflows.approve(self.state.id, artifact="invest", version=invest.version, content_hash=invest.content_hash, decision="approved",
                                                       actor=self.actor, suggestions=decisions)
        approved = [n for n, d in decisions.items() if d == "approved"]
        if approved:
            # HU-003A: las sugerencias aprobadas producen la nueva versión de la HU.
            self.state, _ = await self.c.workflows.request(self.state.id, request=None, goals=["story_revision"], params={}, actor=self.actor)
        story = self.state.artifacts["story"]
        if story.version > before:
            reply = f"Apliqué {len(approved)} sugerencia(s) ({', '.join(approved)}) y generé la versión {story.version} de la HU."
            reevaluated = self.state.artifacts.get("invest")
            if reevaluated and reevaluated.based_on.get("story") == story.version:
                ok = sum(c["status"] == "cumple" for c in reevaluated.payload["criteria"])
                reply += f" También reevalué INVEST sobre la v{story.version}: cumple {ok} de 6."
            return {"intent": "ajustar", "reply": reply, "story": story.payload, "artifact": self._artifact("story")}
        return {"intent": "paso", "reply": "Registré tus decisiones sobre las sugerencias INVEST" + (" y ninguna requiere una nueva versión." if not approved else "."),
                "with_resume": True}

    async def review_defects(self, decisions: dict[str, str]) -> dict[str, Any]:
        """HU-010: el humano clasifica cada fallo; los defectos confirmados quedan como borradores de Bug y el PR se desbloquea."""
        record = self.state.artifacts.get("triage") if self.state else None
        if not record or not record.payload.get("defects"):
            return {"intent": "conversar", "reply": "No hay fallos pendientes de revisar.", "with_resume": True}
        try:
            self.state, _ = await self.c.workflows.approve(self.state.id, artifact="triage", version=record.version, content_hash=record.content_hash,
                                                           decision="approved", actor=self.actor, defect_decisions=decisions)
        except WorkflowConflict as exc:
            if str(exc).startswith("defect_decisions_required"):
                return self._review_prompt("Me falta tu decisión sobre algunos fallos: " + str(exc).split(":", 1)[1].replace(",", ", ") + ".")
            if str(exc).startswith("stale_version"):
                return self._review_prompt("Hubo una ejecución nueva mientras revisabas; esta es la revisión vigente:")
            raise
        chosen = self.state.artifacts["triage"].approval.details["decisions"]
        count = {kind: [d for d in record.payload["defects"] if chosen[d["id"]] == kind] for kind in ("defect", "test_issue", "environment")}
        parts = []
        if count["defect"]:
            parts.append(f"{len(count['defect'])} defecto(s) confirmados como borradores de Bug de Azure Boards ({', '.join(d['case_id'] for d in count['defect'])}); "
                         "se publican cuando se decida la herramienta de gestión (HU-004B)")
        if count["test_issue"]:
            parts.append(f"{len(count['test_issue'])} caso(s) o script(s) mal planteados ({', '.join(d['case_id'] for d in count['test_issue'])}): al corregir la matriz, "
                         "los scripts o las consultas se crea una versión nueva y la ejecución queda desactualizada para repetirla")
        if count["environment"]:
            parts.append(f"{len(count['environment'])} fallo(s) de ambiente: revísalo y vuelve a ejecutar")
        unlocked = " El pull request ya puede aprobarse." if "pipeline" in self.state.artifacts else ""
        return {"intent": "aprobar", "reply": "Registré tu revisión a tu nombre (RT-02): " + "; ".join(parts) + "." + unlocked, "artifact": self._artifact("triage"),
                "with_resume": True}

    def _review_prompt(self, intro: str) -> dict[str, Any]:
        action = next((a for a in flow_view(self.state)["next"] if a["type"] == "review_defects"), None)
        if not action:
            return {"intent": "conversar", "reply": "No hay fallos pendientes de revisar.", "with_resume": True}
        lines = " ".join(f"{d['id']} ({d['case_id']}): sugiero «{DEFECT_DECISIONS[d['suggested']].lower()}» porque {d['suggested_reason'][0].lower()}{d['suggested_reason'][1:]}"
                         for d in action["defects"][:3])
        more = f" Y {len(action['defects']) - 3} más en la tarjeta." if len(action["defects"]) > 3 else ""
        return {"intent": "aclarar", "reply": f"{intro} {lines}{more}", "actions": [action], "artifact": self._artifact("triage")}

    async def continue_flow(self) -> dict[str, Any]:
        view = flow_view(self.state)
        first = next(iter(view["next"]), None)
        if not first:
            return {"intent": "conversar", "reply": "El flujo de esta historia está completo. ¿Quieres revisar algún artefacto o empezar una nueva HU?"}
        if first["type"] == "run":
            return await self.run(first["goal"], {})
        if first["type"] == "approve":
            # RT-02: "siguiente" nunca aprueba por el usuario; se pide la confirmación explícita.
            record = self.state.artifacts[first["artifact"]]
            return {"intent": "aclarar", "actions": [first],
                    "reply": f"El siguiente paso es aprobar {_the(first['artifact'])} v{record.version}. ¿Lo revisaste y das tu aprobación?"}
        if first["type"] == "review_defects":
            return self._review_prompt("El siguiente paso es revisar los fallos de la ejecución antes del pull request.")
        if first["type"] == "decide_suggestions":
            return {"intent": "aclarar", "reply": "El siguiente paso es decidir qué sugerencias INVEST aplicar. Márcalas abajo y genero la nueva versión.", "actions": [first]}
        if first["type"] == "input":
            return {"intent": "aclarar", "reply": " ".join(p["question"] for p in first["params"]), "actions": [first]}
        return self.resume()

    def _suggest_performance(self) -> dict[str, Any]:
        """HU-005 regla 6 y HU-008A: con riesgo alto (o requisitos de rendimiento en la HU) se sugiere, sin forzar, una prueba de performance."""
        reason = performance_reason(self.state)
        if not reason or "performance_design" in self.state.artifacts:
            return {}
        options = [("load", "Prueba de carga"), ("stress", "Prueba de estrés"), ("spike", "Prueba de picos")]
        return {"reply_suffix": f" Te sugiero (sin ser obligatorio) diseñar una prueba de performance: {reason}. ¿De qué tipo la preparo?",
                "actions": [{"type": "input", "goal": "performance_design", "label": label, "preset": {"performance_type": kind},
                             "params": [param_spec(n) for n in ("performance_users", "performance_duration_seconds", "performance_sla_ms") if n not in self.state.params]}
                            for kind, label in options] + flow_view(self.state)["next"][:2]}

    # --- Herramientas del panel: responden según la conversación y la HU en curso -------------------------

    async def tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        story = self.state.artifacts.get("story") if self.state else None
        title = story.payload.get("title") if story else None
        if name == "exportar_matriz":
            return self.export_matrix()
        if name == "validar_bd":
            # HU-011 dentro del flujo: con una HU y su matriz, las consultas son un paso versionado con verificación humana.
            if self.state and "matrix" in self.state.artifacts:
                return await self.run("data_validation", {})
            return await self.validate_database()
        if name in {"generar_pipeline_azure", "disenar_prueba_performance"}:
            goal = "pipeline" if name == "generar_pipeline_azure" else "performance_design"
            if self.state:
                return await self.run(goal, args)
            return await self.ask({"generar_pipeline_azure": "Genera el pipeline de Azure DevOps", "disenar_prueba_performance": "Diseña una prueba de performance"}[name])
        if name in {"redactar_historia", "iniciar_flujo_historia"}:
            if story:
                return {"intent": "aclarar", "reply": f"Ya estamos trabajando en la HU «{title}» v{story.version}. ¿Quieres empezar una historia nueva?",
                        "actions": [{"type": "new_story", "label": "Sí, empezar una HU nueva"}, {"type": "resume", "label": "No, sigamos con la actual"}]}
            return {"intent": "conversar", "reply": "Cuéntame el requerimiento: quién lo usa, qué necesita hacer y qué resultado espera. Lo convierto en una historia de usuario."}
        tool = self.c.assistant.toolbox.get(name)
        if not tool:
            return {"intent": "aclarar", "reply": "No encontré esa herramienta. Pregúntame qué puedo hacer y te muestro la lista completa."}
        args = self._contextual_args(name, args, title)
        missing = [p for p in tool.params if p.required and p.name not in args]
        if missing:
            return {"intent": "aclarar", "reply": f"Para usar «{tool.title}» necesito un dato.",
                    "actions": [{"type": "tool_input", "name": name, "label": tool.title,
                                 "params": [{"name": p.name, "question": p.ask or p.description, "choices": list(p.choices), "multiline": p.name == "script"} for p in missing]}]}
        ctx = ToolContext(actor=self.actor, namespace=self.namespace, session_id=self.session_id, trace_id=self.trace_id, workflow_id=self.state.id if self.state else None)
        try:
            result = await tool.handler(tool.validate(args), ctx)
        except Exception as exc:  # noqa: BLE001 - una herramienta que falla no rompe la conversación
            event(self.c.logger, logging.WARNING, "herramienta_panel_fallo", trace_id=self.trace_id, tool=name, error_type=type(exc).__name__)
            return {"intent": "error", "retryable": True, "reply": f"No pude usar «{tool.title}» en este momento. ¿Lo intentamos de nuevo?"}
        summary = tool.summarize(result).text if tool.summarize else str(result)
        context = f" (en contexto de la HU «{title}»)" if title and name in {"herramienta_bd", "herramienta_automatizacion", "buscar_memoria"} else ""
        return {"intent": "responder", "reply": summary + context, "with_resume": bool(self.state), "assistant": {"status": "answered", "tools_used": [name], "mode": "tool", "steps": []}}

    def _contextual_args(self, name: str, args: dict[str, Any], title: str | None) -> dict[str, Any]:
        """Completa los argumentos con lo que ya se sabe de la conversación y del flujo (memoria corta)."""
        params = self.state.params if self.state else {}
        framework = params.get("framework")
        if name == "herramienta_bd":
            language = {"restassured": "java", "playwright": "node", "postman-newman": "node", "selenium": "python"}.get(framework, "python")
            return {"motor": "postgresql", "lenguaje": language} | args
        if name == "herramienta_automatizacion" and (params.get("platform") or framework):
            platform = params.get("platform") or ("api" if framework in {"restassured", "postman-newman"} else "web")
            return {"plataforma": platform} | ({"herramienta": framework} if framework else {}) | args
        if name == "buscar_memoria" and title:
            return {"consulta": title} | args
        if name == "analizar_script_sql" and self.facts.get("last_sql"):
            return {"script": self.facts["last_sql"], "motor": "postgresql"} | args
        return args

    async def validate_database(self) -> dict[str, Any]:
        """HU-011: consultas de validación de datos derivadas de la HU en curso, analizadas y ejecutadas en la base sintética."""

        story = self.state.artifacts.get("story") if self.state else None
        if not story:
            return {"intent": "aclarar", "reply": "Para validar datos necesito saber qué reglas revisar. Cuéntame el requerimiento o pega la consulta SQL y la analizo.",
                    "actions": [{"type": "tool_input", "name": "analizar_script_sql", "label": "Análisis estático de SQL",
                                 "params": [{"name": "script", "question": "Pega la consulta SQL", "choices": [], "multiline": True}]}]}
        if not self.c.database_executor:
            return {"intent": "no_puedo", "reply": "La base sintética no está configurada en este entorno, así que no puedo ejecutar la validación."}
        body = UserStory.model_validate(story.payload).model_dump_json(include={"title", "description", "business_rules", "acceptance_criteria"})
        data = await self.c.service.llm.generate_json(system=SQL_VALIDATION_SYSTEM, user=f"Historia:\n{body}", schema={"type": "object"})
        results = []
        for n, query in enumerate(q for q in (data.get("queries") or [])[:3] if isinstance(q, dict)):
            sql = str(query.get("sql") or "").strip().rstrip(";")
            analysis = static_analyse_database_script(sql, "postgresql") if sql else {"passed": False, "statement_type": "empty", "findings": ["vacía"]}
            if not sql.lower().startswith("select") or analysis["statement_type"] != "read_only" or not analysis["passed"]:
                results.append({"purpose": query.get("purpose"), "sql": sql, "status": "bloqueada", "findings": analysis.get("findings") or ["no es de solo lectura"]})
                continue
            outcome, report = self.c.database_executor.execute(script=sql, case_id=f"HU-011-{story.version}-{n + 1}", trace_id=self.trace_id or "", output_format="pdf")
            if report and self.c.reports is not None:
                await self.c.reports.save(report)
            results.append({"purpose": query.get("purpose"), "sql": sql, "status": outcome.get("status"), "rows": (outcome.get("rows") or [])[:10],
                            "row_count": len(outcome.get("rows") or []), "report_id": (report or {}).get("id")})
            self.facts["last_sql"] = sql
        if not results:
            return {"intent": "error", "retryable": True, "reply": "No logré derivar consultas de validación de la historia. ¿Lo intentamos de nuevo?"}
        executed = [r for r in results if r["status"] == "completed"]
        reply = (f"Derivé {len(results)} consulta(s) de validación de la HU «{story.payload.get('title')}» v{story.version} y ejecuté {len(executed)} en la base sintética "
                 "(solo lectura, con evidencia descargable).")
        return {"intent": "paso", "reply": reply, "with_resume": True, "artifact": {"kind": "database_validation", "data": {"queries": results}}}

    def resume(self) -> dict[str, Any]:
        return {"intent": "conversar", "reply": resume_line(flow_view(self.state)) or "¿En qué te ayudo?"}

    def export_matrix(self) -> dict[str, Any]:
        matrix = self.state.artifacts.get("matrix") if self.state else None
        if not matrix:
            return {"intent": "aclarar", "reply": "Primero genero la matriz de pruebas.", "actions": [{"type": "run", "goal": "matrix", "label": RUN_LABELS["matrix"]}]}
        exported = export_test_cases_to_excel(matrix.payload["cases"], story_id=str(matrix.payload.get("story_id", "")))
        return {"intent": "paso", "reply": f"Exporté los {len(matrix.payload['cases'])} casos de la matriz v{matrix.version} a Excel.", "download": exported, "with_resume": True}

    # --- Peticiones fuera del flujo y conversación --------------------------------------------------------

    async def ask(self, message: str) -> dict[str, Any]:
        """Prioridad al humano: se atiende su petición con herramientas y después se retoma el flujo."""
        view = flow_view(self.state)
        context = f"CONTEXTO: {resume_line(view)}" if self.state else ""
        recalled = await self.c.memory.recall(message, task="agent", namespace=self.namespace)
        session_context = await self.c.memory.session_context(self.session_id)
        parts = [recall_prompt(recalled), f"SESIÓN ACTUAL:\n{session_context}" if session_context else "", context]
        answer = await self.c.assistant.ask(message, ctx=ToolContext(actor=self.actor, namespace=self.namespace, session_id=self.session_id, trace_id=self.trace_id,
                                                                     workflow_id=self.state.id if self.state else None), context="\n\n".join(p for p in parts if p))
        result = {"intent": "responder" if answer.status == "answered" else "no_puedo", "reply": answer.answer, "with_resume": bool(self.state),
                  "assistant": answer.model_dump(mode="json", exclude={"outputs"}), "memory": {"recalled": describe(recalled)}}
        if answer.outputs.get("story") and not self.state:
            # La skill de redacción produjo una HU: se adopta como inicio del flujo.
            story = answer.outputs["story"]
            self.state, _ = await self.c.workflows.adopt_story(story={k: story[k] for k in ("id", "title", "description", "business_rules", "acceptance_criteria", "version")},
                                                               requirement=message, actor=self.actor, namespace=self.namespace, trace_id=self.trace_id)
            result |= {"story": self.state.artifacts["story"].payload, "artifact": self._artifact("story")}
        if answer.outputs.get("pipeline_yaml"):
            result["artifact"] = {"kind": "pipeline", "data": {"yaml": answer.outputs["pipeline_yaml"]}}
        if answer.outputs.get("performance_plan"):
            result["artifact"] = {"kind": "performance_design", "data": answer.outputs["performance_plan"]}
        return result

    async def chat(self, message: str) -> dict[str, Any]:
        """Cortesía y comentarios: una respuesta breve con el contexto del flujo; nunca toca la historia."""
        context = f"ESTADO DEL FLUJO: {resume_line(flow_view(self.state))}\n\n" if self.state else ""
        data = await self.c.service.llm.generate_json(system=SMALLTALK_SYSTEM, user=f"{context}Mensaje nuevo del usuario:\n{message}", schema={"type": "object"})
        reply = str(data.get("reply") or "").strip() or "¡Con gusto! ¿En qué te ayudo?"
        return {"intent": "conversar", "reply": reply, "with_resume": False}

    # --- Utilidades ------------------------------------------------------------------------------------

    def _pending_suggestions(self) -> list[str]:
        invest = self.state.artifacts.get("invest") if self.state else None
        if not invest or invest.approved:
            return []
        return [c["name"] for c in actionable_suggestions(invest.payload.get("criteria", []))]

    def _waiting_inputs(self) -> tuple[str, list[str]] | None:
        for action in flow_view(self.state)["next"]:
            if action["type"] == "input":
                return action["goal"], [p["name"] for p in action["params"]]
        return None

    def _artifact(self, key: str, **extra: Any) -> dict[str, Any]:
        record = self.state.artifacts[key]
        return {"kind": key, "version": record.version, "approved": record.approved, "data": record.payload, "warnings": record.warnings,
                "assumptions": record.assumptions} | extra

    def _also(self, produced: list[str], goal: str) -> str:
        others = [LABELS[k].lower() for k in produced if k != goal and k in LABELS and k != "triage"]
        return f" También regeneré {', '.join(others)} porque dependían de este paso." if others else ""

    def _describe(self, goal: str, *, fresh: bool) -> str:
        record = self.state.artifacts[goal]
        p = record.payload
        prefix = "Listo" if fresh else "Ya lo tenía al día"
        if goal == "invest":
            ok = sum(c["status"] == "cumple" for c in p["criteria"])
            pending = [c["name"] for c in actionable_suggestions(p["criteria"])]
            return f"{prefix}: evalué la HU v{p['story_version']} con INVEST y cumple {ok} de 6 criterios." + (
                f" Hay sugerencias para {', '.join(pending)}: decide cuáles aplicar y genero la nueva versión." if pending else " No hay sugerencias pendientes.")
        if goal == "matrix":
            types = {t: sum(c["type"] == t for c in p["cases"]) for t in ("positive", "negative", "edge")}
            return (f"{prefix}: la matriz v{record.version} tiene {len(p['cases'])} casos ({types['positive']} positivos, {types['negative']} negativos y "
                    f"{types['edge']} de borde) para la HU v{p['story_version']}. Revísala y apruébala para seguir.")
        if goal == "risk":
            level = {"low": "bajo", "medium": "medio", "high": "alto"}.get(p.get("level"), p.get("level"))
            score = f" ({p['score_total']} de 15)" if p.get("score_total") else ""
            return f"{prefix}: el riesgo de la HU es {level}{score}. {p.get('mitigation', '')}".strip()
        if goal == "automation":
            total = sum(len(b["case_ids"]) for b in p["batches"])
            files = sum(len(b.get("scripts", {})) for b in p["batches"])
            quality = all(b.get("quality", {}).get("pr_allowed", True) for b in p["batches"])
            stack = f"{FRAMEWORK_NAMES.get(p.get('framework'), p.get('framework'))} ({p.get('platform')})"
            return (f"{prefix}: generé el código de {total} casos en {stack}: {files} archivos con Page Object, datos externalizados y un script por caso "
                    f"con su id. Lint y detección de secretos: {'sin hallazgos' if quality else 'con hallazgos, el PR queda bloqueado'}. "
                    "Revísalos y apruébalos para abrir el pull request (sin commit directo).")
        if goal == "execution":
            summary = p["summary"]
            outcome = "todos aprobados" if summary["failed"] == 0 else f"{summary['failed']} fallido(s): revisa la evidencia"
            if summary.get("errors"):
                outcome += f"; {summary['errors']} por error de consulta SQL, no por datos"
            parts = [f"{summary['api']} caso(s) de API con {FRAMEWORK_NAMES.get(p.get('framework'), p.get('framework'))}" if summary.get("api") else "",
                     f"{summary['database']} consulta(s) de datos (HU-011)" if summary.get("database") else "",
                     f"{summary['web']} caso(s) web" if summary.get("web") else ""]
            notes = [w for w in record.warnings if w.startswith(("Los scripts web", "Las consultas de datos no"))]
            where = f"recibí del pipeline de Azure DevOps (corrida {p.get('run_id')})" if p.get("source") == "azure-devops" else "ejecuté contra el entorno sintético"
            review = (f" Preparé {summary['failed']} borrador(es) de defecto, uno por caso fallido, con una clasificación sugerida: revísalos antes de aprobar "
                      "el pull request." if summary["failed"] else "")
            return (f"{prefix}: {where} {' y '.join(x for x in parts if x) or 'lo verificado'}: {summary['passed']} de {summary['total']} "
                    f"aprobados ({outcome}). La evidencia consolidada en PDF queda descargable." + (" " + " ".join(notes) if notes else "") + review)
        if goal == "triage":
            defects = p.get("defects", [])
            if not defects:
                return f"{prefix}: la ejecución v{p['execution_version']} no tuvo fallos que revisar."
            s = p["summary"]
            return (f"{prefix}: hay {len(defects)} fallo(s) de la ejecución v{p['execution_version']} por revisar. Sugerencia: {s['defect']} defecto(s), "
                    f"{s['test_issue']} caso(s) o script(s) mal planteados y {s['environment']} de ambiente. Tú decides cada uno; el PR se aprueba después.")
        if goal == "data_validation":
            ready = [q for q in p["queries"] if q["status"] == "lista"]
            blocked = len(p["queries"]) - len(ready)
            return (f"{prefix}: derivé {len(p['queries'])} consulta(s) de solo lectura de las reglas de la HU v{p['story_version']} y de la matriz v{p['matrix_version']}"
                    + (f"; {blocked} quedó bloqueada por el análisis estático" if blocked else "")
                    + ". Cada una indica el criterio y el caso que valida, y qué debe devolver si el sistema cumple. Revísalas y apruébalas para ejecutarlas (HU-010).")
        if goal == "pipeline":
            return f"{prefix}: el YAML del pipeline referencia {len(p.get('scripts') or [])} script(s). Se entrega por pull request para revisión de DevOps."
        if goal == "performance_design":
            return (f"{prefix}: diseñé un escenario {p.get('scenario_type')} con {p.get('users')} usuarios durante {p.get('duration_seconds')} s y SLA p95 de "
                    f"{p.get('target_sla_ms')} ms con {p.get('tool')}. No se ejecuta en esta etapa (HU-008B).")
        if goal == "azure_work_item":
            return f"{prefix}: preparé la vista previa del Work Item para el proyecto {p.get('project')}. Revisa los campos y apruébala para publicar."
        return f"{prefix}."


def _the(artifact: str) -> str:
    return {"story": "la historia de usuario", "matrix": "la matriz de pruebas", "azure_work_item": "el Work Item de Azure DevOps", "invest": "la evaluación INVEST",
            "automation": "los scripts de automatización", "execution": "la ejecución de los scripts", "triage": "la revisión de fallos", "data_validation": "las consultas de validación de datos", "pipeline": "el pipeline de Azure DevOps", "performance_design": "el diseño de la prueba de performance"}.get(
        artifact, LABELS.get(artifact, artifact).lower())


def _question(name: str) -> str:
    return INPUT_QUESTIONS.get(name, name)
