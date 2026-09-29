"""Generación del código de los scripts de automatización (HU-009) a partir de los casos de la matriz.

Determinista (sin esperar al modelo) y trazable: un script por caso con su id, patrón Page Object, datos
externalizados en un archivo aparte y verificación del resultado esperado.

- Web (Playwright TypeScript, Selenium Python): los pasos se traducen a localizadores semánticos
  (getByRole/getByLabel/getByText, o su equivalente) derivados del texto del paso. Son un punto de partida
  ejecutable; si la interfaz real usa otros textos, se ajustan en el Page Object, en un solo lugar.
- API (Playwright request, RestAssured, Postman-Newman): cada caso se asocia a un endpoint real de la app
  sintética de Nissan según su contenido, con el código de estado esperado según el tipo de caso.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

# Secretos que nunca deben llegar a un pull request (HU-009, regla 6; RT-05).
_SECRETS = [
    re.compile(r"(?i)\b(password|passwd|contraseña|secret|token|api[_-]?key)\b\s*[:=]\s*['\"][^'\"]{4,}['\"]"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{20,}"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@]+@"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

WEB_FRAMEWORKS = {"playwright", "selenium"}
API_FRAMEWORKS = {"playwright", "restassured", "postman-newman"}
FRAMEWORKS = {"playwright": "Playwright", "selenium": "Selenium", "restassured": "RestAssured", "postman-newman": "Postman-Newman"}

# Endpoints de la app sintética de Nissan (src/valkiria/synthetic_app/app.py).
SYNTHETIC_API = [
    (r"\bcancel", "PUT", "/sales-orders/1/cancel", None),
    (r"\b(orden|ordenes|venta|ventas|vender|compra|comprar|apartar)\b", "POST", "/sales-orders", {"dealer_id": 1, "vehicle_id": 1, "customer_id": 1, "total": 289900.0}),
    (r"\b(cita|citas|servicio|taller|mantenimiento)\b", "GET", "/service-appointments", None),
    (r"\b(concesionari\w*|agencias?|dealers?)\b", "GET", "/dealers", None),
    (r"\b(inventario|ubicacion)\b", "GET", "/inventory", None),
    (r"\b(vehicul\w*|autos?|stock|modelos?|precios?|disponib\w*)\b", "GET", "/vehicles", None),
]


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def _ident(text: str, *, upper_first: bool = False, limit: int = 5) -> str:
    words = re.findall(r"[a-z0-9]+", _plain(text))[:limit] or ["paso"]
    name = words[0] + "".join(w.capitalize() for w in words[1:])
    if name[0].isdigit():
        name = "p" + name
    return name[0].upper() + name[1:] if upper_first else name


def _snake(text: str, limit: int = 5) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", _plain(text))[:limit]) or "paso"


_QUOTED = re.compile(r"[\"'«“](.+?)[\"'»”]")


def _target(step: str) -> str:
    """Texto visible al que apunta un paso: "Hacer clic en Buscar" → "Buscar"; "Seleccionar el concesionario 'Apodaca'" → "concesionario"."""
    without_value = _QUOTED.sub("", step).strip()
    if without_value != step.strip() and _action(step) in {"fill", "select"}:
        step = without_value
    elif _QUOTED.search(step):
        return _QUOTED.search(step).group(1)[:60]
    body = re.sub(r"^\s*\S+\s*", "", step)  # sin el verbo
    body = re.sub(r"(?i)^(en|a|el|la|los|las|un|una|de|del)\s+", "", body)
    body = re.sub(r"(?i)^(boton|botón|campo|enlace|menu|menú|opcion|opción|pestaña|filtro)\s+(de\s+)?", "", body)
    return (body.strip(" .") or step.strip(" ."))[:60].replace('"', "").replace("'", "")


def _default_value(step: str) -> str | None:
    quoted = _QUOTED.search(step)
    return quoted.group(1) if quoted else None


def _action(step: str) -> str:
    plain = _plain(step)
    if re.match(r"(abrir|ir|navegar|acceder|entrar|ingresar a la|visitar)\b", plain):
        return "open"
    if re.match(r"(ingresar|escribir|capturar|llenar|introducir|teclear|digitar)\b", plain):
        return "fill"
    if re.match(r"(seleccionar|elegir)\b", plain):
        return "select"
    if re.match(r"(verificar|validar|comprobar|revisar|confirmar|observar|ver)\b", plain):
        return "check"
    return "click"


def scan_scripts(files: dict[str, str], case_ids: list[str]) -> dict[str, Any]:
    """HU-009, regla 6: lint mínimo y detección de secretos antes de abrir el pull request."""
    secrets = [name for name, content in files.items() if any(p.search(content) for p in _SECRETS)]
    unbalanced = [name for name, content in files.items() if name.endswith((".ts", ".java", ".py")) and
                  any(content.count(a) != content.count(b) for a, b in ("()", "{}", "[]"))]
    untraced = [cid for cid in case_ids if not any(cid in content for content in files.values())]
    findings = [f"posible secreto en {n}" for n in secrets] + [f"sintaxis desbalanceada en {n}" for n in unbalanced] + [f"caso sin script: {c}" for c in untraced]
    return {"lint": "ok" if not unbalanced else "falló", "secrets_found": len(secrets), "traceability": "ok" if not untraced else "incompleta",
            "findings": findings, "pr_allowed": not findings}


def generate_scripts(cases: list[dict[str, Any]], *, framework: str, platform: str, feature: str, base_url: str = "http://localhost:8090") -> dict[str, str]:
    framework, platform = framework.lower(), platform.lower()
    cases = [{**c, "scenario": str(c.get("scenario") or c.get("id")), "steps": [str(x) for x in c.get("steps") or []]} for c in cases]
    feature_name = _ident(feature, upper_first=True, limit=4)
    data = {c["id"]: c.get("data") or {} for c in cases}
    if platform == "api":
        return _api(cases, framework, feature_name, base_url)
    if framework == "selenium":
        return _selenium(cases, feature_name, data)
    return _playwright_web(cases, feature_name, data)


# --- Web --------------------------------------------------------------------------------------------------

def _playwright_web(cases: list[dict[str, Any]], feature: str, data: dict[str, Any]) -> dict[str, str]:
    methods: dict[str, str] = {}
    for case in cases:
        for step in case.get("steps") or [case["scenario"]]:
            name = _ident(step)
            if name in methods:
                continue
            target, action = json.dumps(_target(step), ensure_ascii=False), _action(step)
            body = {
                "open": "    await this.page.goto(process.env.BASE_URL ?? '/');",
                "fill": f"    await this.page.getByLabel(new RegExp({target}, 'i')).fill(String(value ?? ''));",
                "select": f"    await this.page.getByRole('combobox', {{ name: new RegExp({target}, 'i') }}).selectOption(String(value ?? ''));",
                "check": f"    await expect(this.page.getByText(new RegExp({target}, 'i')).first()).toBeVisible();",
                "click": f"    await this.page.getByRole('button', {{ name: new RegExp({target}, 'i') }}).or(this.page.getByText(new RegExp({target}, 'i'))).first().click();",
            }[action]
            default = _default_value(step)
            signature = f"value: unknown = {json.dumps(default, ensure_ascii=False)}" if action in {"fill", "select"} and default else ("value?: unknown" if action in {"fill", "select"} else "")
            methods[name] = f"  /** {step} */\n  async {name}({signature}) {{\n{body}\n  }}\n"
    page = (f"import {{ expect, type Page }} from '@playwright/test';\n\n"
            f"/** Page Object de {feature}: los localizadores semánticos salen de los pasos de la matriz; ajústalos aquí si la interfaz usa otros textos. */\n"
            f"export class {feature}Page {{\n  constructor(private readonly page: Page) {{}}\n\n" + "\n".join(methods.values()) +
            "\n  async expectResult(expected: string) {\n    await expect(this.page.getByText(new RegExp(expected.slice(0, 40), 'i')).first()).toBeVisible();\n  }\n}\n")
    files = {f"tests/pages/{feature}Page.ts": page, f"tests/data/{_snake(feature)}.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
             "playwright.config.ts": "import { defineConfig } from '@playwright/test';\n\nexport default defineConfig({\n  testDir: './tests/specs',\n"
                                     "  use: { baseURL: process.env.BASE_URL, screenshot: 'only-on-failure', trace: 'retain-on-failure' },\n"
                                     "  reporter: [['list'], ['junit', { outputFile: 'test-results.xml' }]],\n});\n",
             "tsconfig.json": json.dumps({"compilerOptions": {"target": "ES2022", "module": "commonjs", "strict": True, "esModuleInterop": True, "resolveJsonModule": True}}, indent=2) + "\n"}
    for case in cases:
        steps = "\n".join(
            f"  await test.step({json.dumps(step, ensure_ascii=False)}, async () => {{ await app.{_ident(step)}("
            f"{'data[' + json.dumps(next(iter(case.get('data') or {}), ''), ensure_ascii=False) + ']' if _action(step) in {'fill', 'select'} and case.get('data') else ''}); }});"
            for step in (case.get("steps") or [case["scenario"]]))
        files[f"tests/specs/{_snake(case['id'], 8)}.spec.ts"] = (
            f"import {{ test }} from '@playwright/test';\nimport {{ {feature}Page }} from '../pages/{feature}Page';\nimport cases from '../data/{_snake(feature)}.json';\n\n"
            f"// Trazabilidad: {case['id']} · criterio {case.get('criterion_id')} · {case.get('type')}\n"
            f"test({json.dumps(case['id'] + ': ' + case['scenario'], ensure_ascii=False)}, {{ tag: ['@{case.get('type')}', '@{case.get('criterion_id')}'] }}, async ({{ page }}) => {{\n"
            f"  const app = new {feature}Page(page);\n  const data = cases[{json.dumps(case['id'])}] as Record<string, unknown>;\n"
            f"{steps}\n  await test.step('Resultado esperado', async () => {{ await app.expectResult({json.dumps(case.get('expected_result', ''), ensure_ascii=False)}); }});\n}});\n")
    return files


def _selenium(cases: list[dict[str, Any]], feature: str, data: dict[str, Any]) -> dict[str, str]:
    methods: dict[str, str] = {}
    for case in cases:
        for step in case.get("steps") or [case["scenario"]]:
            name = _snake(step)
            if name in methods:
                continue
            target, action = _target(step).replace("'", ""), _action(step)
            body = {
                "open": "        self.driver.get(os.getenv('BASE_URL', 'http://localhost:8090'))",
                "fill": f"        field = self.driver.find_element(By.XPATH, \"//label[contains(., '{target}')]/following::input[1]\")\n        field.clear()\n        field.send_keys(str(value or ''))",
                "select": f"        Select(self.driver.find_element(By.XPATH, \"//label[contains(., '{target}')]/following::select[1]\")).select_by_visible_text(str(value or ''))",
                "check": f"        assert self.driver.find_element(By.XPATH, \"//*[contains(., '{target}')]\").is_displayed()",
                "click": f"        self.driver.find_element(By.XPATH, \"//*[self::button or self::a][contains(., '{target}')]\").click()",
            }[action]
            default = _default_value(step)
            param = (f", value={json.dumps(default, ensure_ascii=False)}" if default else ", value=None") if action in {"fill", "select"} else ""
            methods[name] = f"    def {name}(self{param}):\n        \"\"\"{step.replace(chr(34), chr(39))}\"\"\"\n{body}\n"
    page = ("import os\n\nfrom selenium.webdriver.common.by import By\nfrom selenium.webdriver.support.ui import Select\n\n\n"
            f"class {feature}Page:\n    \"\"\"Page Object de {feature}: los localizadores salen de los pasos de la matriz; ajústalos aquí.\"\"\"\n\n"
            "    def __init__(self, driver):\n        self.driver = driver\n\n" + "\n".join(methods.values()) +
            "\n    def expect_result(self, expected):\n        assert expected[:40].lower() in self.driver.page_source.lower()\n")
    files = {f"tests/pages/{_snake(feature)}_page.py": page, f"tests/data/{_snake(feature)}.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
             "tests/conftest.py": "import pytest\nfrom selenium import webdriver\n\n\n@pytest.fixture\ndef driver():\n    driver = webdriver.Chrome()\n    yield driver\n    driver.quit()\n"}
    for case in cases:
        calls = "\n".join(f"    page.{_snake(step)}({'data.get(' + json.dumps(next(iter(case.get('data') or {}), '')) + ')' if _action(step) in {'fill', 'select'} and case.get('data') else ''})"
                          for step in (case.get("steps") or [case["scenario"]]))
        files[f"tests/test_{_snake(case['id'], 8)}.py"] = (
            f"import json\nfrom pathlib import Path\n\nfrom pages.{_snake(feature)}_page import {feature}Page\n\n"
            f"DATA = json.loads((Path(__file__).parent / 'data' / '{_snake(feature)}.json').read_text(encoding='utf-8'))\n\n\n"
            f"def test_{_snake(case['id'], 8)}(driver):\n    \"\"\"{case['id']} · criterio {case.get('criterion_id')} · {case.get('type')}: {case['scenario']}\"\"\"\n"
            f"    page = {feature}Page(driver)\n    data = DATA[{json.dumps(case['id'])}]\n{calls}\n    page.expect_result({json.dumps(case.get('expected_result', ''), ensure_ascii=False)})\n")
    return files


# --- API contra la app sintética de Nissan --------------------------------------------------------------------

def api_call(case: dict[str, Any]) -> dict[str, Any]:
    text = _plain(" ".join([case.get("scenario", ""), *case.get("steps", []), case.get("expected_result", "")]))
    method, path, body = next(((m, p, b) for pattern, m, p, b in SYNTHETIC_API if re.search(pattern, text)), ("GET", "/vehicles", None))
    kind = case.get("type")
    if kind == "negative":
        # Negativo: datos inválidos (ids inexistentes) → la API debe rechazar sin error interno.
        if method == "POST":
            body = {"dealer_id": 2, "vehicle_id": 2, "customer_id": 999, "total": 355000.0}
            return {"method": method, "path": path, "body": body, "expect": [404, 409, 422]}
        if method == "PUT":
            return {"method": method, "path": "/sales-orders/999999/cancel", "body": None, "expect": [404]}
        return {"method": method, "path": path + "?available_only=maybe", "body": None, "expect": [200, 422]}
    if kind == "edge" and method == "GET" and path in {"/vehicles", "/dealers"}:
        flag = "available_only" if path == "/vehicles" else "active_only"
        return {"method": method, "path": f"{path}?{flag}=true", "body": None, "expect": [200]}
    return {"method": method, "path": path, "body": body, "expect": [201] if method == "POST" else [200]}


def _api(cases: list[dict[str, Any]], framework: str, feature: str, base_url: str) -> dict[str, str]:
    calls = {c["id"]: api_call(c) for c in cases}
    if framework == "restassured":
        files = {}
        for case in cases:
            call, cls = calls[case["id"]], _ident(case["id"], upper_first=True, limit=8) + "Test"
            body = f"\n            .contentType(ContentType.JSON)\n            .body({json.dumps(json.dumps(call['body']))})" if call["body"] else ""
            codes = ", ".join(str(c) for c in call["expect"])
            files[f"src/test/java/com/nissan/qa/{cls}.java"] = (
                "package com.nissan.qa;\n\nimport io.restassured.http.ContentType;\nimport org.junit.jupiter.api.DisplayName;\nimport org.junit.jupiter.api.Test;\n\n"
                "import static io.restassured.RestAssured.given;\nimport static org.hamcrest.Matchers.anyOf;\nimport static org.hamcrest.Matchers.is;\n\n"
                f"/** Trazabilidad: {case['id']} · criterio {case.get('criterion_id')} · {case.get('type')} */\nclass {cls} {{\n"
                f"    private static final String BASE_URL = System.getenv().getOrDefault(\"BASE_URL\", \"{base_url}\");\n\n"
                f"    @Test\n    @DisplayName({json.dumps(case['id'] + ': ' + case['scenario'], ensure_ascii=False)})\n    void {_ident(case['scenario'])}() {{\n"
                f"        given().baseUri(BASE_URL){body}\n        .when().request(\"{call['method']}\", \"{call['path']}\")\n"
                f"        .then().statusCode(anyOf({', '.join(f'is({c})' for c in call['expect'])}));  // esperado: {codes}\n    }}\n}}\n")
        return files
    if framework == "postman-newman":
        items = []
        for case in cases:
            call = calls[case["id"]]
            request: dict[str, Any] = {"method": call["method"], "url": "{{baseUrl}}" + call["path"]}
            if call["body"]:
                request |= {"header": [{"key": "Content-Type", "value": "application/json"}], "body": {"mode": "raw", "raw": json.dumps(call["body"])}}
            items.append({"name": f"{case['id']}: {case['scenario']}", "request": request, "event": [{"listen": "test", "script": {"exec": [
                f"pm.test({json.dumps(case['id'] + ' · ' + case.get('expected_result', ''), ensure_ascii=False)}, function () {{",
                f"  pm.expect(pm.response.code).to.be.oneOf({json.dumps(call['expect'])});", "});"]}}]})
        collection = {"info": {"name": f"Valkiria · {feature}", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
                      "variable": [{"key": "baseUrl", "value": base_url}], "item": items}
        return {f"postman/{_snake(feature)}.postman_collection.json": json.dumps(collection, ensure_ascii=False, indent=2) + "\n",
                "postman/README.md": f"Ejecutar: `newman run postman/{_snake(feature)}.postman_collection.json --env-var baseUrl=$BASE_URL`\n"}
    files = {}
    for case in cases:
        call = calls[case["id"]]
        options = f", {{ data: {json.dumps(call['body'])} }}" if call["body"] else ""
        files[f"tests/api/{_snake(case['id'], 8)}.spec.ts"] = (
            "import { test, expect } from '@playwright/test';\n\n"
            f"// Trazabilidad: {case['id']} · criterio {case.get('criterion_id')} · {case.get('type')}\n"
            f"test({json.dumps(case['id'] + ': ' + case['scenario'], ensure_ascii=False)}, async ({{ request }}) => {{\n"
            f"  const response = await request.{call['method'].lower()}(`${{process.env.BASE_URL ?? '{base_url}'}}{call['path']}`{options});\n"
            f"  expect({json.dumps(call['expect'])}).toContain(response.status());  // {case.get('expected_result', '')}\n}});\n")
    return files
