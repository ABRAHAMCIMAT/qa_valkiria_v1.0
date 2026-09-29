"""HU-011 en el pipeline: el script generado corre las consultas aprobadas contra PostgreSQL, en solo lectura, y deja un JUnit."""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET  # JUnit generado por la prueba

import pytest

from valkiria.application.pipeline_files import pipeline_files
from valkiria.infrastructure.settings import Settings

pytestmark = pytest.mark.skipif(os.getenv("RUN_SYNTHETIC_E2E") != "1", reason="E2E sintética deshabilitada; usar RUN_SYNTHETIC_E2E=1")


def test_generated_data_validation_runs_read_only_and_writes_junit(tmp_path):
    url = Settings().secret("synthetic_database_url")
    if not url or not url.startswith("postgresql"):
        pytest.fail("Requiere VALKIRIA_SYNTHETIC_DATABASE_URL apuntando a PostgreSQL")
    queries = [
        {"id": "DV-01", "case_id": "TC-AC-01-P", "purpose": "Hay vehículos con stock", "sql": "SELECT vehicle_id FROM vehicles WHERE stock > 0", "expect": "rows", "status": "lista"},
        {"id": "DV-02", "case_id": "TC-AC-01-N", "purpose": "Ningún stock negativo", "sql": "SELECT vehicle_id FROM vehicles WHERE stock < 0", "expect": "empty", "status": "lista"},
        {"id": "DV-03", "case_id": "TC-AC-02-N", "purpose": "Regla mal planteada", "sql": "SELECT vehicle_id FROM vehicles WHERE stock = 0", "expect": "empty", "status": "lista"},
        {"id": "DV-04", "case_id": "TC-AC-02-E", "purpose": "Escritura bloqueada", "sql": "UPDATE vehicles SET stock = 99", "expect": "empty", "status": "lista"},
    ]
    for name, content in pipeline_files(queries).items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    junit = tmp_path / "out" / "data-validation-results.xml"
    env = os.environ | {"SYNTHETIC_DATABASE_URL": url.replace("postgresql+psycopg://", "postgresql://")}
    run = subprocess.run([sys.executable, "valkiria/validate_data.py", "--junit", str(junit)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    cases = {c.get("name").split()[1]: c for c in ET.parse(junit).getroot().iter("testcase")}
    assert cases["DV-01:"].find("failure") is None and cases["DV-02:"].find("failure") is None
    assert cases["DV-03:"].find("failure") is not None  # Kicks tiene stock 0: la consulta lo detecta
    assert cases["DV-04:"].find("error") is not None  # la transacción de solo lectura rechaza la escritura
    count = subprocess.run([sys.executable, "-c", "import os, psycopg; c = psycopg.connect(os.environ['SYNTHETIC_DATABASE_URL']); "
                            "print(c.execute('SELECT count(*) FROM vehicles WHERE stock = 99').fetchone()[0])"], env=env, capture_output=True, text=True)
    assert count.stdout.strip() == "0"
    assert json.loads((tmp_path / "valkiria/queries.json").read_text())[0]["id"] == "DV-01"
