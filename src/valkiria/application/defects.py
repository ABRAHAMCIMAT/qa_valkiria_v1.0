"""Revisión de fallos de la ejecución (HU-010): un borrador de defecto por caso fallido, ligado a su caso y criterio.

Determinista y sin modelo: cada fallo trae una clasificación sugerida con su motivo, pero la decisión es humana
(RT-02): "defect" (defecto del producto), "test_issue" (el caso, el script o la consulta están mal planteados) o
"environment" (ambiente o infraestructura). Mientras haya fallos sin revisar, el pull request no se aprueba.

El borrador usa los campos de un Bug de Azure Boards (vista previa): la publicación queda pendiente de decidir la
herramienta de gestión (HU-004B), sin inventar una integración que no existe.
"""

from __future__ import annotations

import html
import re
from typing import Any

from valkiria.application.web_steps import KNOWN_MESSAGES, OPTIONS, plain, plan_case

DECISIONS = {"defect": "Defecto del producto", "test_issue": "Caso, script o consulta mal planteados", "environment": "Ambiente o infraestructura"}
_SEVERITY = {"high": "2 - High", "medium": "3 - Medium", "low": "4 - Low"}
_KNOWN_TEXTS = {message for _, message, _ in KNOWN_MESSAGES}


def _known(text: str) -> bool:
    """El texto es un mensaje de las pantallas o un dato que existe (modelo, concesionario, cliente)."""
    folded = plain(text)
    return any(folded in plain(k) or plain(k) in folded for k in _KNOWN_TEXTS) or any(folded in plain(o) for values in OPTIONS.values() for o in values)


def case_design_issue(case: dict[str, Any] | None) -> str | None:
    """Problemas de diseño visibles en el propio caso web: nunca envía el formulario o va y viene entre pantallas."""
    if not case or not case.get("steps"):
        return None
    plan = plan_case(case)
    visits = [plan.route] + [s.target for s in plan.steps if s.action == "open"]
    if plan.anchors and not any(s.action == "click" for s in plan.steps) and any(s.action in {"select", "fill", "tick"} for s in plan.steps):
        return "el caso captura datos pero nunca presiona el botón que envía el formulario, así que la pantalla no puede mostrar el resultado esperado"
    if len(visits) != len(set(visits)):
        return "los pasos van y vienen entre pantallas, así que lo capturado en una se pierde al volver a otra"
    return None


def suggest(result: dict[str, Any], case: dict[str, Any] | None = None) -> tuple[str, str]:
    """(clasificación sugerida, motivo). Es una sugerencia para acelerar la revisión, nunca la decisión."""
    kind = result.get("kind")
    if result.get("result") == "error":
        return "test_issue", "La consulta no se pudo ejecutar (error de SQL): hay que corregirla, no es un defecto de datos."
    if kind == "api":
        status = result.get("status")
        if status is None:
            return "environment", "La aplicación no respondió: revisa el ambiente antes de reportar un defecto."
        if int(status) >= 500:
            return "defect", f"La API respondió {status} (error interno): es un defecto del producto."
        return "defect", f"La API respondió {status} y se esperaba {' o '.join(str(s) for s in result.get('expected_status') or [])}."
    if kind == "web":
        step = str(result.get("failed_step") or "")
        detail = str(result.get("detail") or "")
        if step and step != "Resultado esperado":
            return "test_issue", f"El paso «{step}» no se pudo ejecutar en la pantalla ({detail}): el caso o el script no corresponde a la interfaz."
        design = case_design_issue(case)
        if design:
            return "test_issue", f"El resultado no fue el esperado y {design}: revisa el caso."
        missing = re.search(r"No se ve «(.+?)»", detail)
        if missing and not _known(missing.group(1)):
            return "test_issue", f"El resultado esperado pide ver «{missing.group(1)}», que la aplicación no muestra en ningún caso: revisa el caso."
        return "defect", f"Todos los pasos se ejecutaron y el resultado no fue el esperado ({detail}). Confirma que los datos del caso llevan a ese resultado."
    if kind == "database":
        return "defect", "La consulta corrió y encontró datos que no cumplen la regla (o no encontró los esperados)."
    return "defect", "El caso no obtuvo el resultado esperado."


