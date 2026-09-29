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
            results.append({"case_id": case["id"], "criterion_id": case.get("criterion_id"), "type": case.get("type"), "request": f"{call['method']} {call['path']}",
                            "expected_status": call["expect"], "status": status, "result": "pass" if status in call["expect"] else "fail",
                            "duration_ms": round((time.perf_counter() - started) * 1000, 1), "response_excerpt": body})
    return results
