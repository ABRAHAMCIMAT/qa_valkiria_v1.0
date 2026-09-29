from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.assistant.routing import is_question
from valkiria.llmops.lifecycle import Phase


class GroundingAgent(BaseAgent):
    name = "grounding"
    phase = Phase.GROUNDING

    async def can_handle(self, context: AgentContext) -> bool:
        return True

    async def execute(self, context: AgentContext):
        text = context.user_request.lower()
        explicit = any(word in text for word in ("ambigua", "ambiguo", "no sé", "no se"))
        # Una petición corta es ambigua salvo que continúe una sesión con contexto (memoria de corto plazo).
        follow_up = bool(context.memory.get("session"))
        # Las preguntas cortas ("¿qué puedes hacer?") las resuelve el asistente; no son ambigüedad.
        ambiguous = explicit or (len(text.strip()) < 20 and not follow_up and not is_question(text))
        if ambiguous:
            return self.blocked(context, "La petición necesita aclarar alcance, resultado esperado o criterio de éxito.", {"grounding": {"ambiguous": True, "questions": ["¿Qué artefacto debe producirse?", "¿Qué historia o componente es prioritario?"]}})
        policies = ["validación de esquema", "trazabilidad por trace_id", "PR-only", "secretos fuera del código", "bloqueo de producción"]
        sources = ["domain", "application", "llmops", "docs"] + (["session_memory"] if follow_up else []) + (["long_term_memory"] if context.memory.get("recalled") else [])
        grounding = {"ambiguous": False, "policies": policies, "sources": sources, "follow_up": follow_up, "recalled": context.memory.get("recalled", [])}
        return self.success(context, "Contexto del repositorio, políticas y memoria cargados.", {"grounding": grounding}, [{"agent": self.name, "decision": "policy_context_loaded", "policies": policies, "memory_sources": sources[4:]}])
