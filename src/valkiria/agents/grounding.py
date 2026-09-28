from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.llmops.lifecycle import Phase


class GroundingAgent(BaseAgent):
    name = "grounding"
    phase = Phase.GROUNDING

    async def can_handle(self, context: AgentContext) -> bool:
        return True

    async def execute(self, context: AgentContext):
        text = context.user_request.lower()
        ambiguous = len(text.strip()) < 20 or any(word in text for word in ("ambigua", "ambiguo", "no sé", "no se"))
        if ambiguous:
            return self.blocked(context, "La petición necesita aclarar alcance, resultado esperado o criterio de éxito.", {"grounding": {"ambiguous": True, "questions": ["¿Qué artefacto debe producirse?", "¿Qué historia o componente es prioritario?"]}})
        policies = ["validación de esquema", "trazabilidad por trace_id", "PR-only", "secretos fuera del código", "bloqueo de producción"]
        return self.success(context, "Contexto del repositorio y políticas cargados.", {"grounding": {"ambiguous": False, "policies": policies, "sources": ["domain", "application", "llmops", "docs"]}}, [{"agent": self.name, "decision": "policy_context_loaded", "policies": policies}])
