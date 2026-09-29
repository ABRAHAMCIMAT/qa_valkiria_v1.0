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
    static_analyse_database_script,
)
from valkiria.application.automation_runner import (
    execute_api_cases,
    execute_data_queries,
)
from valkiria.application.defects import (
    DECISIONS,
    carry_decisions,
    case_design_issue,
    triage,
)
from valkiria.application.pipeline_files import pipeline_files
from valkiria.application.prompts import (
    INVEST_SYSTEM,
    REVISION_SYSTEM,
    SQL_VALIDATION_SYSTEM,
)
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
    if service.ui_guide:
        # Antes de aprobar: casos de interfaz que no podrán dar su resultado en las pantallas (se ven igual al ejecutarlos).
        flawed = [c["id"] for c in cases if case_design_issue(c)]
        if flawed:
            warnings.append(f"{len(flawed)} caso(s) de interfaz no envían el formulario o van y vienen entre pantallas ({', '.join(flawed[:6])}"
                            f"{'…' if len(flawed) > 6 else ''}): así fallarán al ejecutarse. Corrígelos antes de aprobar la matriz.")
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


async def run_data_validation(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    """HU-011: consultas de solo lectura derivadas de la HU y de los casos negativos y de borde, ligadas a su criterio y caso."""
    story, matrix = _story(state), state.artifacts["matrix"]
    cases = [c for c in matrix.payload["cases"] if c.get("type") in {"negative", "edge"}][:12]
    user = (matrix_user_prompt(story) + "\n\nCasos negativos y de borde de la matriz:\n"
            + "\n".join(f"{c['id']} ({c['criterion_id']}, {c['type']}): {c['scenario']} → {c['expected_result']}" for c in cases))
    data = await _generate(service, SQL_VALIDATION_SYSTEM, with_memory(user, memory), {"type": "object"})
    ids = {c["id"] for c in matrix.payload["cases"]}
    criteria = {c.id for c in story.acceptance_criteria}
    queries = []
    for n, raw in enumerate(q for q in (data.get("queries") or [])[:4] if isinstance(q, dict)):
        sql = str(raw.get("sql") or "").strip().rstrip(";")
        analysis = static_analyse_database_script(sql, "postgresql") if sql else {"passed": False, "statement_type": "empty", "findings": ["consulta vacía"]}
        read_only = sql.lower().startswith("select") and analysis["statement_type"] == "read_only" and analysis["passed"]
        queries.append({"id": f"DV-{n + 1:02d}", "purpose": str(raw.get("purpose") or "Validación de datos"), "sql": sql,
                        "criterion_id": raw.get("criterion_id") if raw.get("criterion_id") in criteria else None,
                        "case_id": raw.get("case_id") if raw.get("case_id") in ids else None,
                        "expect": "rows" if str(raw.get("expect", "")).lower() == "rows" else "empty",
                        "status": "lista" if read_only else "bloqueada", "findings": [] if read_only else (analysis.get("findings") or ["no es de solo lectura"])})
    if service.database_executor is not None:
        # Validación previa: cada consulta de solo lectura se prueba en la base sintética antes de pedir la aprobación humana;
        # si falla (columna inexistente, sintaxis), se pide una corrección con el error concreto.
        for query in (q for q in queries if q["status"] == "lista"):
            error = await _preflight(service, query["sql"], state.trace_id, query)
            if error:
                fixed = await _generate(service, SQL_VALIDATION_SYSTEM, f"Esta consulta falló con {error}. Corrígela usando solo tablas y columnas del esquema; "
                                        f"conserva su propósito ({query['purpose']}):\n{query['sql']}", {"type": "object"})
                candidate = next((str(q.get("sql") or "").strip().rstrip(";") for q in fixed.get("queries") or [] if isinstance(q, dict)), "") or str(fixed.get("sql") or "").strip().rstrip(";")
                analysis = static_analyse_database_script(candidate, "postgresql") if candidate else {"passed": False, "statement_type": "empty"}
                if candidate.lower().startswith("select") and analysis["passed"] and analysis["statement_type"] == "read_only" and not await _preflight(service, candidate, state.trace_id, query):
                    query |= {"sql": candidate, "corrected": True}
                else:
                    query |= {"status": "inválida", "findings": [f"falló en la base sintética: {error}"]}
    if not any(q["status"] == "lista" for q in queries):
        raise StepError("data_validation_invalid", "El modelo no produjo consultas de solo lectura válidas.", retryable=True)
    warnings = ["Consultas de solo lectura sobre la base sintética; se ejecutan en HU-010 después de tu verificación."]
    blocked = [q["id"] for q in queries if q["status"] == "bloqueada"]
    if blocked:
        warnings.append(f"Bloqueadas por el análisis estático (no se ejecutarán): {', '.join(blocked)}.")
    # Lo que la consulta devuelve HOY en la base sintética: ayuda al humano a detectar un resultado esperado mal planteado antes de aprobar.
    mismatched = [q["id"] for q in queries if "preview_rows" in q and ((q["expect"] == "empty") == bool(q["preview_rows"]))]
    for query in queries:
        if "preview_rows" in query:
            query["preview_matches"] = query["id"] not in mismatched
    if mismatched:
        warnings.append(f"Hoy fallarían en la base sintética: {', '.join(mismatched)}. Revisa si es un defecto de datos o si la consulta o su resultado esperado no reflejan la regla.")
    invalid = [q["id"] for q in queries if q["status"] == "inválida"]
    if invalid:
        warnings.append(f"Inválidas en la base sintética aun después de una corrección (no se ejecutarán): {', '.join(invalid)}.")
    untraced = [q["id"] for q in queries if not q["case_id"]]
    if untraced:
        warnings.append(f"Sin caso de la matriz asociado: {', '.join(untraced)}; asígnalo al revisar.")
    return StepOutput({"queries": queries, "story_version": state.artifacts["story"].version, "matrix_version": matrix.version},
                      _versions(state, "story", "matrix"), warnings, f"{sum(q['status'] == 'lista' for q in queries)} consulta(s) de datos listas para verificar.")


async def _preflight(service: ValkiriaService, sql: str, trace_id: str, preview: dict | None = None) -> str | None:
    """Prueba una consulta de solo lectura en la base sintética; devuelve el tipo de error o None si corre (y anota cuántas filas devuelve hoy)."""
    outcome, _ = await asyncio.to_thread(service.database_executor.execute, script=sql, case_id="HU-011-preflight", trace_id=trace_id, output_format="pdf")
    if outcome.get("status") == "completed" and preview is not None:
        preview["preview_rows"] = len(outcome.get("rows") or [])
    return None if outcome.get("status") == "completed" else str(outcome.get("error_type") or outcome.get("status"))


async def run_execution(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    """HU-010: ejecución unificada de lo que el humano verificó — scripts de API y consultas de datos (HU-011) — con evidencia consolidada."""
    automation, data = state.artifacts.get("automation"), state.artifacts.get("data_validation")
    approved_scripts = automation if automation and automation.approved else None
    approved_data = data if data and data.approved else None
    if not approved_scripts and not approved_data:
        raise StepError("execution_requires_verification", "Para ejecutar necesito que verifiques y apruebes los scripts o las consultas de datos (RT-02).", retryable=False)
    results: list[dict[str, Any]] = []
    notes: list[str] = []
    platform = framework = None
    if approved_scripts:
        platform, framework = approved_scripts.payload.get("platform", "web"), approved_scripts.payload.get("framework", "playwright")
        cases = [case for batch in approved_scripts.payload["batches"] for case in batch.get("cases", [])]
        if platform == "api":
            results += await execute_api_cases(cases, base_url=service.synthetic_app_base_url, transport=service.synthetic_transport)
        else:
            web = None
            if service.web_runner is not None:
                try:
                    # Chromium ejecuta los pasos con la misma traducción que el código entregado (web_steps) contra las pantallas sintéticas.
                    web = [{**r, "kind": "web"} for r in await service.web_runner.run(base_url=service.synthetic_app_base_url, cases=cases)]
                except RuntimeError:
                    web = None
            if web is not None:
                results += web
            elif not approved_data:
                raise StepError("web_execution_unavailable", "Este entorno no tiene el navegador habilitado para ejecutar scripts web (VALKIRIA_AUTOMATION_EXECUTE con la "
                                "imagen que incluye Chromium). Puedes regenerar los scripts con un stack de API (Playwright, RestAssured o Postman-Newman) para "
                                "ejecutarlos aquí, o ejecutarlos en tu pipeline.", retryable=False)
            else:
                notes.append("Los scripts web no se ejecutaron: el navegador no está habilitado en este entorno.")
    if approved_data:
        if service.database_executor is None:
            notes.append("Las consultas de datos no se ejecutaron: la base sintética no está configurada en este entorno.")
        else:
            results += await execute_data_queries(approved_data.payload["queries"], executor=service.database_executor, trace_id=state.trace_id)
    based_on = {k: v for k, v in {"automation": approved_scripts.version if approved_scripts else None, "data_validation": approved_data.version if approved_data else None}.items() if v}
    return execution_output(state, results, platform=platform, framework=framework, based_on=based_on, notes=notes, source="valkiria")


def execution_output(state: WorkflowState, results: list[dict[str, Any]], *, platform: str | None, framework: str | None, based_on: dict[str, int],
                     notes: list[str], source: str, run_id: str | None = None) -> StepOutput:
    """Resultado de HU-010 con evidencia consolidada; lo usan la ejecución local y los resultados que envía el pipeline de Azure DevOps."""
    passed = sum(r.get("result", r.get("status")) == "pass" for r in results)
    by_kind = {kind: sum(r.get("kind") == kind for r in results) for kind in ("api", "database", "web")}
    version = state.artifacts["execution"].version + 1 if "execution" in state.artifacts else 1
    execution_id = f"HU-010-{state.id[:8]}-v{version}" + (f"-run{run_id}" if run_id else "")
    origin = f"pipeline de Azure DevOps (corrida {run_id})" if source == "azure-devops" else "entorno sintético (Valkiria)"
    report = build_evidence_report(execution_id=execution_id, title="Valkiria · Evidencia de ejecución (HU-010 / HU-011)", output_format="pdf",
                                   fields={"historia": state.artifacts["story"].payload.get("title"), "origen": origin,
                                           "stack": f"{framework} ({platform})" if framework else "solo datos",
                                           "casos_api": by_kind["api"], "consultas_datos": by_kind["database"], "casos_web": by_kind["web"],
                                           "aprobados": passed, "fallidos": len(results) - passed, "trace_id": state.trace_id},
                                   logs=[_evidence_line(r) for r in results])
    warnings = ["Ejecución en el entorno sintético, nunca en producción." if source == "valkiria" else f"Resultados recibidos del {origin}.", *notes]
    if passed < len(results):
        warnings.append(f"{len(results) - passed} caso(s) fallidos: revísalos (borradores de defecto) antes de aprobar el pull request.")
    return StepOutput({"platform": platform, "framework": framework, "results": results, "report": report, "source": source, "run_id": run_id,
                       "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed, "errors": sum(r.get("result") == "error" for r in results), **by_kind}},
                      based_on, warnings,
                      f"{passed} de {len(results)} casos aprobados ({origin}).")


async def run_triage(state: WorkflowState, service: ValkiriaService, memory: str = "") -> StepOutput:
    """Revisión de fallos (HU-010): un borrador de defecto por caso fallido, con una clasificación sugerida que decide el humano."""
    execution = state.artifacts["execution"]
    cases = {c["id"]: c for c in state.artifacts["matrix"].payload.get("cases", [])} if "matrix" in state.artifacts else {}
    previous = state.artifacts.get("triage")
    decided = previous.approval.details.get("decisions", {}) if previous and previous.approval else {}
    carried = carry_decisions([d | {"decision": decided.get(d["id"])} for d in previous.payload.get("defects", [])]) if previous else {}
    defects = triage(execution.payload.get("results", []), cases, execution_version=execution.version,
                     report_id=(execution.payload.get("report") or {}).get("id"), story_title=str(state.artifacts["story"].payload.get("title", "")), previous=carried)
    by_suggestion = {key: sum(d["suggested"] == key for d in defects) for key in DECISIONS}
    warnings = ["Borradores en formato Bug de Azure Boards (vista previa): se publican cuando se decida la herramienta de gestión (HU-004B)."] if defects else []
    if any(d["decision"] for d in defects):
        warnings.append("Se precargaron tus decisiones anteriores sobre los mismos casos; confírmalas.")
    return StepOutput({"execution_version": execution.version, "source": execution.payload.get("source", "valkiria"), "defects": defects,
                       "summary": {"failed": len(defects), **by_suggestion}}, _versions(state, "execution"), warnings,
                      f"{len(defects)} fallo(s) por revisar." if defects else "Sin fallos que revisar.")


def _evidence_line(result: dict[str, Any]) -> str:
    if result.get("kind") == "web":
        failure = f" en el paso «{result.get('failed_step')}»: {result.get('detail')}" if result.get("result") == "fail" else ""
        return (f"{result['case_id']} [web] {result.get('request', '')} -> {result.get('steps_executed', 0)}/{result.get('steps_total', 0)} pasos "
                f"({result.get('result')}){failure}; captura: {result.get('screenshot') or 'sin captura'}")
    return f"{result['case_id']} [{result.get('kind')}] {result.get('request', '')} -> {result.get('status')} esperado {result.get('expected_status', '')} ({result.get('result', '')})"


RUN_COMMANDS = {"playwright": "npx playwright test", "selenium": "pytest tests --junitxml=test-results.xml", "restassured": "mvn -B test",
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
    data = state.artifacts.get("data_validation")
    queries = data.payload.get("queries") if data and data.approved else None
    text = generate_pipeline_yaml(scripts=scripts or None, data_validation=bool(queries), workflow_id=state.id)
    warnings = [] if scripts else ["Sin scripts todavía: la etapa de test queda con advertencia y no reporta pruebas aprobadas (HU-007)."]
    if data and not data.approved:
        warnings.append("Las consultas de datos (HU-011) no están aprobadas: el pipeline no incluye la etapa de validación de datos.")
    warnings.append("Requiere en el grupo de variables (ligado a Key Vault): synthetic-database-url, valkiria-url y valkiria-pipeline-token; nunca en el repositorio.")
    return StepOutput({"yaml": text, "scripts": scripts, "files": pipeline_files(queries), "data_validation": bool(queries), "reports_to": "valkiria",
                       "delivery": "pull_request_only"}, _versions(state, "automation") | ({"data_validation": data.version} if queries else {}), warnings,
                      f"Pipeline YAML con {len(scripts)} comando(s) de prueba" + (" y validación de datos." if queries else "."))


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
    "data_validation": run_data_validation,
    "execution": run_execution,
    "triage": run_triage,
    "pipeline": run_pipeline,
    "performance_design": run_performance_design,
    "azure_work_item": run_azure_work_item,
}
