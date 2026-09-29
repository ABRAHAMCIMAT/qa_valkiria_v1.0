"""Ejecutores de cada capacidad del flujo.

Reutilizan los casos de uso existentes y agregan un ciclo de calidad para las salidas del LLM:
generar → validar contra las reglas de la HU → pedir una corrección concreta (una vez) → completar de forma
determinista y declararlo como advertencia. Nunca se presenta como generado por el LLM lo que no lo fue.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from valkiria.application.automation_execution import (
    build_evidence_report,
    create_automation_batch,
)
from valkiria.application.automation_runner import execute_api_cases
from valkiria.application.prompts import INVEST_SYSTEM, REVISION_SYSTEM
from valkiria.application.qa_artifacts import (
    MAX_AUTOMATION_BATCH,
    PolicyViolation,
    generate_pipeline_yaml,
    performance_plan,
)
from valkiria.application.script_generation import FRAMEWORKS as FRAMEWORK_NAMES
from valkiria.application.use_cases import (
    ValkiriaService,
    _normalize_invest,
    _normalize_story,
    _story_changes,
)
from valkiria.domain.models import InvestEvaluation, TestMatrix, UserStory
from valkiria.memory.service import with_memory
from valkiria.workflow.planner import actionable_suggestions, approved_suggestions
from valkiria.workflow.state import WorkflowState
from valkiria.workflow.validators import (
    MAX_CRITERIA_PER_MATRIX,
    missing_case_slots,
    validate_invest,
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
    by_id = {c.id: c for c in story.acceptance_criteria}
    missing = sorted({criterion_id for criterion_id, _ in missing_case_slots(cases, ids)})
    if missing:
        # Solo se regeneran los criterios incompletos (formato compacto, en paralelo).
        cases = [c for c in cases if c.get("criterion_id") not in missing]
        for group in await asyncio.gather(*(service.matrix_cases(story, by_id[cid], memory=memory) for cid in missing)):
            cases += group
        warnings.append(f"Se regeneraron {len(missing)} criterio(s) incompletos para cumplir HU-004.")
    cases = _fit_matrix([c for c in cases if c.get("criterion_id") in ids], ids)
    templated = sorted({c["criterion_id"] for c in cases if c.get("template")})
    if templated:
        warnings.append(f"Casos completados con plantilla determinista para {', '.join(templated)} porque el modelo no respondió; revísalos.")
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


API_ONLY = {"restassured", "postman-newman"}


async def run_automation(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    matrix = state.artifacts["matrix"]
    cases = matrix.payload["cases"]
    params = state.params
    framework = str(params.get("framework", "playwright")).lower().replace(" ", "")
    framework = {"postman": "postman-newman", "newman": "postman-newman", "rest-assured": "restassured"}.get(framework, framework)
    platform = str(params.get("platform") or ("api" if framework in API_ONLY else "web")).lower()
    feature = str(state.artifacts["story"].payload.get("title", "Aplicacion"))
    batches = []
    for start in range(0, len(cases), MAX_AUTOMATION_BATCH):
        try:
            batches.append(create_automation_batch(cases=cases[start:start + MAX_AUTOMATION_BATCH], framework=framework, platform=platform, repository=str(params["repository"]),
                                                   base_branch=str(params.get("base_branch", "main")), matrix_status="approved" if matrix.approved else "draft", feature=feature))
        except PolicyViolation as exc:
            raise StepError("automation_policy_violation", str(exc), retryable=False) from exc
    warnings = []
    if len(batches) > 1:
        warnings.append(f"{len(cases)} casos divididos en {len(batches)} lotes de máximo {MAX_AUTOMATION_BATCH} (HU-009, regla 1).")
    if not matrix.approved:
        warnings.append("Scripts generados desde una matriz en borrador; se marcarán como desactualizados si la matriz cambia.")
    findings = [f for b in batches for f in b.get("quality", {}).get("findings", [])]
    if findings:
        warnings.append("El pull request no se abre hasta resolver: " + "; ".join(findings[:5]) + " (HU-009, regla 6).")
    total = sum(len(b["case_ids"]) for b in batches)
    return StepOutput({"batches": batches, "framework": framework, "platform": platform}, _versions(state, "matrix"), warnings,
                      f"{total} scripts {FRAMEWORK_NAMES.get(framework, framework)} ({platform}) en {len(batches)} lote(s), entrega solo por pull request.")


async def run_execution(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    """HU-010: ejecución controlada de los scripts aprobados contra la app sintética, con evidencia por caso."""
    automation = state.artifacts["automation"]
    platform, framework = automation.payload.get("platform", "web"), automation.payload.get("framework", "playwright")
    cases = [case for batch in automation.payload["batches"] for case in batch.get("cases", [])]
    if platform == "api":
        results = await execute_api_cases(cases, base_url=service.synthetic_app_base_url, transport=service.synthetic_transport)
    elif service.web_runner is not None:
        results = await service.web_runner.run(base_url=service.synthetic_app_base_url, cases=cases)
    else:
        raise StepError("web_execution_unavailable", "La app sintética de Nissan es una API sin interfaz web, así que los scripts web no tienen contra qué ejecutarse en "
                        "este entorno. Regenera los scripts con un stack de API (Playwright, RestAssured o Postman-Newman) para ejecutarlos aquí, o ejecuta los "
                        "scripts web en tu pipeline contra la interfaz real.", retryable=False)
    passed = sum(r.get("result", r.get("status")) == "pass" for r in results)
    execution_id = f"HU-010-{automation.version}-{state.id[:8]}"
    report = build_evidence_report(execution_id=execution_id, title="Valkiria · Evidencia de ejecución de scripts (HU-010)", output_format="pdf",
                                   fields={"historia": state.artifacts["story"].payload.get("title"), "stack": f"{framework} ({platform})",
                                           "casos": len(results), "aprobados": passed, "fallidos": len(results) - passed, "trace_id": state.trace_id},
                                   logs=[f"{r['case_id']} {r.get('request', '')} -> {r.get('status')} esperado {r.get('expected_status', '')} ({r.get('result', '')})" for r in results])
    warnings = ["Ejecución en el entorno sintético, nunca en producción."]
    if passed < len(results):
        warnings.append(f"{len(results) - passed} caso(s) fallidos: revisa la evidencia antes de integrar el pull request.")
    return StepOutput({"platform": platform, "framework": framework, "results": results, "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed},
                       "report": report}, _versions(state, "automation"), warnings, f"{passed} de {len(results)} casos aprobados en la ejecución sintética.")


RUN_COMMANDS = {"playwright": "npx playwright test", "selenium": "pytest tests", "restassured": "mvn -B test",
                "postman-newman": "npx newman run {file} --env-var baseUrl=$(BASE_URL) --reporters cli,junit"}


async def run_pipeline(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    scripts: list[str] = []
    automation = state.artifacts.get("automation")
    if automation:
        framework = automation.payload.get("framework", "playwright")
        for batch in automation.payload["batches"]:
            collections = [name for name in batch.get("scripts", {}) if name.endswith(".postman_collection.json")]
            command = RUN_COMMANDS.get(framework, "echo 'Ejecutar los scripts del lote'")
            for item in (collections or [None]):
                line = command.format(file=item) if item else command
                if line not in scripts:
                    scripts.append(line)
    text = generate_pipeline_yaml(scripts=scripts or None)
    warnings = [] if scripts else ["Sin scripts todavía: la etapa de test queda con advertencia y no reporta pruebas aprobadas (HU-007)."]
    return StepOutput({"yaml": text, "scripts": scripts, "delivery": "pull_request_only"}, _versions(state, "automation"), warnings, f"Pipeline YAML con {len(scripts)} comando(s) de prueba.")


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
    "execution": run_execution,
    "pipeline": run_pipeline,
    "performance_design": run_performance_design,
    "azure_work_item": run_azure_work_item,
}
