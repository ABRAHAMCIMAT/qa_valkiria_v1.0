"""Validación de las salidas del LLM contra las reglas de negocio de las HU (v3.0).

Un modelo pequeño como Llama 3.2 3B puede omitir casos o criterios; estas validaciones permiten
pedirle una corrección concreta y, si no basta, completar de forma determinista.
"""

from __future__ import annotations

import unicodedata
from typing import Any

INVEST_NAMES = ("independiente", "negociable", "valiosa", "estimable", "pequena", "testeable")
CASE_TYPES = ("positive", "negative", "edge")
MAX_CASES = 30
MAX_CRITERIA_PER_MATRIX = MAX_CASES // len(CASE_TYPES)


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn").strip()


def validate_invest(criteria: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []
    names = [_plain(str(c.get("name", ""))) for c in criteria]
    missing = [name for name in INVEST_NAMES if name not in names]
    if missing or len(criteria) != len(INVEST_NAMES):
        findings.append(f"Deben evaluarse exactamente los 6 criterios INVEST; faltan: {', '.join(missing) or 'ninguno'}.")
    for criterion in criteria:
        status = criterion.get("status")
        if status not in {"cumple", "parcial", "no_cumple"}:
            findings.append(f"Estado inválido en {criterion.get('name')}: {status}.")
        if not str(criterion.get("justification") or "").strip():
            findings.append(f"Falta la justificación de {criterion.get('name')}.")
        if status in {"parcial", "no_cumple"} and not str(criterion.get("suggestion") or "").strip():
            findings.append(f"{criterion.get('name')} no se cumple por completo y no incluye una sugerencia concreta.")
    return findings


def validate_matrix(cases: list[dict[str, Any]], criterion_ids: list[str]) -> list[str]:
    findings: list[str] = []
    if len(cases) > MAX_CASES:
        findings.append(f"La matriz tiene {len(cases)} casos; el máximo es {MAX_CASES}.")
    known = set(criterion_ids)
    for case in cases:
        if case.get("criterion_id") not in known:
            findings.append(f"El caso {case.get('id')} no está vinculado a un criterio existente ({case.get('criterion_id')}).")
        if not str(case.get("scenario") or "").strip() or not str(case.get("expected_result") or "").strip():
            findings.append(f"El caso {case.get('id')} no tiene escenario o resultado esperado.")
    for criterion_id in criterion_ids:
        present = {case.get("type") for case in cases if case.get("criterion_id") == criterion_id}
        for case_type in CASE_TYPES:
            if case_type not in present:
                findings.append(f"Falta un caso {case_type} para el criterio {criterion_id}.")
    return findings


def missing_case_slots(cases: list[dict[str, Any]], criterion_ids: list[str]) -> list[tuple[str, str]]:
    return [(criterion_id, case_type) for criterion_id in criterion_ids for case_type in CASE_TYPES
            if not any(c.get("criterion_id") == criterion_id and c.get("type") == case_type for c in cases)]
