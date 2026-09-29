"""Runner web de HU-010: ejecuta en Chromium los pasos de cada caso con la misma traducción que los scripts generados (web_steps)."""

from __future__ import annotations

import base64
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from valkiria.application.web_steps import (
    ERROR_ALERT,
    WebPlan,
    loose_pattern,
    option_matches,
    plan_case,
)


def _rx(text: str) -> re.Pattern[str]:
    return re.compile(loose_pattern(text), re.IGNORECASE)


class PlaywrightRunner:
    """Ejecuta los casos web contra la app sintética: un resultado por caso con el paso que falla y captura de pantalla."""

    def __init__(self, headless: bool = True, timeout_seconds: int = 30, step_timeout_seconds: float = 5):
        self.headless = headless
        self.timeout_ms = timeout_seconds * 1000
        self.step_timeout_ms = int(step_timeout_seconds * 1000)

    async def run(self, *, base_url: str, cases: list[dict[str, Any]], artifacts_dir: str | None = None) -> list[dict[str, Any]]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("playwright_not_installed_run_pip_install_e_e2e") from exc
        # /tmp por omisión: en el contenedor el resto del sistema de archivos es de solo lectura.
        output = Path(artifacts_dir or Path(tempfile.gettempdir()) / "valkiria-playwright")
        output.mkdir(parents=True, exist_ok=True)
        results = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless, args=["--disable-dev-shm-usage"])
            context = await browser.new_context(base_url=base_url.rstrip("/"))
            try:
                for case in cases:
                    results.append(await self._run_case(context, case, output))
            finally:
                await context.close()
                await browser.close()
        return results

    async def _run_case(self, context, case: dict[str, Any], output: Path) -> dict[str, Any]:
        case_id = str(case.get("id"))
        page = await context.new_page()
        page.set_default_timeout(self.step_timeout_ms)
        page.set_default_navigation_timeout(self.timeout_ms)
        console: list[str] = []
        page.on("console", lambda message: console.append(message.text))
        started = time.perf_counter()
        plan = plan_case(case)
        current = "Abrir la pantalla"
        executed: list[str] = []
        try:
            await page.goto(plan.route if plan.steps else "/")
            for step in plan.steps:
                current = step.text
                await self._step(page, step)
                executed.append(step.text)
            current = "Resultado esperado"
            if plan.steps or plan.anchors:
                await self._expect(page, plan)
            result, detail = "pass", None
        except Exception as exc:  # noqa: BLE001 - frontera: cada caso reporta su falla sin detener la ejecución
            result, detail = "fail", _describe(exc)
        screenshot: Path | None = output / f"{re.sub(r'[^A-Za-z0-9_-]', '_', case_id)}.jpg"
        inline = None
        try:
            image = await page.screenshot(path=str(screenshot), full_page=True, type="jpeg", quality=60)
            # En la falla, la captura viaja con el resultado para que el humano la vea sin acceso al disco del contenedor.
            inline = base64.b64encode(image).decode() if result == "fail" else None
        except Exception:  # noqa: BLE001 - la evidencia visual es opcional si la página ya no responde
            screenshot = None
        finally:
            await page.close()
        outcome: dict[str, Any] = {"case_id": case_id, "kind": "web", "result": result, "status": result, "traceable": True,
                                   "request": f"GET {plan.route}", "steps_executed": len(executed), "steps_total": len(plan.steps),
                                   "expected": {"texts": plan.anchors, "error_alert": plan.expect_error},
                                   "screenshot": str(screenshot) if screenshot else None, "console": console,
                                   "duration_ms": round((time.perf_counter() - started) * 1000, 2)}
        if result == "fail":
            outcome |= {"failed_step": current, "detail": detail, "screenshot_jpeg": inline}
        return outcome

    async def _step(self, page, step) -> None:
        if step.action == "open":
            await page.goto(step.target)
        if step.action == "tick":
            box = page.get_by_label(_rx(step.target)).first
            await (box.uncheck() if step.value == "off" else box.check())
        elif step.action == "fill":
            if step.value is None and step.data_key is None:
                return  # sin dato: se conserva el valor por defecto
            await page.get_by_label(_rx(step.target)).first.fill(step.value or "")
        elif step.action == "select":
            if step.value is None and step.data_key is None:
                return  # sin dato: se conserva la opción por defecto
            field = page.get_by_label(_rx(step.target)).first
            options = await field.locator("option").evaluate_all("items => items.map(o => ({value: o.value, text: o.textContent || ''}))")
            match = next((o for o in options if option_matches(step.value or "", o["value"], o["text"])), None)
            if match is None:
                raise AssertionError(f"Sin opción «{step.value}» en «{step.target}»")
            await field.select_option(match["value"])
        elif step.action == "click":
            name = _rx(step.target)
            button = page.get_by_role("button", name=name)
            # Primero el botón; el enlace solo si no hay botón (un menú «Registrar orden de venta» no debe ganarle al botón «Registrar orden»).
            await (button if await button.count() else page.get_by_role("link", name=name)).first.click()
            await page.wait_for_load_state()
        elif step.action == "check":
            await self._see(page, step.anchors)

    async def _see(self, page, texts: list[str]) -> None:
        from playwright.async_api import expect

        for text in texts:
            try:
                await expect(page.get_by_text(_rx(text)).first).to_be_visible(timeout=self.step_timeout_ms)
            except AssertionError as exc:
                raise AssertionError(f"No se ve «{text}»") from exc

    async def _expect(self, page, plan: WebPlan) -> None:
        await self._see(page, plan.anchors)
        errors = page.locator(ERROR_ALERT)
        count = await errors.count()
        if plan.expect_error and not count:
            raise AssertionError("Se esperaba un aviso de error y la pantalla no lo muestra")
        if not plan.expect_error and count:
            raise AssertionError(f"Aviso de error inesperado: «{(await errors.first.inner_text()).strip()}»")


def _describe(exc: Exception) -> str:
    text = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    if type(exc).__name__ == "TimeoutError":
        return "No se encontró el elemento del paso en la pantalla (tiempo de espera agotado)"
    return text[:200]
