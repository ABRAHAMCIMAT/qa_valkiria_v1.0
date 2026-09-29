"""Ejecutores de cada capacidad del flujo.

Reutilizan los casos de uso existentes y agregan un ciclo de calidad para las salidas del LLM:
generar → validar contra las reglas de la HU → pedir una corrección concreta (una vez) → completar de forma
determinista y declararlo como advertencia. Nunca se presenta como generado por el LLM lo que no lo fue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from valkiria.application.automation_execution import create_automation_batch
from valkiria.application.prompts import INVEST_SYSTEM, MATRIX_SYSTEM, REVISION_SYSTEM
from valkiria.application.qa_artifacts import (
    MAX_AUTOMATION_BATCH,
    PolicyViolation,
    generate_pipeline_yaml,
    performance_plan,
)
from valkiria.application.use_cases import (
    ValkiriaService,
    _normalize_invest,
    _normalize_matrix,
    _normalize_story,
    _story_changes,
    matrix_user_prompt,
)
from valkiria.domain.models import InvestEvaluation, TestMatrix, UserStory
from valkiria.memory.service import with_memory
from valkiria.workflow.planner import actionable_suggestions, approved_suggestions
from valkiria.workflow.state import WorkflowState
from valkiria.workflow.validators import (
    MAX_CRITERIA_PER_MATRIX,
    missing_case_slots,
    validate_invest,
    validate_matrix,
)

PERFORMANCE_TOOLS = {"jmeter", "locust"}  # Soportadas por Azure Load Testing (HU-008A/B).


class StepError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class StepOutput:
    payload: dict[str, Any]
    based_on: dict[str, int]
    warnings: list[str] = field(default_factory=list)
    summary: str = ""
    assumptions: list[str] = field(default_factory=list)


def _story(state: WorkflowState) -> UserStory:
    return UserStory.model_validate(state.artifacts["story"].payload)


def _versions(state: WorkflowState, *keys: str) -> dict[str, int]:
    return {key: state.artifacts[key].version for key in keys if key in state.artifacts}


async def _generate(service: ValkiriaService, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    return await service.llm.generate_json(system=system, user=user, schema=schema)


async def run_story(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    story, extras = await service.draft_story(str(state.params["requirement"]), state.actor, memory=memory)
    warnings = ["Borrador generado por IA (HU-003B)."]
    if extras["assumptions"]:
        warnings.append("Supuestos que el PO debe confirmar antes de aprobar (HU-003B, regla 4): " + "; ".join(extras["assumptions"]) + ".")
    if extras["split"]:
        warnings.append("Requerimiento amplio: se sugiere dividirlo (HU-003B, regla 2). Esta HU cubre el flujo principal; otras HU propuestas: " + "; ".join(extras["split"]) + ".")
    return StepOutput(story.model_dump(mode="json"), {}, warnings, f"HU '{story.title}' creada en borrador.", assumptions=extras["assumptions"])


async def run_invest(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    story = _story(state)
    evaluation = await service.evaluate_invest(story, state.actor, memory=memory)
    criteria = [c.model_dump() for c in evaluation.criteria]
    findings = validate_invest(criteria)
    warnings: list[str] = []
    if findings:
        repair = "Corrige la evaluación. Problemas detectados:\n- " + "\n- ".join(findings) + "\n\nHistoria:\n" + story.model_dump_json()
        data = _normalize_invest(await _generate(service, INVEST_SYSTEM, repair, InvestEvaluation.model_json_schema()))
        second = validate_invest(data["criteria"])
        if second:
            raise StepError("invest_invalid", "El modelo no produjo una evaluación INVEST completa: " + " ".join(second), retryable=True)
        criteria = data["criteria"]
        warnings.append("La evaluación se corrigió una vez para cumplir HU-002.")
    payload = {"story_version": state.artifacts["story"].version, "model": evaluation.model, "prompt_version": evaluation.prompt_version, "criteria": criteria}
    pending = [c["name"] for c in actionable_suggestions(criteria)]
    if pending:
        warnings.append("Sugerencias que el PO debe aprobar o rechazar: " + ", ".join(pending) + ".")
    return StepOutput(payload, _versions(state, "story"), warnings, f"INVEST evaluado: {sum(c['status'] == 'cumple' for c in criteria)}/6 cumplen.")


async def run_story_revision(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    story = _story(state)
    suggestions = approved_suggestions(state)
    user = "Historia actual:\n" + story.model_dump_json(include={"title", "description", "business_rules", "acceptance_criteria"}) + "\n\nSugerencias aprobadas:\n" + json.dumps(
        [{"criterio": s["name"], "sugerencia": s["suggestion"]} for s in suggestions], ensure_ascii=False)
    fields = _normalize_story(await _generate(service, REVISION_SYSTEM, with_memory(user, memory), UserStory.model_json_schema()))
    try:
        revised = UserStory.model_validate({**fields, "id": story.id, "version": story.version + 1})
    except ValidationError as exc:
        raise StepError("story_revision_invalid", "El modelo no produjo una HU completa con los 4 componentes.", retryable=True) from exc
    changes = _story_changes(story, revised)
    changed = [k.removesuffix("_changed") for k, v in changes.items() if k.endswith("_changed") and v]
    return StepOutput(revised.model_dump(mode="json"), _versions(state, "invest"),
                      ["Nueva versión en borrador generada por IA (HU-003A).", "Cambios: " + (", ".join(changed) or "ninguno detectado") + "."],
                      f"HU v{revised.version} redactada con {len(suggestions)} sugerencia(s) aprobada(s).")


def _template_case(case_id: str, criterion: dict[str, Any], case_type: str) -> dict[str, Any]:
    label = {"positive": "comportamiento válido", "negative": "comportamiento inválido", "edge": "valores límite"}[case_type]
    return {"id": case_id, "criterion_id": criterion["id"], "scenario": f"{criterion['text']} ({label})", "preconditions": [], "steps": [], "data": {},
            "expected_result": criterion["text"] if case_type == "positive" else "El sistema rechaza o maneja el caso sin error", "priority": "medium", "type": case_type}


def _fit_matrix(cases: list[dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    """Máximo 30 casos, conservando primero un caso de cada tipo por criterio (cobertura mínima)."""
    required, rest = [], []
    covered: set[tuple[str, str]] = set()
    for case in cases:
        slot = (case.get("criterion_id"), case.get("type"))
        if slot not in covered and slot[0] in ids:
            covered.add(slot)
            required.append(case)
        else:
            rest.append(case)
    return (required + rest)[:30]


async def run_matrix(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    story = _story(state)
    criteria = [c.model_dump() for c in story.acceptance_criteria]
    if len(criteria) > MAX_CRITERIA_PER_MATRIX:
        raise StepError("matrix_requires_split", f"La HU tiene {len(criteria)} criterios; el máximo por generación es {MAX_CRITERIA_PER_MATRIX}. Divide la HU o genera por lotes (HU-004).", retryable=False)
    ids = [c["id"] for c in criteria]
    matrix = await service.generate_matrix(story, state.actor, memory=memory)
    cases = [c.model_dump(mode="json") for c in matrix.cases]
    warnings: list[str] = []
    findings = validate_matrix(cases, ids)
    if findings:
        repair = "Corrige la matriz. Problemas detectados:\n- " + "\n- ".join(findings[:15]) + "\n\nHistoria:\n" + matrix_user_prompt(story)
        repaired = _normalize_matrix(await _generate(service, MATRIX_SYSTEM, repair, TestMatrix.model_json_schema()))["cases"]
        if len(validate_matrix(repaired, ids)) < len(findings):
            cases = repaired
        warnings.append("La matriz se corrigió una vez para cumplir HU-004.")
    cases = _fit_matrix([c for c in cases if c.get("criterion_id") in ids], ids)
    slots = missing_case_slots(cases, ids)
    if slots:
        by_id = {c["id"]: c for c in criteria}
        for n, (criterion_id, case_type) in enumerate(slots, start=1):
            cases.append(_template_case(f"TC-AUTO-{n:02d}", by_id[criterion_id], case_type))
        cases = _fit_matrix(cases, ids)
        warnings.append(f"Se completaron {len(slots)} caso(s) con plantilla determinista para cumplir el mínimo positivo/negativo/borde por criterio; revísalos.")
    TestMatrix.model_validate({"story_id": str(story.id), "cases": cases})
    if not state.artifacts["story"].approved:
        warnings.append("Matriz generada desde una HU en borrador (HU-004, regla 4).")
    return StepOutput({"story_id": str(story.id), "story_version": state.artifacts["story"].version, "cases": cases}, _versions(state, "story"), warnings,
                      f"Matriz con {len(cases)} casos para {len(ids)} criterio(s).")


async def run_risk(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    assessment = await service.assess_risk(_story(state), state.actor, memory=memory)
    payload = assessment.model_dump(mode="json")
    warnings = ["Histórico de defectos no considerado (HU-005, regla 3)."]
    if assessment.level.value == "high":
        warnings.append("Riesgo alto: se sugiere (sin forzar) diseñar una prueba de performance (HU-008A) y notificar a PO y DevOps.")
    return StepOutput(payload, _versions(state, "story"), warnings, f"Riesgo {assessment.level.value}.")


async def run_automation(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    matrix = state.artifacts["matrix"]
    cases = matrix.payload["cases"]
    params = state.params
    batches = []
    for start in range(0, len(cases), MAX_AUTOMATION_BATCH):
        try:
            batches.append(create_automation_batch(cases=cases[start:start + MAX_AUTOMATION_BATCH], framework=str(params.get("framework", "playwright")), platform=str(params.get("platform", "web")),
                                                   repository=str(params["repository"]), base_branch=str(params.get("base_branch", "main")), matrix_status="approved" if matrix.approved else "draft"))
        except PolicyViolation as exc:
            raise StepError("automation_policy_violation", str(exc), retryable=False) from exc
    warnings = []
    if len(batches) > 1:
        warnings.append(f"{len(cases)} casos divididos en {len(batches)} lotes de máximo {MAX_AUTOMATION_BATCH} (HU-009, regla 1).")
    if not matrix.approved:
        warnings.append("Scripts generados desde una matriz en borrador; se marcarán como desactualizados si la matriz cambia.")
    return StepOutput({"batches": batches}, _versions(state, "matrix"), warnings, f"{sum(len(b['case_ids']) for b in batches)} scripts en {len(batches)} lote(s), entrega solo por pull request.")


async def run_pipeline(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    scripts: list[str] = []
    automation = state.artifacts.get("automation")
    if automation:
        for batch in automation.payload["batches"]:
            for filename in batch.get("scripts", {}):
                scripts.append(f"npx playwright test tests/automation/{filename}" if filename.endswith(".spec.ts") else f"echo 'Ejecutar tests/automation/{filename}'")
    text = generate_pipeline_yaml(scripts=scripts or None)
    warnings = [] if scripts else ["Sin scripts todavía: la etapa de test queda con advertencia y no reporta pruebas aprobadas (HU-007)."]
    return StepOutput({"yaml": text, "scripts": scripts, "delivery": "pull_request_only"}, _versions(state, "automation"), warnings, f"Pipeline YAML con {len(scripts)} script(s).")


async def run_performance_design(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    params = state.params
    tool = str(params.get("performance_tool", "jmeter")).lower()
    if tool not in PERFORMANCE_TOOLS:
        raise StepError("unsupported_performance_tool", f"Herramienta '{tool}' no soportada por Azure Load Testing; usa jmeter o locust.", retryable=False)
    try:
        plan = performance_plan(str(_story(state).id), tool=tool, scenario_type=str(params.get("performance_type", "load")), users=int(params["performance_users"]),
                                duration_seconds=int(params["performance_duration_seconds"]), sla_ms=int(params["performance_sla_ms"]))
    except (PolicyViolation, ValueError) as exc:
        raise StepError("performance_invalid_parameters", str(exc), retryable=False) from exc
    return StepOutput(plan | {"execution_target": "azure-load-testing", "executed": False}, _versions(state, "story"), ["Diseño listo; la ejecución corresponde a HU-008B."], f"Escenario {plan['scenario_type']} con {plan['users']} usuarios y SLA p95 {plan['target_sla_ms']} ms.")


async def run_azure_work_item(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    story = _story(state)
    criteria = "".join(f"<li><b>{c.id}</b> {c.text}</li>" for c in story.acceptance_criteria)
    rules = "".join(f"<li>{rule}</li>" for rule in story.business_rules)
    fields = {"System.Title": story.title, "System.Description": story.description, "Microsoft.VSTS.Common.AcceptanceCriteria": f"<ul>{criteria}</ul>",
              "Custom.BusinessRules": f"<ul>{rules}</ul>", "System.Tags": "valkiria"}
    payload = {"operation": "create", "project": state.params["azure_project"], "story_id": str(story.id), "story_version": state.artifacts["story"].version, "fields": fields,
               "status": "preview", "idempotency_key": f"{state.params['azure_project']}:{story.id}"}
    return StepOutput(payload, _versions(state, "story"), ["Vista previa: se publican exactamente estos campos una vez aprobados (HU-006, regla 4)."],
                      "Vista previa del Work Item lista para aprobación.")


EXECUTORS = {
    "story": run_story,
    "invest": run_invest,
    "story_revision": run_story_revision,
    "matrix": run_matrix,
    "risk": run_risk,
    "automation": run_automation,
    "pipeline": run_pipeline,
    "performance_design": run_performance_design,
    "azure_work_item": run_azure_work_item,
}
