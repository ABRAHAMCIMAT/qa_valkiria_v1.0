"""Mapa del flujo completo de una historia, derivado del estado del motor (una sola fuente de verdad).

Orden racional (épica v3.0): HU → INVEST → nueva versión con sugerencias → aprobación de la HU → matriz → aprobación de la
matriz → riesgo → scripts → pipeline → performance → Work Item. Cada paso declara su estado y, si se puede ejecutar,
la acción que lo hace avanzar.
"""

from __future__ import annotations

from typing import Any

from valkiria.workflow.graph import CAPABILITIES
from valkiria.workflow.planner import (
    INPUT_QUESTIONS,
    actionable_suggestions,
    approved_suggestions,
)
from valkiria.workflow.state import WorkflowState

# (clave, HU, etiqueta, artefacto que produce, requisitos: artefactos y si deben estar aprobados)
STEPS: list[tuple[str, str, str, str | None, tuple[tuple[str, bool], ...]]] = [
    ("story", "HU-003B", "Historia de usuario", "story", ()),
    ("invest", "HU-002", "Evaluación INVEST", "invest", (("story", False),)),
    ("story_revision", "HU-003A", "Nueva versión con sugerencias", None, (("invest", True),)),
    ("approve_story", "RT-02", "Aprobación de la HU", None, (("story", False),)),
    ("matrix", "HU-004", "Matriz de pruebas", "matrix", (("story", False),)),
    ("approve_matrix", "RT-02", "Aprobación de la matriz", None, (("matrix", False),)),
    ("risk", "HU-005", "Análisis de riesgo", "risk", (("story", True),)),
    ("automation", "HU-009", "Scripts de automatización", "automation", (("matrix", False),)),
    ("pipeline", "HU-007", "Pipeline de Azure DevOps", "pipeline", ()),
    ("performance_design", "HU-008A", "Diseño de prueba de performance", "performance_design", (("story", False),)),
    ("azure_work_item", "HU-006", "Work Item de Azure DevOps (vista previa)", "azure_work_item", (("story", True),)),
]
LABELS = {key: label for key, _, label, _, _ in STEPS}
INPUTS = {"automation": ("repository",), "performance_design": ("performance_users", "performance_duration_seconds", "performance_sla_ms"),
          "azure_work_item": ("azure_project",)}
RUN_LABELS = {"invest": "Evaluar INVEST", "matrix": "Generar la matriz de pruebas", "risk": "Evaluar el riesgo", "automation": "Generar los scripts",
              "pipeline": "Generar el pipeline", "performance_design": "Diseñar la prueba de performance", "azure_work_item": "Preparar el Work Item"}


def _stale(state: WorkflowState, artifact: str) -> bool:
    """Desactualizado solo por sus dependencias declaradas (la HU nunca depende de su propia evaluación INVEST)."""
    record = state.artifacts.get(artifact)
    if not record or artifact not in CAPABILITIES:
        return False
    declared = {r.artifact for r in CAPABILITIES[artifact].requires} | set(CAPABILITIES[artifact].optional)
    return any(dep in declared and state.artifacts.get(dep) and state.artifacts[dep].version != version for dep, version in record.based_on.items())


def _ready(state: WorkflowState, requires: tuple[tuple[str, bool], ...]) -> tuple[bool, str | None]:
    for artifact, approved in requires:
        record = state.artifacts.get(artifact)
        if not record:
            return False, f"Requiere {LABELS.get(artifact, artifact).lower()}."
        if approved and not record.approved:
            return False, f"Requiere {LABELS.get(artifact, artifact).lower()} aprobada."
    return True, None


def _step(state: WorkflowState, key: str, hu: str, label: str, artifact: str | None, requires) -> dict[str, Any]:
    ready, why = _ready(state, requires)
    step: dict[str, Any] = {"key": key, "hu": hu, "label": label, "state": "pending", "ready": ready, "why": why}
    if key == "story_revision":
        invest = state.artifacts.get("invest")
        stories = [r for r in state.history if r.key == "story"] + ([state.artifacts["story"]] if "story" in state.artifacts else [])
        applied = any(r.produced_by == "story_revision" for r in stories)
        pending = bool(invest and not invest.approved and actionable_suggestions(invest.payload.get("criteria", [])))
        if pending:
            step |= {"state": "action", "why": "Decide qué sugerencias INVEST aplicar."}
        elif applied:
            step["state"] = "done"
        elif invest and (invest.approved and not approved_suggestions(state) or not actionable_suggestions(invest.payload.get("criteria", []))):
            step |= {"state": "skipped", "why": "No hay sugerencias INVEST por aplicar."}
        return step
    if key in {"approve_story", "approve_matrix"}:
        record = state.artifacts.get("story" if key == "approve_story" else "matrix")
        if record:
            step |= {"state": "done" if record.approved else "action", "version": record.version, "content_hash": record.content_hash,
                     "assumptions": record.assumptions if key == "approve_story" and not record.approved else []}
        return step
    record = state.artifacts.get(artifact) if artifact else None
    if key in state.failures:
        step |= {"state": "failed", "why": state.failures[key].message}
    elif record:
        step |= {"state": "stale" if _stale(state, artifact) else "done", "version": record.version, "approved": record.approved}
    missing = [name for name in INPUTS.get(key, ()) if name not in state.params]
    if missing and step["state"] == "pending":
        step["missing_inputs"] = missing
    return step


