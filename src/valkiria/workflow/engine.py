"""Motor del flujo por historia: ejecuta el plan con puntos de control, reintentos y aislamiento de fallos.

Garantías:
- Tras cada paso el estado se guarda: un reinicio o un error posterior no pierde el trabajo hecho.
- Un fallo transitorio (LLM lento, validación fallida) se reintenta con espera exponencial.
- Un paso que falla no detiene a las ramas independientes; se reporta y se puede reanudar.
- Las aprobaciones humanas detienen solo lo que depende de ellas y se validan sobre versión y hash exactos.
- Memoria: antes de cada paso se recuperan recuerdos validados relevantes; las decisiones humanas (aprobar,
  rechazar, editar) y los fallos definitivos se guardan como memoria de largo plazo para las próximas historias.
  Si la memoria falla, el flujo continúa sin ella.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from valkiria.application.prompts import PROMPT_VERSION
from valkiria.application.qa_artifacts import PolicyViolation
from valkiria.application.use_cases import ValkiriaService, _story_changes
from valkiria.assistant.tools import ToolContext
from valkiria.domain.models import TestMatrix, UserStory
from valkiria.infrastructure.logging import event, get_logger
from valkiria.memory.long_term import MemoryKind
from valkiria.memory.service import (
    KINDS_BY_TASK,
    MemoryService,
    describe,
    recall_prompt,
)
from valkiria.providers.openai_compatible import LLMProviderError
from valkiria.workflow.graph import CAPABILITIES, artifact_of, detect_goals
from valkiria.workflow.planner import WorkflowPlan, actionable_suggestions, plan
from valkiria.workflow.state import Approval, StepFailure, WorkflowState, WorkflowStore
from valkiria.workflow.steps import EXECUTORS, StepError

DEFAULT_GOALS = ["story", "invest"]
EDITABLE = {"story": UserStory, "matrix": TestMatrix}


class WorkflowNotFound(LookupError):
    pass


class WorkflowConflict(ValueError):
    """La operación no aplica al estado actual (por ejemplo, aprobar una versión que ya no es la vigente)."""


class WorkflowEngine:
    def __init__(self, store: WorkflowStore, service: ValkiriaService, *, memory: MemoryService | None = None, max_attempts: int = 3, backoff_seconds: float = 1.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.store = store
        self.service = service
        self.memory = memory
        # Se asigna después de construir el catálogo de herramientas (que a su vez usa este motor).
        self.assistant: Any = None
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep
        self.logger = get_logger("valkiria.workflow")
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, workflow_id: str) -> asyncio.Lock:
        return self._locks.setdefault(workflow_id, asyncio.Lock())

    async def _load(self, workflow_id: str) -> WorkflowState:
        state = await self.store.get(workflow_id)
        if not state:
            raise WorkflowNotFound(workflow_id)
        return state

    @staticmethod
    def _merge_goals(current: list[str], new: list[str]) -> list[str]:
        return current + [goal for goal in new if goal not in current]

    async def start(self, *, request: str | None, goals: list[str] | None, params: dict[str, Any], actor: str, trace_id: str | None = None) -> tuple[WorkflowState, WorkflowPlan]:
        detected = list(goals or (detect_goals(request) if request else []) or DEFAULT_GOALS)
        state = WorkflowState(actor=actor, goals=[], params=dict(params))
        if trace_id:
            state.trace_id = trace_id
        if request:
            state.params.setdefault("requirement", request)
        state.goals = self._merge_goals([], detected)
        state.think("goals", f"Objetivos: {', '.join(state.goals)} ({'indicados' if goals else 'detectados en la petición' if request and detect_goals(request) else 'por defecto'}).")
        state = await self.store.save(state)
        async with self._lock(state.id):
            return await self._run(state)

    async def request(self, workflow_id: str, *, request: str | None, goals: list[str] | None, params: dict[str, Any], actor: str) -> tuple[WorkflowState, WorkflowPlan]:
        """Continúa un flujo existente con nuevas tareas o datos, conservando lo ya generado."""
        async with self._lock(workflow_id):
            state = await self._load(workflow_id)
            new_goals = list(goals or (detect_goals(request) if request else []))
            state.params.update(params)
            if new_goals:
                state.goals = self._merge_goals(state.goals, new_goals)
                state.think("goals", f"{actor} agregó objetivos: {', '.join(new_goals)}.")
            elif request and self.assistant and not params:
                # Fuera del flujo programado: se razona y se responde con herramientas en vez de ignorarla.
                await self._answer(state, request, actor)
            elif request:
                state.think("goals", "La petición no contiene tareas reconocibles; se continúa con los objetivos vigentes.")
            if params:
                state.think("input", f"{actor} aportó datos: {', '.join(sorted(params))}.")
            return await self._run(state)

    async def resume(self, workflow_id: str) -> tuple[WorkflowState, WorkflowPlan]:
        async with self._lock(workflow_id):
            state = await self._load(workflow_id)
            state.think("resume", "Reanudación solicitada; se reintentan los pasos fallidos.")
            return await self._run(state)

    async def approve(self, workflow_id: str, *, artifact: str, version: int, content_hash: str | None, decision: str, actor: str, comment: str = "",
                      suggestions: dict[str, str] | None = None, assumptions_confirmed: bool = False) -> tuple[WorkflowState, WorkflowPlan]:
        async with self._lock(workflow_id):
            state = await self._load(workflow_id)
            record = state.artifacts.get(artifact)
            if not record:
                raise WorkflowConflict(f"artifact_not_found:{artifact}")
            if artifact not in CAPABILITIES or not CAPABILITIES[artifact].approvable:
                raise WorkflowConflict(f"artifact_not_approvable:{artifact}")
            if record.version != version or (content_hash and record.content_hash != content_hash):
                raise WorkflowConflict(f"stale_version:{artifact}:current=v{record.version}")
            details: dict[str, Any] = {}
            if artifact == "story" and decision == "approved" and record.assumptions:
                # HU-003B, regla 4: los supuestos del borrador se confirman explícitamente antes de aprobar.
                if not assumptions_confirmed:
                    raise WorkflowConflict("assumptions_confirmation_required:" + " | ".join(record.assumptions))
                details["assumptions_confirmed"] = record.assumptions
            if artifact == "invest" and decision == "approved":
                pending = [c["name"] for c in actionable_suggestions(record.payload.get("criteria", []))]
                decided = {name: value for name, value in (suggestions or {}).items() if name in pending and value in {"approved", "rejected"}}
                if set(decided) != set(pending):
                    raise WorkflowConflict("suggestion_decisions_required:" + ",".join(sorted(set(pending) - set(decided))))
                details["suggestions"] = decided
            record.approval = Approval(version=record.version, content_hash=record.content_hash, decision=decision, actor=actor, comment=comment, details=details)
            state.think("approval", f"{actor} {'aprobó' if decision == 'approved' else 'rechazó'} '{artifact}' v{record.version}" + (f": {comment}" if comment else "") + ".", artifact)
            await self._learn_from_approval(state, artifact, decision, actor, comment, details)
            return await self._run(state)

    async def adopt_story(self, *, story: dict[str, Any], requirement: str, actor: str, namespace: str = "default", assumptions: list[str] | None = None,
                          warnings: list[str] | None = None, trace_id: str | None = None) -> tuple[WorkflowState, WorkflowPlan]:
        """Inicia un flujo con una HU ya redactada en la conversación (HU-003B), sin volver a generarla."""
        state = WorkflowState(actor=actor, goals=["story"], params={"requirement": requirement, "namespace": namespace})
        if trace_id:
            state.trace_id = trace_id
        state.put_artifact("story", story, produced_by="story", based_on={}, warnings=["Borrador generado por IA (HU-003B)."] + (warnings or []),
                           assumptions=assumptions, model=getattr(self.service.llm, "model_name", None), prompt_version=PROMPT_VERSION)
        state.think("executed", f"HU-003B: HU '{story.get('title')}' redactada en la conversación. Resultado: 'story' v1.", "story")
        state = await self.store.save(state)
        async with self._lock(state.id):
            return await self._run(state)

    async def revise_story(self, workflow_id: str, *, payload: dict[str, Any], actor: str, instruction: str,
                           produced_by: str = "ai_edit") -> tuple[WorkflowState, WorkflowPlan]:
        """Toda modificación de la HU crea la versión N+1 (HU-003A): la anterior queda en el historial y lo que dependía de ella se regenera."""
        async with self._lock(workflow_id):
            state = await self._load(workflow_id)
            current = state.artifacts.get("story")
            if not current:
                raise WorkflowConflict("artifact_not_editable:story")
            payload = payload | {"id": current.payload["id"], "version": current.version + 1}
            try:
                UserStory.model_validate(payload)
            except ValidationError as exc:
                raise WorkflowConflict(f"invalid_story:{exc.error_count()}_errors") from exc
            note = f"Nueva versión generada por IA a partir de la instrucción: \"{instruction[:200]}\"." if produced_by == "ai_edit" else f"Editado por {actor}."
            record = state.put_artifact("story", payload, produced_by=produced_by, based_on=current.based_on, warnings=["Borrador generado por IA.", note],
                                        model=getattr(self.service.llm, "model_name", None), prompt_version=PROMPT_VERSION)
            state.think("edit", f"{actor} pidió modificar la HU; queda v{record.version} pendiente de aprobación.", "story")
            await self._remember(state, "po_preference", f"Al refinar la HU '{payload.get('title')}', el PO pidió: {instruction[:300]}", "edit:story", actor, ["story"])
            return await self._run(state)

    async def edit(self, workflow_id: str, *, artifact: str, payload: dict[str, Any], actor: str) -> tuple[WorkflowState, WorkflowPlan]:
        """Edición humana de la HU o la matriz: crea una versión nueva y vuelve a exigir aprobación."""
        async with self._lock(workflow_id):
            state = await self._load(workflow_id)
            if artifact not in EDITABLE or artifact not in state.artifacts:
                raise WorkflowConflict(f"artifact_not_editable:{artifact}")
            current = state.artifacts[artifact]
            identity = {"id": current.payload["id"], "version": current.version + 1} if artifact == "story" else {"story_id": current.payload["story_id"]}
            payload = payload | identity
            try:
                EDITABLE[artifact].model_validate(payload)
            except ValidationError as exc:
                raise WorkflowConflict(f"invalid_{artifact}:{exc.error_count()}_errors") from exc
            record = state.put_artifact(artifact, payload, produced_by="manual_edit", based_on=current.based_on, warnings=[f"Editado por {actor}."])
            state.think("edit", f"{actor} editó '{artifact}'; queda v{record.version} pendiente de aprobación.", artifact)
            await self._learn_from_edit(state, artifact, current.payload, payload, actor)
            return await self._run(state)

    async def get(self, workflow_id: str) -> tuple[WorkflowState, WorkflowPlan]:
        state = await self._load(workflow_id)
        return state, plan(state, state.goals)

    async def _execute(self, state: WorkflowState, key: str) -> bool:
        executor = EXECUTORS[key]
        recalled = await self._recall(state, key)
        memory = recall_prompt(recalled)
        for attempt in range(1, self.max_attempts + 1):
            try:
                output = await executor(state, self.service, memory)
            except (StepError, LLMProviderError, TimeoutError, ValidationError, ValueError) as exc:
                code = getattr(exc, "code", type(exc).__name__)
                # Una violación de política es determinista: reintentar daría el mismo resultado.
                retryable = exc.retryable if isinstance(exc, StepError) else not isinstance(exc, PolicyViolation)
                event(self.logger, logging.WARNING, "paso_fallido", workflow_id=state.id, trace_id=state.trace_id, capability=key, attempt=attempt, error=code)
                if retryable and attempt < self.max_attempts:
                    await self.sleep(self.backoff_seconds * 2 ** (attempt - 1))
                    continue
                state.failures[key] = StepFailure(capability=key, error_code=str(code), message=str(exc)[:500], attempts=attempt, retryable=retryable)
                state.think("failed", f"Falló tras {attempt} intento(s): {str(exc)[:200]}", key)
                if not retryable:
                    # Un fallo determinista es una lección: la próxima vez se puede anticipar (por ejemplo, dividir la HU).
                    await self._remember(state, "lesson", f"{CAPABILITIES[key].hu} ({key}) falló por {code}: {str(exc)[:300]}", f"failure:{key}", "valkiria", [key, str(code)])
                return False
            # Frontera de aislamiento: un error inesperado en un paso no debe tumbar el flujo ni filtrar detalles.
            except Exception as exc:  # noqa: BLE001
                event(self.logger, logging.ERROR, "paso_error_interno", workflow_id=state.id, trace_id=state.trace_id, capability=key, error_type=type(exc).__name__)
                state.failures[key] = StepFailure(capability=key, error_code="internal_error", message="Error interno; consulta el trace_id.", attempts=attempt, retryable=False)
                state.think("failed", "Error interno no previsto; el resto del flujo continúa.", key)
                return False
            record = state.put_artifact(artifact_of(key), output.payload, produced_by=key, based_on=output.based_on, warnings=output.warnings, memory_used=describe(recalled),
                                        assumptions=output.assumptions, model=getattr(self.service.llm, "model_name", None), prompt_version=PROMPT_VERSION)
            state.think("executed", f"{CAPABILITIES[key].hu}: {output.summary} Resultado: '{record.key}' v{record.version}.", key)
            return True
        return False

    async def _answer(self, state: WorkflowState, request: str, actor: str) -> None:
        story = state.artifacts.get("story")
        context = f"CONTEXTO: flujo {state.id}, objetivos {', '.join(state.goals)}" + (f", HU '{story.payload.get('title')}' v{story.version}" if story else "") + "."
        answer = await self.assistant.ask(request, ctx=ToolContext(actor=actor, namespace=self._namespace(state), trace_id=state.trace_id, workflow_id=state.id), context=context)
        state.answers = [*state.answers, {"request": request, "actor": actor, **answer.model_dump(mode="json", exclude={"capabilities"})}][-10:]
        verdict = "respondida con " + ", ".join(answer.tools_used) if answer.tools_used else "respondida sin herramientas" if answer.status == "answered" else "fuera de las capacidades"
        state.think("assistant", f"Petición fuera del flujo {verdict}.")

    @staticmethod
    def _namespace(state: WorkflowState) -> str:
        return str(state.params.get("namespace") or "default")

    @staticmethod
    def _query(state: WorkflowState) -> str:
        story = state.artifacts.get("story")
        parts = [str(state.params.get("requirement", ""))]
        if story:
            parts += [str(story.payload.get("title", "")), str(story.payload.get("description", ""))]
        return " ".join(parts)

    async def _recall(self, state: WorkflowState, key: str) -> list:
        if not self.memory or key not in KINDS_BY_TASK:
            return []
        try:
            recalled = await self.memory.recall(self._query(state), task=key, namespace=self._namespace(state))
        # La memoria enriquece, pero no es requisito: si su almacenamiento falla, el paso sigue sin ella.
        except Exception as exc:  # noqa: BLE001
            event(self.logger, logging.WARNING, "memoria_no_disponible", workflow_id=state.id, trace_id=state.trace_id, capability=key, error_type=type(exc).__name__)
            state.think("memory", "Memoria de largo plazo no disponible; se continúa sin ella.", key)
            return []
        if recalled:
            state.think("memory", f"Se consideraron {len(recalled)} recuerdo(s) validados: " + ", ".join(sorted({r.record.kind for r in recalled})) + ".", key)
        return recalled

    async def _remember(self, state: WorkflowState, kind: MemoryKind, content: str, source: str, actor: str, tags: list[str] | None = None) -> None:
        if not self.memory:
            return
        try:
            await self.memory.remember(kind=kind, content=content, source=f"workflow:{state.id}:{source}", actor=actor, namespace=self._namespace(state), tags=tags)
        except Exception as exc:  # noqa: BLE001
            event(self.logger, logging.WARNING, "memoria_no_guardada", workflow_id=state.id, trace_id=state.trace_id, kind=kind, error_type=type(exc).__name__)
            return
        state.think("memory", f"Aprendizaje guardado en memoria de largo plazo ({kind}).")

    async def _learn_from_approval(self, state: WorkflowState, artifact: str, decision: str, actor: str, comment: str, details: dict[str, Any]) -> None:
        story = state.artifacts.get("story")
        title = str(story.payload.get("title", "")) if story else ""
        if artifact == "story" and decision == "approved" and story:
            criteria = "; ".join(str(c.get("text", "")) for c in story.payload.get("acceptance_criteria", []))
            rules = "; ".join(str(r) for r in story.payload.get("business_rules", []))
            await self._remember(state, "approved_story", f"HU aprobada '{title}': {story.payload.get('description', '')} Reglas: {rules or 'ninguna'}. Criterios: {criteria}",
                                 f"approval:story:v{story.version}", actor, ["hu_aprobada"])
        if artifact == "invest" and decision == "approved":
            criteria = {c["name"]: c for c in state.artifacts["invest"].payload.get("criteria", [])}
            for name, verdict in details.get("suggestions", {}).items():
                suggestion = criteria.get(name, {}).get("suggestion") or ""
                action = "aceptó" if verdict == "approved" else "rechazó"
                await self._remember(state, "po_preference", f"Para el criterio INVEST {name}, el PO {action} la sugerencia: \"{suggestion}\" (HU '{title}').",
                                     f"approval:invest:{name}", actor, ["invest", name])
        if decision == "rejected" and comment:
            await self._remember(state, "human_correction", f"{actor} rechazó '{artifact}' de la HU '{title}': {comment}", f"rejection:{artifact}", actor, [artifact])

    async def _learn_from_edit(self, state: WorkflowState, artifact: str, before: dict[str, Any], after: dict[str, Any], actor: str) -> None:
        if artifact == "story":
            changes = _story_changes(UserStory.model_validate(before), UserStory.model_validate(after))
            changed = [k.removesuffix("_changed") for k, v in changes.items() if k.endswith("_changed") and v]
            detail = f"título '{before.get('title')}' → '{after.get('title')}'; " if changes["title_changed"] else ""
            content = (f"{actor} corrigió la HU generada por IA ({', '.join(changed) or 'criterios'}): {detail}criterios agregados {changes['criteria_added']}, "
                       f"eliminados {changes['criteria_removed']}. Versión corregida: '{after.get('title')}': {after.get('description', '')}")
        else:
            content = f"{actor} corrigió la matriz de la HU '{state.artifacts['story'].payload.get('title', '') if 'story' in state.artifacts else ''}': {len(before.get('cases', []))} → {len(after.get('cases', []))} casos."
        await self._remember(state, "human_correction", content, f"edit:{artifact}", actor, [artifact])

    async def _run(self, state: WorkflowState) -> tuple[WorkflowState, WorkflowPlan]:
        exhausted: set[str] = set()
        # Cota de seguridad: cada capacidad puede ejecutarse a lo sumo dos veces por llamada.
        for _ in range(2 * len(CAPABILITIES)):
            current = plan(state, state.goals, exhausted=exhausted)
            if not current.runnable:
                break
            step = current.runnable[0]
            state.think(step.action, step.reason, step.capability)
            ok = await self._execute(state, step.capability)
            if not ok:
                exhausted.add(step.capability)
            state = await self.store.save(state)
        final = plan(state, state.goals, exhausted=exhausted)
        summary = {"completed": "Todos los objetivos están cumplidos.", "waiting_approval": "Se requiere aprobación humana para continuar: " + ", ".join(final.pending_approvals) + ".",
                   "needs_input": "Faltan datos: " + ", ".join(final.missing_inputs) + ".", "failed": "Hay pasos fallidos; se pueden reanudar.",
                   "partially_completed": "Algunas capacidades solicitadas aún no están disponibles."}.get(final.status, final.status)
        state.think("status", summary)
        state = await self.store.save(state)
        return state, final


def view(state: WorkflowState, current: WorkflowPlan, *, reasoning_limit: int = 40) -> dict[str, Any]:
    return {
        "id": state.id,
        "trace_id": state.trace_id,
        "status": current.status,
        "goals": state.goals,
        "plan": [step.as_dict() for step in current.steps],
        "next_actions": current.next_actions(state),
        "artifacts": {key: {"version": r.version, "content_hash": r.content_hash, "approved": r.approved,
                            "requires_approval": bool(key in CAPABILITIES and CAPABILITIES[key].approvable and not r.approved),
                            "assumptions": r.assumptions, "model": r.model, "prompt_version": r.prompt_version,
                            "pending_suggestions": [c["name"] for c in actionable_suggestions(r.payload.get("criteria", []))] if key == "invest" and not r.approved else [],
                            "produced_by": r.produced_by, "based_on": r.based_on, "memory_used": r.memory_used,
                            "warnings": r.warnings, "payload": r.payload} for key, r in state.artifacts.items()},
        "failures": {key: f.model_dump(mode="json") for key, f in state.failures.items()},
        "reasoning": [r.model_dump(mode="json") for r in state.reasoning[-reasoning_limit:]],
        "answers": state.answers[-3:],
        "revision": state.revision,
    }
