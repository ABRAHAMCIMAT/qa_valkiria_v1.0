"""Archivos que acompañan al pipeline de Azure DevOps (HU-007) para ejecutar allá lo mismo que Valkiria ejecuta aquí.

- validate_data.py: corre las consultas HU-011 aprobadas en una transacción de solo lectura y deja un JUnit.
- report_results.py: junta los JUnit de la corrida y los envía a Valkiria, que los registra como una nueva
  ejecución de HU-010 y prepara la revisión de fallos.

Solo usan la biblioteca estándar (y psycopg para la base). Los secretos llegan como variables del grupo ligado a
Key Vault; nunca se escriben en el repositorio.
"""

from __future__ import annotations

import json
from typing import Any

VALIDATE_DATA = '''"""HU-011 en el pipeline: consultas de validación de datos aprobadas en Valkiria, de solo lectura, con salida JUnit."""
import argparse
import json
import os
import time
from xml.sax.saxutils import escape, quoteattr

import psycopg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default="valkiria/queries.json")
    parser.add_argument("--junit", default="data-validation-results.xml")
    args = parser.parse_args()
    with open(args.queries, encoding="utf-8") as source:
        queries = json.load(source)
    os.makedirs(os.path.dirname(args.junit) or ".", exist_ok=True)
    cases, failures, errors = [], 0, 0
    with psycopg.connect(os.environ["SYNTHETIC_DATABASE_URL"], autocommit=False) as connection:
        for query in queries:
            started = time.perf_counter()
            outcome = ""
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(query["sql"])
                    rows = cursor.fetchmany(20)
                ok = (not rows) if query["expect"] == "empty" else bool(rows)
                if not ok:
                    failures += 1
                    expected = "sin filas" if query["expect"] == "empty" else "con filas"
                    outcome = f"<failure message={quoteattr(f'Se esperaba {expected} y devolvio {len(rows)} fila(s)')}>{escape(str(rows[:3]))}</failure>"
            except psycopg.Error as exc:
                errors += 1
                outcome = f"<error message={quoteattr(type(exc).__name__)}>{escape(str(exc)[:300])}</error>"
            finally:
                connection.rollback()
            name = f"{query.get('case_id') or query['id']} {query['id']}: {query['purpose']}"
            cases.append(f'  <testcase classname="HU-011" name={quoteattr(name)} time="{time.perf_counter() - started:.3f}">{outcome}</testcase>')
    with open(args.junit, "w", encoding="utf-8") as output:
        output.write(f'<?xml version="1.0" encoding="UTF-8"?>\\n<testsuite name="HU-011 Validacion de datos" tests="{len(cases)}" failures="{failures}" errors="{errors}">\\n'
                     + "\\n".join(cases) + "\\n</testsuite>\\n")
    print(f"{len(cases)} consulta(s): {failures} fallida(s), {errors} con error")


if __name__ == "__main__":
    main()
'''

REPORT_RESULTS = '''"""Envía a Valkiria los resultados JUnit de esta corrida (HU-010): quedan como una nueva ejecución con su revisión de fallos."""
import argparse
import glob
import json
import os
import urllib.request
import xml.etree.ElementTree as ET  # archivos generados por esta misma corrida


def collect(folder):
    results = []
    for path in sorted(glob.glob(os.path.join(folder, "**", "*.xml"), recursive=True)):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            problem = next((child for child in case if child.tag in ("failure", "error", "skipped")), None)
            result = "pass" if problem is None else {"failure": "fail", "error": "error", "skipped": "skipped"}[problem.tag]
            message = "" if problem is None else (problem.get("message") or problem.text or "")[:300]
            results.append({"name": case.get("name", ""), "classname": case.get("classname", ""), "result": result, "message": message,
                            "duration_ms": round(float(case.get("time") or 0) * 1000, 1), "file": os.path.basename(path)})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default=".")
    args = parser.parse_args()
    results = collect(args.results)
    if not results:
        print("##vso[task.logissue type=warning]No hay resultados JUnit que enviar a Valkiria")
        return
    body = json.dumps({"run_id": os.environ.get("BUILD_ID", "local"), "source": "azure-devops", "results": results}).encode()
    url = os.environ["VALKIRIA_URL"].rstrip("/") + "/v1/workflows/" + os.environ["VALKIRIA_WORKFLOW_ID"] + "/pipeline-results"
    if not url.startswith("https://") and not url.startswith("http://localhost"):
        raise SystemExit("VALKIRIA_URL debe usar https")
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json",
                                                                          "Authorization": "Bearer " + os.environ["VALKIRIA_PIPELINE_TOKEN"]})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - URL validada arriba
        print(f"Valkiria registro {len(results)} resultado(s): HTTP {response.status}")


if __name__ == "__main__":
    main()
'''


def pipeline_files(queries: list[dict[str, Any]] | None) -> dict[str, str]:
    files = {"valkiria/report_results.py": REPORT_RESULTS}
    if queries:
        ready = [{k: q.get(k) for k in ("id", "case_id", "criterion_id", "purpose", "sql", "expect")} for q in queries if q.get("status") == "lista"]
        files |= {"valkiria/validate_data.py": VALIDATE_DATA, "valkiria/queries.json": json.dumps(ready, ensure_ascii=False, indent=2) + "\n"}
    return files