def flow_view(state: WorkflowState | None) -> dict[str, Any]:
    if state is None:
        return {"workflow_id": None, "steps": [_empty(key, hu, label) for key, hu, label, _, _ in STEPS], "next": _first_step(), "story": None}
    steps = [_step(state, *spec) for spec in STEPS]
    story = state.artifacts.get("story")
    history = [r for r in state.history if r.key == "story"] + ([story] if story else [])
    return {
        "workflow_id": state.id,
        "story": {"title": story.payload.get("title"), "version": story.version, "approved": story.approved, "assumptions": story.assumptions,
                  "versions": [{"version": r.version, "produced_by": r.produced_by, "title": r.payload.get("title"), "at": r.created_at.isoformat()} for r in history]}
        if story else None,
        "steps": steps,
        "next": next_actions(state, steps),
    }


def _empty(key: str, hu: str, label: str) -> dict[str, Any]:
    return {"key": key, "hu": hu, "label": label, "state": "pending", "ready": key == "story", "why": None}


def _first_step() -> list[dict[str, Any]]:
    return [{"type": "say", "label": "Describe el requerimiento", "hint": "Cuéntame qué necesita el usuario y lo convierto en una historia de usuario."}]


def next_actions(state: WorkflowState, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Siguiente paso recomendado primero; después, las alternativas que ya se pueden ejecutar."""
    by_key = {s["key"]: s for s in steps}
    actions: list[dict[str, Any]] = []

    def add(action: dict[str, Any]) -> None:
        if action not in actions:
            actions.append(action)

    invest = state.artifacts.get("invest")
    if by_key["invest"]["state"] in {"pending", "stale"}:
        add({"type": "run", "goal": "invest", "label": RUN_LABELS["invest"]})
    if by_key["story_revision"]["state"] == "action" and invest:
        add({"type": "decide_suggestions", "label": "Decidir sugerencias INVEST", "version": invest.version, "content_hash": invest.content_hash,
             "suggestions": [{"name": c["name"], "status": c["status"], "suggestion": c["suggestion"]} for c in actionable_suggestions(invest.payload.get("criteria", []))]})
    if by_key["approve_story"]["state"] == "action":
        step = by_key["approve_story"]
        add({"type": "approve", "artifact": "story", "label": f"Aprobar la HU v{step['version']}", "version": step["version"], "content_hash": step["content_hash"],
             "confirm_assumptions": step["assumptions"]})
    for key in ("matrix", "risk", "automation", "pipeline", "performance_design", "azure_work_item"):
        step = by_key[key]
        if step["state"] in {"pending", "stale", "failed"} and step["ready"]:
            if step.get("missing_inputs"):
                add({"type": "input", "goal": key, "label": RUN_LABELS[key], "params": [{"name": n, "question": INPUT_QUESTIONS[n]} for n in step["missing_inputs"]]})
            else:
                add({"type": "run", "goal": key, "label": ("Regenerar: " if step["state"] == "stale" else "") + RUN_LABELS[key]})
        if key == "matrix" and by_key["approve_matrix"]["state"] == "action" and step["state"] == "done":
            add({"type": "approve", "artifact": "matrix", "label": f"Aprobar la matriz v{by_key['approve_matrix']['version']}",
                 "version": by_key["approve_matrix"]["version"], "content_hash": by_key["approve_matrix"]["content_hash"], "confirm_assumptions": []})
    work_item = state.artifacts.get("azure_work_item")
    if work_item and not work_item.approved:
        add({"type": "approve", "artifact": "azure_work_item", "label": "Aprobar la publicación del Work Item", "version": work_item.version,
             "content_hash": work_item.content_hash, "confirm_assumptions": []})
    return actions[:4]


def resume_line(view: dict[str, Any]) -> str | None:
    """Recordatorio breve de dónde va el flujo, para retomarlo después de atender otra petición."""
    story = view.get("story")
    if not story:
        return None
    first = next(iter(view["next"]), None)
    where = f"Seguimos con la HU «{story['title']}» v{story['version']}"
    label = first["label"][0].lower() + first["label"][1:] if first else ""
    return f"{where}. Siguiente paso sugerido: {label}." if first else f"{where}. El flujo está completo."
