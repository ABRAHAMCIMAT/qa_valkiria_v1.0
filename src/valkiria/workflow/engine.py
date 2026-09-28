"""Motor del flujo por historia: ejecuta el plan con puntos de control, reintentos y aislamiento de fallos.

Garantías:
- Tras cada paso el estado se guarda: un reinicio o un error posterior no pierde el trabajo hecho.
- Un fallo transitorio (LLM lento, validación fallida) se reintenta con espera exponencial.
- Un paso que falla no detiene a las ramas independientes; se reporta y se puede reanudar.
- Las aprobaciones humanas detienen solo lo que depende de ellas y se validan sobre versión y hash exactos.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from valkiria.application.qa_artifacts import PolicyViolation
from valkiria.application.use_cases import ValkiriaService
from valkiria.domain.models import TestMatrix, UserStory
from valkiria.infrastructure.logging import event, get_logger
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
    def __init__(self, store: WorkflowStore, service: ValkiriaService, *, max_attempts: int = 3, backoff_seconds: float = 1.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.store = store
        self.service = service
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
                      suggestions: dict[str, str] | None = None) -> tuple[WorkflowState, WorkflowPlan]:
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
            if artifact == "invest" and decision == "approved":
                pending = [c["name"] for c in actionable_suggestions(record.payload.get("criteria", []))]
                decided = {name: value for name, value in (suggestions or {}).items() if name in pending and value in {"approved", "rejected"}}
                if set(decided) != set(pending):
                    raise WorkflowConflict("suggestion_decisions_required:" + ",".join(sorted(set(pending) - set(decided))))
                details["suggestions"] = decided
            record.approval = Approval(version=record.version, content_hash=record.content_hash, decision=decision, actor=actor, comment=comment, details=details)
            state.think("approval", f"{actor} {'aprobó' if decision == 'approved' else 'rechazó'} '{artifact}' v{record.version}" + (f": {comment}" if comment else "") + ".", artifact)
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
            return await self._run(state)

    async def get(self, workflow_id: str) -> tuple[WorkflowState, WorkflowPlan]:
        state = await self._load(workflow_id)
        return state, plan(state, state.goals)

    async def _execute(self, state: WorkflowState, key: str) -> bool:
        executor = EXECUTORS[key]
        for attempt in range(1, self.max_attempts + 1):
            try:
                output = await executor(state, self.service)
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
                return False
            # Frontera de aislamiento: un error inesperado en un paso no debe tumbar el flujo ni filtrar detalles.
            except Exception as exc:  # noqa: BLE001
                event(self.logger, logging.ERROR, "paso_error_interno", workflow_id=state.id, trace_id=state.trace_id, capability=key, error_type=type(exc).__name__)
                state.failures[key] = StepFailure(capability=key, error_code="internal_error", message="Error interno; consulta el trace_id.", attempts=attempt, retryable=False)
                state.think("failed", "Error interno no previsto; el resto del flujo continúa.", key)
                return False
            record = state.put_artifact(artifact_of(key), output.payload, produced_by=key, based_on=output.based_on, warnings=output.warnings)
            state.think("executed", f"{CAPABILITIES[key].hu}: {output.summary} Resultado: '{record.key}' v{record.version}.", key)
            return True
        return False

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
                            "pending_suggestions": [c["name"] for c in actionable_suggestions(r.payload.get("criteria", []))] if key == "invest" and not r.approved else [],
                            "produced_by": r.produced_by, "based_on": r.based_on,
                            "warnings": r.warnings, "payload": r.payload} for key, r in state.artifacts.items()},
        "failures": {key: f.model_dump(mode="json") for key, f in state.failures.items()},
        "reasoning": [r.model_dump(mode="json") for r in state.reasoning[-reasoning_limit:]],
        "revision": state.revision,
    }
