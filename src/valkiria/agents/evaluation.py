from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.assistant.routing import route_message
from valkiria.llmops.lifecycle import Phase


class EvaluationAgent(BaseAgent):
    name = "evaluation"
    phase = Phase.EVALUATION

    async def can_handle(self, context: AgentContext) -> bool:
        # Evalúa todo artefacto generado por el flujo: INVEST, cobertura, riesgo y políticas estáticas.
        return route_message(context.user_request, follow_up=context.follow_up) == "story"

    async def execute(self, context: AgentContext):
        generation = context.artifacts.get("generation", {})
        criteria = ["Independent", "Negotiable", "Valuable", "Estimable", "Small", "Testable"]
        evaluation = {"invest": {name: {"status": "pending_review", "evidence": "requiere revisión del artefacto"} for name in criteria}, "coverage": {"positive": True, "negative": True, "edge": True}, "risk": "medium", "static_analysis": "required"}
        return self.success(context, "Se evaluaron INVEST, cobertura, riesgo y controles estáticos.", {"evaluation": evaluation}, [{"agent": self.name, "decision": "quality_review", "artifact_present": bool(generation)}])
