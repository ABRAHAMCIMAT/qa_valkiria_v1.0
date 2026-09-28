from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.llmops.lifecycle import Phase


class GenerationAgent(BaseAgent):
    name = "generation"
    phase = Phase.GENERATION

    def __init__(self, llm=None):
        self.llm = llm

    async def can_handle(self, context: AgentContext) -> bool:
        # Toda solicitud suficientemente clara pasa por generación para producir
        # un artefacto o un plan verificable antes de su evaluación.
        return True

    async def execute(self, context: AgentContext):
        if context.artifacts.get("grounding", {}).get("ambiguous"):
            return self.blocked(context, "La generación se detuvo porque Grounding detectó ambigüedad crítica.")
        if self.llm is None:
            return self.success(context, "Se generó un borrador determinista para continuar la evaluación.", {"generation": {"mode": "deterministic_draft", "request": context.user_request, "version": "draft-1"}})
        try:
            generated = await self.llm.generate_json(system="Razona la petición de QA por etapas y devuelve un artefacto JSON conciso, verificable y en español.", user=context.user_request, schema={"type": "object", "required": ["summary", "deliverables", "acceptance_criteria"]})
            return self.success(context, "El LLM generó un artefacto estructurado.", {"generation": {"mode": "llm", "version": "draft-1", "artifact": generated}})
        except Exception as exc:
            return self.failure(context, "La generación LLM no produjo una respuesta válida.", retryable=isinstance(exc, TimeoutError))
