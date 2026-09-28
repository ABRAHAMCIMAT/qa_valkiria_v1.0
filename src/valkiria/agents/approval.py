from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.llmops.lifecycle import Phase


class ApprovalAgent(BaseAgent):
    name = "approval"
    phase = Phase.APPROVAL

    async def can_handle(self, context: AgentContext) -> bool:
        text = context.user_request.lower()
        return any(term in text for term in ("aprobar", "aprobación", "aprobacion", "release", "publicar", "pull request", "pr", "desplegar"))

    async def execute(self, context: AgentContext):
        approval = context.artifacts.get("approval", {})
        if approval.get("approved") is True and approval.get("artifact_hash"):
            return self.success(context, "La versión exacta del artefacto fue aprobada.", {"approval": approval})
        return self.pending(context, "La acción requiere aprobación humana explícita para esta versión.", {"approval": {"status": "pending", "required": True, "artifact_hash": None}})
