from __future__ import annotations

from typing import Any

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.infrastructure.playwright_runner import PlaywrightRunner
from valkiria.llmops.lifecycle import Phase


class AutomationAgent(BaseAgent):
    """Prepara casos y ejecuta Playwright solo cuando el perfil E2E lo habilita."""

    name = "automation"
    phase = Phase.EVALUATION

    def __init__(self, runner: PlaywrightRunner | None = None, base_url: str = "http://localhost:8090"):
        self.runner = runner
        self.base_url = base_url

    async def can_handle(self, context: AgentContext) -> bool:
        text = context.user_request.lower()
        return any(term in text for term in ("playwright", "selenium", "automatización", "automatizacion", "e2e", "end to end", "prueba de interfaz"))

    async def execute(self, context: AgentContext):
        cases = [{"id": "HU010-TC-001", "scenario": "Consultar vehículos Nissan disponibles"}]
        if self.runner is None:
            return self.success(context, "Se prepararon los casos de automatización; la ejecución queda pendiente de habilitar el runner E2E.", {"automation": {"runner": "playwright", "mode": "preview", "base_url": self.base_url, "cases": cases, "evidence": False}}, [{"agent": self.name, "decision": "automation_preview", "case_count": len(cases)}])
        try:
            results = await self.runner.run(base_url=self.base_url, cases=cases)
        except RuntimeError:
            return self.failure(context, "El runner de automatización no está disponible en el entorno.", retryable=False)
        failed = any(item.get("status") == "fail" for item in results)
        payload: dict[str, Any] = {"automation": {"runner": "playwright", "mode": "executed", "base_url": self.base_url, "results": results, "evidence": True}}
        if failed:
            return self.failure(context, "Uno o más casos de automatización fallaron.", retryable=False)
        return self.success(context, "La automatización Playwright terminó con evidencia por caso.", payload, [{"agent": self.name, "decision": "automation_executed", "case_count": len(results)}])
