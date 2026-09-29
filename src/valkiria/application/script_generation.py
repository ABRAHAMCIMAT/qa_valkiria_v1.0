"""Generación del código de los scripts de automatización (HU-009) a partir de los casos de la matriz.

Determinista (sin esperar al modelo) y trazable: un script por caso con su id, patrón Page Object, datos
externalizados en un archivo aparte y verificación del resultado esperado.

- Web (Playwright TypeScript, Selenium Python): los pasos se traducen con web_steps (acción, campo, valor, pantalla y
  textos esperados) a localizadores semánticos por etiqueta, rol y texto; el runner de Chromium de HU-010 usa la misma
  traducción. Si la interfaz real usa otros textos, se ajustan en el Page Object, en un solo lugar.
- API (Playwright request, RestAssured, Postman-Newman): cada caso se asocia a un endpoint real de la app
  sintética de Nissan según su contenido, con el código de estado esperado según el tipo de caso.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from valkiria.application.web_steps import ERROR_ALERT, WebStep, plan_case

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
# La traducción de cada paso sale de web_steps.plan_case, la misma que usa el runner de Chromium (HU-010): lo que se
# ejecuta localmente contra la app sintética es exactamente lo que el script entregado en el PR hace.

def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _has_value(step: WebStep) -> bool:
    return step.value is not None or step.data_key is not None


def _skipped(step: WebStep) -> str:
    return "se verifica en el resultado esperado" if step.action == "check" else "sin dato: se conserva el valor por defecto"


def _ts_value(step: WebStep) -> str:
    default = _js(step.value or "")
    return f"String(data[{_js(step.data_key)}] ?? {default})" if step.data_key else default


def _playwright_web(cases: list[dict[str, Any]], feature: str, data: dict[str, Any]) -> dict[str, str]:
    page = (
        "import { expect, type Page } from '@playwright/test';\n\n"
        "export function plain(text: string): string {\n  return text.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').trim();\n}\n\n"
        "/** Ignora acentos y mayúsculas: 'vehiculo' encuentra 'Vehículo'. */\n"
        "export function loose(text: string): RegExp {\n"
        "  const classes: Record<string, string> = { a: '[aá]', e: '[eé]', i: '[ií]', o: '[oó]', u: '[uúü]', n: '[nñ]' };\n"
        "  return new RegExp([...plain(text)].map((c) => classes[c] ?? c.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')).join(''), 'i');\n"
        "}\n\n"
        f"/** Page Object de {feature}. Las acciones y los localizadores semánticos (etiqueta, rol, texto) salen de los pasos de la matriz;\n"
        " *  si la interfaz real usa otros textos o rutas, se ajustan aquí, en un solo lugar. */\n"
        f"export class {feature}Page {{\n  constructor(private readonly page: Page) {{}}\n\n"
        "  async open(path: string) {\n    await this.page.goto(path);\n  }\n\n"
        "  async fill(label: string, value: string) {\n    await this.page.getByLabel(loose(label)).first().fill(value);\n  }\n\n"
        "  async select(label: string, value: string) {\n"
        "    if (!value) return;  // sin dato: se conserva la opción por defecto\n"
        "    const list = this.page.getByLabel(loose(label)).first();\n"
        "    const options = await list.locator('option').evaluateAll((items) => items.map((o) => ({ value: (o as HTMLOptionElement).value, text: o.textContent ?? '' })));\n"
        "    const wanted = plain(value);\n"
        "    const match = options.find((o) => o.value === value || plain(o.text).includes(wanted) || (plain(o.text) !== '' && wanted.includes(plain(o.text))));\n"
        "    if (!match) throw new Error(`Sin opción \"${value}\" en ${label}`);\n"
        "    await list.selectOption(match.value);\n  }\n\n"
        "  /** Primero el botón; el enlace solo si no hay botón con ese nombre. */\n"
        "  async click(name: string) {\n"
        "    const button = this.page.getByRole('button', { name: loose(name) });\n"
        "    await ((await button.count()) ? button : this.page.getByRole('link', { name: loose(name) })).first().click();\n  }\n\n"
        "  async tick(label: string, on = true) {\n"
        "    const box = this.page.getByLabel(loose(label)).first();\n    await (on ? box.check() : box.uncheck());\n  }\n\n"
        "  async see(texts: string[]) {\n"
        "    for (const text of texts) await expect(this.page.getByText(loose(text)).first()).toBeVisible();\n  }\n\n"
        "  /** Resultado esperado: sus textos visibles y, según el caso, que haya o no un aviso de error. */\n"
        "  async expectResult(texts: string[], expectError: boolean) {\n"
        "    await this.see(texts);\n"
        f"    const errors = this.page.locator({_js(ERROR_ALERT)});\n"
        "    if (expectError) await expect(errors.first()).toBeVisible();\n    else await expect(errors).toHaveCount(0);\n  }\n}\n")
    files = {f"tests/pages/{feature}Page.ts": page, f"tests/data/{_snake(feature)}.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
             "playwright.config.ts": "import { defineConfig } from '@playwright/test';\n\nexport default defineConfig({\n  testDir: './tests/specs',\n"
                                     "  use: { baseURL: process.env.BASE_URL ?? 'http://localhost:8090', screenshot: 'only-on-failure', trace: 'retain-on-failure' },\n"
                                     "  reporter: [['list'], ['junit', { outputFile: 'test-results.xml' }]],\n});\n",
             "tsconfig.json": json.dumps({"compilerOptions": {"target": "ES2022", "module": "commonjs", "strict": True, "esModuleInterop": True, "resolveJsonModule": True}}, indent=2) + "\n"}
    for case in cases:
        plan = plan_case(case)
        lines = [f"  await test.step('Abrir la pantalla', async () => {{ await app.open({_js(plan.route)}); }});"]
        for step in plan.steps:
            call = {"open": f"app.open({_js(step.target)})",
                    "fill": f"app.fill({_js(step.target)}, {_ts_value(step)})" if _has_value(step) else None,
                    "select": f"app.select({_js(step.target)}, {_ts_value(step)})" if _has_value(step) else None,
                    "check": f"app.see({_js(step.anchors)})" if step.anchors else None,
                    "click": f"app.click({_js(step.target)})",
                    "tick": f"app.tick({_js(step.target)}, {'false' if step.value == 'off' else 'true'})"}[step.action]
            body = f"await {call};" if call else f"/* {_skipped(step)} */"
            lines.append(f"  await test.step({_js(step.text)}, async () => {{ {body} }});")
        files[f"tests/specs/{_snake(case['id'], 8)}.spec.ts"] = (
            f"import {{ test }} from '@playwright/test';\nimport {{ {feature}Page }} from '../pages/{feature}Page';\nimport cases from '../data/{_snake(feature)}.json';\n\n"
            f"// Trazabilidad: {case['id']} · criterio {case.get('criterion_id')} · {case.get('type')}\n"
            f"test({_js(case['id'] + ': ' + case['scenario'])}, {{ tag: ['@{case.get('type')}', '@{case.get('criterion_id')}'] }}, async ({{ page }}) => {{\n"
            f"  const app = new {feature}Page(page);\n  const data = (cases as Record<string, Record<string, unknown>>)[{_js(case['id'])}] ?? {{}};\n"
            + "\n".join(lines) +
            f"\n  // {case.get('expected_result', '')}\n"
            f"  await test.step('Resultado esperado', async () => {{ await app.expectResult({_js(plan.anchors)}, {'true' if plan.expect_error else 'false'}); }});\n}});\n")
    return files


def _py_value(step: WebStep) -> str:
    default = _js(step.value or "")
    return f"str(data.get({_js(step.data_key)}, {default}))" if step.data_key else default


def _selenium(cases: list[dict[str, Any]], feature: str, data: dict[str, Any]) -> dict[str, str]:
    page = (
        "import os\nimport re\nimport unicodedata\n\n"
        "from selenium.common.exceptions import TimeoutException\nfrom selenium.webdriver.common.by import By\n"
        "from selenium.webdriver.support import expected_conditions as ec\nfrom selenium.webdriver.support.ui import Select, WebDriverWait\n\n"
        "BASE_URL = os.getenv('BASE_URL', 'http://localhost:8090')\n\n\n"
        "def plain(text):\n    return ''.join(c for c in unicodedata.normalize('NFD', str(text).lower()) if unicodedata.category(c) != 'Mn').strip()\n\n\n"
        f"class {feature}Page:\n    \"\"\"Page Object de {feature}: acciones y localizadores salen de los pasos de la matriz; si la interfaz usa otros textos, se ajustan aquí.\"\"\"\n\n"
        "    def __init__(self, driver):\n        self.driver = driver\n\n"
        "    def open(self, path):\n        self.driver.get(BASE_URL.rstrip('/') + path)\n\n"
        "    def _labelled(self, label):\n"
        "        for element in self.driver.find_elements(By.TAG_NAME, 'label'):\n"
        "            if plain(label) in plain(element.text):\n"
        "                target = element.get_attribute('for')\n"
        "                return self.driver.find_element(By.ID, target) if target else element.find_element(By.XPATH, './/input|.//select')\n"
        "        raise AssertionError(f'Sin campo con la etiqueta {label}')\n\n"
        "    def fill(self, label, value):\n        field = self._labelled(label)\n        field.clear()\n        field.send_keys(str(value))\n\n"
        "    def select(self, label, value):\n"
        "        if not value:\n            return  # sin dato: se conserva la opción por defecto\n"
        "        options = Select(self._labelled(label))\n"
        "        match = next((o for o in options.options if o.get_attribute('value') == str(value) or plain(value) in plain(o.text)\n"
        "                      or (plain(o.text) and plain(o.text) in plain(value))), None)\n"
        "        assert match is not None, f'Sin opción {value} en {label}'\n"
        "        options.select_by_value(match.get_attribute('value'))\n\n"
        "    def click(self, name):\n"
        "        # Primero el botón; el enlace solo si no hay botón con ese nombre.\n"
        "        for element in self.driver.find_elements(By.TAG_NAME, 'button') + self.driver.find_elements(By.TAG_NAME, 'a'):\n"
        "            if plain(name) in plain(element.text):\n                element.click()\n"
        "                try:  # si el clic envía un formulario, espera a la página nueva\n"
        "                    WebDriverWait(self.driver, 2).until(ec.staleness_of(element))\n"
        "                except TimeoutException:\n                    pass\n"
        "                return\n"
        "        raise AssertionError(f'Sin botón o enlace {name}')\n\n"
        "    def tick(self, label, on=True):\n"
        "        box = self._labelled(label)\n        if box.is_selected() != on:\n            box.click()\n\n"
        "    def see(self, texts, timeout=5):\n"
        "        for text in texts:\n"
        "            try:\n"
        "                WebDriverWait(self.driver, timeout).until(lambda d, t=text: plain(t) in plain(d.find_element(By.TAG_NAME, 'body').text))\n"
        "            except TimeoutException:\n                raise AssertionError(f'No se ve: {text}') from None\n\n"
        "    def expect_result(self, texts, expect_error):\n"
        "        self.see(texts)\n"
        f"        errors = self.driver.find_elements(By.CSS_SELECTOR, {_js(ERROR_ALERT)})\n"
        "        assert bool(errors) == expect_error, 'Se esperaba un aviso de error' if expect_error else f'Aviso de error inesperado: {errors[0].text}'\n")
    files = {f"tests/pages/{_snake(feature)}_page.py": page, f"tests/data/{_snake(feature)}.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
             "tests/conftest.py": "import pytest\nfrom selenium import webdriver\n\n\n@pytest.fixture\ndef driver():\n    options = webdriver.ChromeOptions()\n"
                                  "    options.add_argument('--headless=new')\n    driver = webdriver.Chrome(options=options)\n    yield driver\n    driver.quit()\n"}
    for case in cases:
        plan = plan_case(case)
        lines = [f"    page.open({_js(plan.route)})"]
        for step in plan.steps:
            call = {"open": f"page.open({_js(step.target)})",
                    "fill": f"page.fill({_js(step.target)}, {_py_value(step)})" if _has_value(step) else None,
                    "select": f"page.select({_js(step.target)}, {_py_value(step)})" if _has_value(step) else None,
                    "check": f"page.see({_js(step.anchors)})" if step.anchors else None, "click": f"page.click({_js(step.target)})",
                    "tick": f"page.tick({_js(step.target)}, {step.value != 'off'})"}[step.action]
            lines.append(f"    # {step.text}\n    {call}" if call else f"    # {step.text} ({_skipped(step)})")
        files[f"tests/test_{_snake(case['id'], 8)}.py"] = (
            f"import json\nfrom pathlib import Path\n\nfrom pages.{_snake(feature)}_page import {feature}Page\n\n"
            f"DATA = json.loads((Path(__file__).parent / 'data' / '{_snake(feature)}.json').read_text(encoding='utf-8'))\n\n\n"
            f"def test_{_snake(case['id'], 8)}(driver):\n    \"\"\"{case['id']} · criterio {case.get('criterion_id')} · {case.get('type')}: {case['scenario']}\"\"\"\n"
            f"    page = {feature}Page(driver)\n    data = DATA.get({_js(case['id'])}, {{}})\n" + "\n".join(lines) +
            f"\n    # Resultado esperado: {case.get('expected_result', '')}\n    page.expect_result({_js(plan.anchors)}, {plan.expect_error})\n")
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
