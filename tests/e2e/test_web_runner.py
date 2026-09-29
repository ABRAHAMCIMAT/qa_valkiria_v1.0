"""HU-010 web: el runner ejecuta en Chromium los pasos de la matriz contra las pantallas reales de la app sintética."""

import asyncio
import os
import socket
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_SYNTHETIC_E2E") != "1", reason="E2E sintética deshabilitada; usar RUN_SYNTHETIC_E2E=1")
pytest.importorskip("playwright")


@pytest.fixture(scope="module")
def base_url():
    import uvicorn

    from valkiria.synthetic_app.app import create_synthetic_app
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_synthetic_app("sqlite+pysqlite:///:memory:"), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


CASES = [
    {"id": "TC-SIN-STOCK", "scenario": "Registrar orden con vehículo sin stock", "data": {"vehiculo": "Kicks 2025"},
     "steps": ["Ir a la pantalla de registro de orden", "Seleccionar el vehículo", "Hacer clic en el botón Registrar orden"],
     "expected_result": "La orden es rechazada con el mensaje Vehículo sin stock"},
    {"id": "TC-ORDEN-OK", "scenario": "Registrar orden válida", "data": {"vehiculo": "Sentra", "total": "289900"},
     "steps": ["Ir a registrar orden", "Seleccionar el vehículo", "Ingresar el total", "Hacer clic en Registrar orden"], "expected_result": "La orden queda aprobada"},
    {"id": "TC-INACTIVO", "scenario": "Consultar inventario de concesionario inactivo", "data": {"concesionario": "Nissan Centro Sintético"},
     "steps": ["Abrir la consulta de inventario", "Seleccionar el concesionario", "Hacer clic en Buscar"], "expected_result": "Se muestra el mensaje Concesionario inactivo"},
    {"id": "TC-DISPONIBLES", "scenario": "Consultar inventario disponible", "data": {},
     "steps": ["Abrir la consulta de inventario", "Seleccionar el concesionario 'Apodaca'", "Marcar la casilla Solo disponibles", "Hacer clic en Buscar"],
     "expected_result": "Se muestra el Sentra disponible"},
    {"id": "TC-INEXISTENTE", "scenario": "Exportar inventario", "data": {}, "steps": ["Abrir la consulta de inventario", "Hacer clic en Exportar"],
     "expected_result": "Se descarga el archivo"},
    {"id": "TC-ERRONEO", "scenario": "Orden con vehículo sin stock mal esperada", "data": {"vehiculo": "Kicks 2025"},
     "steps": ["Ir a registrar orden", "Seleccionar el vehículo", "Hacer clic en Registrar orden"], "expected_result": "La orden queda aprobada"},
]


def test_runner_executes_the_steps_and_reports_the_failing_one(base_url, tmp_path):
    from valkiria.infrastructure.playwright_runner import PlaywrightRunner
    results = {r["case_id"]: r for r in asyncio.run(PlaywrightRunner(step_timeout_seconds=2).run(base_url=base_url, cases=CASES, artifacts_dir=str(tmp_path)))}
    assert {cid for cid, r in results.items() if r["result"] == "pass"} == {"TC-SIN-STOCK", "TC-ORDEN-OK", "TC-INACTIVO", "TC-DISPONIBLES"}
    missing = results["TC-INEXISTENTE"]
    assert missing["failed_step"] == "Hacer clic en Exportar" and missing["screenshot_jpeg"]
    wrong = results["TC-ERRONEO"]  # el sistema rechaza la orden; el caso esperaba aprobación: la falla se reporta con su motivo
    assert (wrong["failed_step"], wrong["detail"]) == ("Resultado esperado", "No se ve «Orden aprobada»")
    assert all(r["kind"] == "web" and r["screenshot"] for r in results.values())
