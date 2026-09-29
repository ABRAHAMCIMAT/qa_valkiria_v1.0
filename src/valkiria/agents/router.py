from __future__ import annotations

from valkiria.agents.contracts import AgentContext, AgentPlan
from valkiria.agents.registry import AgentRegistry


class AgentRouter:
    """Clasifica la petición y crea un plan explícito de delegación."""

    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    async def plan(self, context: AgentContext) -> AgentPlan:
        selected = []
        rationale = ["Intake y Grounding son obligatorios. Generation y Evaluation atienden el trabajo del flujo; Assistant, las preguntas y peticiones fuera del flujo."]
        for agent in self.registry.values():
            if await agent.can_handle(context):
                selected.append(agent.name)
        mandatory = [name for name in ("intake", "grounding", "generation", "evaluation", "assistant") if name in self.registry.names()]
        selected = [name for name in mandatory if name in selected] + [name for name in selected if name not in mandatory]
        if selected[:2] != ["intake", "grounding"]:
            raise RuntimeError("El plan no contiene los agentes intake y grounding obligatorios.")
        rationale.append("Los agentes especializados adicionales fueron seleccionados por intención y dependencias declaradas.")
        return AgentPlan(context.request_id, context.trace_id, selected, rationale)
