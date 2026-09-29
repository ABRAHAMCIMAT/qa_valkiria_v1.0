"""Planificador determinista: decide qué hacer con cada capacidad y explica por qué.

Para cada objetivo recorre sus dependencias y clasifica cada capacidad en una acción:
- reuse: el artefacto existe y está al día con sus dependencias.
- run / rerun: falta o quedó desactualizado (cambió la versión de una dependencia).
- blocked: espera a otra capacidad o a una aprobación humana.
- needs_input: faltan datos que solo el usuario puede aportar.
- skipped: no aplica (por ejemplo, no hay sugerencias INVEST aprobadas).
- unavailable: la capacidad aún no está implementada.
- failed: agotó sus reintentos en esta ejecución.
Una capacidad ya satisfecha no fuerza a recalcular sus dependencias.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from valkiria.workflow.graph import CAPABILITIES, TOPOLOGICAL_ORDER, artifact_of
from valkiria.workflow.state import WorkflowState

Action = Literal["reuse", "run", "rerun", "blocked", "needs_input", "skipped", "unavailable", "failed"]

INPUT_QUESTIONS = {
    "requirement": "¿Cuál es el requerimiento en lenguaje natural para redactar la historia?",
    "repository": "¿En qué repositorio se abrirá el pull request con los scripts de automatización?",
    "performance_users": "¿Cuántos usuarios virtuales debe simular la prueba de performance?",
    "performance_duration_seconds": "¿Cuánto debe durar la prueba de performance, en segundos?",
    "performance_sla_ms": "¿Cuál es el SLA de latencia p95, en milisegundos?",
    "azure_project": "¿En qué proyecto de Azure DevOps se publicará el Work Item?",
}


@dataclass
class PlanStep:
    capability: str
    hu: str
    action: Action
    reason: str
    blocked_by: list[str] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"capability": self.capability, "hu": self.hu, "action": self.action, "reason": self.reason, "blocked_by": self.blocked_by, "missing_inputs": self.missing_inputs}


@dataclass
class WorkflowPlan:
    goals: list[str]
    steps: list[PlanStep]

    @property
    def runnable(self) -> list[PlanStep]:
        return [step for step in self.steps if step.action in {"run", "rerun"}]

    @property
    def pending_approvals(self) -> list[str]:
        return sorted({blocker.removeprefix("approval:") for step in self.steps for blocker in step.blocked_by if blocker.startswith("approval:")})

    @property
    def missing_inputs(self) -> list[str]:
        return sorted({name for step in self.steps for name in step.missing_inputs})

    @property
    def status(self) -> str:
        actions = {step.action for step in self.steps}
        if self.runnable:
            return "running"
        if self.pending_approvals:
            return "waiting_approval"
        if self.missing_inputs:
            return "needs_input"
        if "failed" in actions:
            return "failed"
        if "unavailable" in actions:
            return "partially_completed"
        return "completed"

    def next_actions(self, state: WorkflowState) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for artifact in self.pending_approvals:
            record = state.artifacts.get(artifact)
            if record:
                action = {"type": "approve", "artifact": artifact, "version": record.version, "content_hash": record.content_hash, "why": f"Otras tareas requieren la versión {record.version} de '{artifact}' aprobada."}
                if record.assumptions:
                    action["confirm_assumptions"] = record.assumptions  # HU-003B, regla 4
                actions.append(action)
        for name in self.missing_inputs:
            actions.append({"type": "provide_input", "param": name, "question": INPUT_QUESTIONS.get(name, name)})
        for step in self.steps:
            if step.action == "failed":
                actions.append({"type": "resume", "capability": step.capability, "why": "Reintentar el paso fallido cuando el servicio esté disponible."})
        return actions


def _stale_dependencies(state: WorkflowState, key: str) -> list[str]:
    record = state.artifacts.get(artifact_of(key))
    if not record:
        return []
    # Solo cuentan las dependencias declaradas de esta capacidad: la HU producida por HU-003A guarda la versión
    # de INVEST que usó, pero reevaluar INVEST no debe regenerar la historia.
    capability = CAPABILITIES[key]
    declared = {r.artifact for r in capability.requires} | set(capability.optional)
    stale = [dependency for dependency, version in record.based_on.items() if dependency in declared and dependency in state.artifacts and state.artifacts[dependency].version != version]
    # Una dependencia opcional que apareció después (por ejemplo, scripts para el pipeline) también obliga a regenerar.
    stale += [dependency for dependency in capability.optional if dependency in state.artifacts and dependency not in record.based_on]
    return stale


def _revision_applied(state: WorkflowState) -> bool:
    story = state.artifacts.get("story")
    invest = state.artifacts.get("invest")
    return bool(story and invest and story.produced_by == "story_revision" and story.based_on.get("invest") == invest.version)


def actionable_suggestions(criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sugerencias que el PO debe decidir: solo las de criterios parcial o no_cumple (HU-002, regla 2)."""
    return [c for c in criteria if c.get("status") in {"parcial", "no_cumple"} and str(c.get("suggestion") or "").strip()]


