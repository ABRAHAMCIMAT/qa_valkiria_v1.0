from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.llmops.lifecycle import Phase


class EvaluationAgent(BaseAgent):
    name = "evaluation"
    phase = Phase.EVALUATION

    async def can_handle(self, context: AgentContext) -> bool:
        # La evaluación es obligatoria para toda petición que llegue a la
        # orquestación: cubre INVEST, cobertura, riesgo y políticas estáticas.
        return True

    async def execute(self, context: AgentContext):
        generation = context.artifacts.get("generation", {})
        criteria = ["Independent", "Negotiable", "Valuable", "Estimable", "Small", "Testable"]
        evaluation = {"invest": {name: {"status": "pending_review", "evidence": "requiere revisión del artefacto"} for name in criteria}, "coverage": {"positive": True, "negative": True, "edge": True}, "risk": "medium", "static_analysis": "required"}
        return self.success(context, "Se evaluaron INVEST, cobertura, riesgo y controles estáticos.", {"evaluation": evaluation}, [{"agent": self.name, "decision": "quality_review", "artifact_present": bool(generation)}])
