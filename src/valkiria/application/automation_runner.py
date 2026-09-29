"""Ejecución controlada de los scripts de API (HU-010) contra la app sintética de Nissan.

Ejecuta exactamente las llamadas que contienen los scripts generados (`script_generation.api_call`): no simula
resultados. Cada caso queda aprobado o fallido con el código esperado frente al obtenido, su duración y un extracto
de la respuesta; la evidencia se consolida en un reporte PDF descargable.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from valkiria.application.script_generation import api_call


async def execute_api_cases(cases: list[dict[str, Any]], *, base_url: str, transport: httpx.AsyncBaseTransport | None = None,
                            timeout: float = 10.0) -> list[dict[str, Any]]:
    results = []
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport) as client:
        for case in cases:
            call = api_call(case)
            started = time.perf_counter()
            try:
                response = await client.request(call["method"], call["path"], json=call["body"])
                status, body = response.status_code, response.text[:300]
            except httpx.HTTPError as exc:
                status, body = None, f"sin respuesta ({type(exc).__name__})"
            results.append({"case_id": case["id"], "criterion_id": case.get("criterion_id"), "type": case.get("type"), "kind": "api", "request": f"{call['method']} {call['path']}",
                            "expected_status": call["expect"], "status": status, "result": "pass" if status in call["expect"] else "fail",
                            "duration_ms": round((time.perf_counter() - started) * 1000, 1), "response_excerpt": body})
    return results


async def execute_data_queries(queries: list[dict[str, Any]], *, executor, trace_id: str) -> list[dict[str, Any]]:
    """HU-011 dentro de HU-010: cada consulta aprobada se ejecuta con el ejecutor seguro (solo lectura, perfil sintético).

    Veredicto: con expect "empty" la regla se cumple si no hay filas (la consulta busca violaciones); con "rows", si las hay.
    """
    import asyncio

    results = []
    for query in queries:
        if query.get("status") in {"bloqueada", "inválida"}:
            continue
        started = time.perf_counter()
        outcome, _ = await asyncio.to_thread(executor.execute, script=query["sql"], case_id=str(query.get("case_id") or query.get("id")), trace_id=trace_id, output_format="pdf")
        rows = outcome.get("rows") or []
        expect = query.get("expect", "empty")
        completed = outcome.get("status") == "completed"
        ok = completed and ((expect == "empty" and not rows) or (expect == "rows" and bool(rows)))
        results.append({"case_id": query.get("case_id") or query.get("id"), "criterion_id": query.get("criterion_id"), "kind": "database",
                        "request": query["sql"][:160], "expected_status": ["sin filas" if expect == "empty" else "con filas"],
                        "status": f"{len(rows)} fila(s)" if completed else f"error de consulta ({outcome.get('error_type') or outcome.get('status')})",
                        # Una consulta que no corre no es un defecto de datos: se reporta como error para corregirla.
                        "result": "pass" if ok else "fail" if completed else "error", "duration_ms": round((time.perf_counter() - started) * 1000, 1), "purpose": query.get("purpose"),
                        "response_excerpt": str(rows[:3])[:300]})
    return results