def approved_suggestions(state: WorkflowState) -> list[dict[str, Any]]:
    invest = state.artifacts.get("invest")
    if not invest or not invest.approved:
        return []
    decisions = invest.approval.details.get("suggestions", {})
    return [criterion for criterion in actionable_suggestions(invest.payload.get("criteria", [])) if decisions.get(criterion.get("name")) == "approved"]


def plan(state: WorkflowState, goals: list[str], *, exhausted: set[str] | None = None) -> WorkflowPlan:
    exhausted = exhausted or set()
    decided: dict[str, PlanStep] = {}

    def satisfied(key: str) -> bool:
        if key == "story_revision":
            return _revision_applied(state)
        return artifact_of(key) in state.artifacts and not _stale_dependencies(state, key)

    def decide(key: str) -> PlanStep:
        if key in decided:
            return decided[key]
        capability = CAPABILITIES[key]
        step = PlanStep(key, capability.hu, "run", "")
        decided[key] = step
        if not capability.available:
            step.action, step.reason = "unavailable", capability.unavailable_reason
            return step
        if satisfied(key):
            record = state.artifacts[artifact_of(key)]
            step.action = "reuse"
            step.reason = f"'{artifact_of(key)}' v{record.version} ya existe y está al día con sus dependencias."
            return step
        blockers: list[str] = []
        for requirement in capability.requires:
            dependency = decide(requirement.artifact)
            record = state.artifacts.get(requirement.artifact)
            if dependency.action != "reuse":
                blockers.append(requirement.artifact)
            elif requirement.approved and not (record and record.approved):
                blockers.append(f"approval:{requirement.artifact}")
        if blockers:
            step.action, step.blocked_by = "blocked", blockers
            step.reason = "Espera a: " + ", ".join(b.replace("approval:", "aprobación humana de ") for b in blockers) + "."
            return step
        if key == "story_revision" and not approved_suggestions(state):
            step.action, step.reason = "skipped", "No hay sugerencias INVEST aprobadas; la HU se conserva sin cambios (HU-003A)."
            return step
        needs = [name for name in capability.inputs if name not in state.params and not (name == "requirement" and "story" in state.artifacts)]
        if needs:
            step.action, step.missing_inputs = "needs_input", needs
            step.reason = "Faltan datos del usuario: " + ", ".join(needs) + "."
            return step
        if key in exhausted:
            failure = state.failures.get(key)
            step.action = "failed"
            step.reason = f"Falló tras {failure.attempts if failure else '?'} intentos ({failure.error_code if failure else 'error'}); se puede reanudar."
            return step
        stale = _stale_dependencies(state, key)
        if stale:
            record = state.artifacts[artifact_of(key)]
            changes = ", ".join(f"{d} v{record.based_on[d]}→v{state.artifacts[d].version}" if d in record.based_on else f"ahora existe {d} v{state.artifacts[d].version}" for d in stale)
            step.action, step.reason = "rerun", f"'{artifact_of(key)}' v{record.version} quedó desactualizado ({changes}); se regenera."
        else:
            step.reason = f"No existe '{artifact_of(key)}'; se genera ({capability.hu})."
            optional = [o for o in capability.optional if o in state.artifacts]
            if optional:
                step.reason += " Usará también: " + ", ".join(optional) + "."
        return step

    for goal in goals:
        decide(goal)
    steps = [decided[key] for key in TOPOLOGICAL_ORDER if key in decided]
    return WorkflowPlan(goals=list(goals), steps=steps)
