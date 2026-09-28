from __future__ import annotations

import time
from pathlib import Path
from typing import Any


class PlaywrightRunner:
    """Runner E2E opcional para la aplicación sintética."""

    def __init__(self, headless: bool = True, timeout_seconds: int = 30):
        self.headless = headless
        self.timeout_ms = timeout_seconds * 1000

    async def run(self, *, base_url: str, cases: list[dict[str, Any]], artifacts_dir: str = "artifacts/playwright") -> list[dict[str, Any]]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("playwright_not_installed_run_pip_install_e_e2e") from exc
        output = Path(artifacts_dir)
        output.mkdir(parents=True, exist_ok=True)
        results = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            context = await browser.new_context(record_video_dir=str(output / "video"))
            for case in cases:
                page = await context.new_page()
                page.set_default_timeout(self.timeout_ms)
                case_id = str(case["id"])
                started = time.perf_counter()
                console_logs: list[str] = []
                requests: list[dict[str, Any]] = []
                responses: list[dict[str, Any]] = []
                page.on("console", lambda message, logs=console_logs: logs.append(message.text))
                page.on("request", lambda request, events=requests: events.append({"method": request.method, "url": request.url}))
                page.on("response", lambda response, events=responses: events.append({"status": response.status, "url": response.url}))
                try:
                    await page.goto(base_url, wait_until="networkidle")
                    screenshot = output / f"{case_id}.png"
                    await page.screenshot(path=str(screenshot), full_page=True)
                    results.append({"case_id": case_id, "status": "pass", "traceable": True, "screenshot": str(screenshot), "console": console_logs, "requests": requests, "responses": responses, "duration_ms": round((time.perf_counter() - started) * 1000, 2)})
                except Exception as exc:  # noqa: BLE001 - boundary translates vendor errors per case
                    results.append({"case_id": case_id, "status": "fail", "traceable": True, "error": "playwright_case_failed", "error_type": type(exc).__name__, "console": console_logs, "requests": requests, "responses": responses, "duration_ms": round((time.perf_counter() - started) * 1000, 2)})
                finally:
                    await page.close()
            await context.close()
            await browser.close()
        return results