def _repro(case: dict[str, Any] | None, result: dict[str, Any]) -> list[str]:
    if result.get("kind") == "database":
        return [f"Ejecutar en la base sintética: {result.get('request')}", f"Resultado esperado: {', '.join(result.get('expected_status') or [])}."]
    if result.get("kind") == "api":
        return [f"Enviar {result.get('request')} a la app sintética.", f"Código esperado: {' o '.join(str(s) for s in result.get('expected_status') or [])}."]
    steps = list((case or {}).get("steps") or [])
    return steps or [f"Abrir {result.get('request', '').removeprefix('GET ')} en la app sintética."]


def triage(results: list[dict[str, Any]], cases: dict[str, dict[str, Any]], *, execution_version: int, report_id: str | None, story_title: str,
           previous: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Un borrador por resultado fallido o con error, en el orden de la ejecución."""
    defects = []
    for n, result in enumerate(r for r in results if r.get("result", r.get("status")) != "pass"):
        case = cases.get(str(result.get("case_id")))
        suggested, reason = suggest(result, case)
        actual = result.get("detail") or result.get("status") or "sin respuesta"
        expected = ", ".join((result.get("expected") or {}).get("texts") or []) if result.get("kind") == "web" else ", ".join(str(s) for s in result.get("expected_status") or [])
        title = f"[{result.get('case_id')}] {(case or {}).get('scenario') or result.get('purpose') or result.get('request')}"[:120]
        defect = {"id": f"DEF-{n + 1:02d}", "case_id": result.get("case_id"), "criterion_id": result.get("criterion_id") or (case or {}).get("criterion_id"),
                  "kind": result.get("kind"), "title": title, "severity": _SEVERITY.get(str((case or {}).get("priority")), "3 - Medium"),
                  "expected": (case or {}).get("expected_result") or expected, "actual": str(actual)[:300], "failed_step": result.get("failed_step"),
                  "repro_steps": _repro(case, result), "suggested": suggested, "suggested_reason": reason,
                  "evidence": {"execution_version": execution_version, "report_id": report_id, "screenshot": bool(result.get("screenshot_jpeg"))},
                  "source": result.get("source", "valkiria")}
        defect["decision"] = (previous or {}).get(_key(defect))
        defect["azure_bug"] = azure_bug(defect, story_title)
        defects.append(defect)
    return defects


def _key(defect: dict[str, Any]) -> str:
    # Por caso y tipo: el mismo fallo llega con otro texto desde el pipeline (mensaje de Playwright) que desde Valkiria.
    return f"{defect['case_id']}|{defect['kind']}"


def carry_decisions(defects: list[dict[str, Any]]) -> dict[str, str]:
    """Decisiones ya tomadas sobre el mismo caso: al repetir la ejecución se precargan y el humano solo las confirma."""
    return {_key(d): d["decision"] for d in defects if d.get("decision")}


def azure_bug(defect: dict[str, Any], story_title: str) -> dict[str, Any]:
    """Vista previa de los campos de un Bug de Azure Boards; no se publica hasta decidir la herramienta (HU-004B)."""
    steps = "".join(f"<li>{html.escape(str(s))}</li>" for s in defect["repro_steps"])
    return {"System.WorkItemType": "Bug", "System.Title": defect["title"], "Microsoft.VSTS.Common.Severity": defect["severity"],
            "Microsoft.VSTS.TCM.ReproSteps": f"<ol>{steps}</ol><p><b>Esperado:</b> {html.escape(str(defect['expected']))}</p>"
                                             f"<p><b>Obtenido:</b> {html.escape(str(defect['actual']))}</p>",
            "System.Tags": "; ".join(t for t in ("valkiria", f"caso:{defect['case_id']}", f"criterio:{defect['criterion_id']}" if defect["criterion_id"] else "",
                                                  f"hu:{story_title[:60]}") if t)}
