from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.application.prompts import GENERATION_SYSTEM
from valkiria.assistant.routing import route_message
from valkiria.llmops.lifecycle import Phase
from valkiria.memory.service import with_memory
from valkiria.providers.openai_compatible import LLMProviderError


class GenerationAgent(BaseAgent):
    name = "generation"
    phase = Phase.GENERATION

    def __init__(self, llm=None):
        self.llm = llm

    async def can_handle(self, context: AgentContext) -> bool:
        # El trabajo del flujo (HU, matriz, scripts…) pasa por generación; las preguntas y peticiones
        # fuera del flujo las resuelve el agente assistant con herramientas.
        return route_message(context.user_request, follow_up=context.follow_up) == "story"

    async def execute(self, context: AgentContext):
        if context.artifacts.get("grounding", {}).get("ambiguous"):
            return self.blocked(context, "La generación se detuvo porque Grounding detectó ambigüedad crítica.")
        if self.llm is None:
            return self.success(context, "Se generó un borrador determinista para continuar la evaluación.", {"generation": {"mode": "deterministic_draft", "request": context.user_request, "version": "draft-1"}})
        try:
            memory = "\n\n".join(part for part in (context.memory.get("long_term", ""), ("SESIÓN ACTUAL:\n" + context.memory["session"]) if context.memory.get("session") else "") if part)
            generated = await self.llm.generate_json(system=GENERATION_SYSTEM, user=with_memory(context.user_request, memory), schema={"type": "object", "required": ["summary", "deliverables", "acceptance_criteria"]})
            return self.success(context, "El LLM generó un artefacto estructurado.", {"generation": {"mode": "llm", "version": "draft-1", "artifact": generated}})
        except (LLMProviderError, TimeoutError, ValueError) as exc:
            retryable = isinstance(exc, TimeoutError) or getattr(exc, "code", None) == "llm_timeout"
            return self.failure(context, "La generación LLM no produjo una respuesta válida.", retryable=retryable)
