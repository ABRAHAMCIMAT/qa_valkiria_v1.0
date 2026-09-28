from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.llmops.lifecycle import Phase


class ReleaseAgent(BaseAgent):
    name = "release"
    phase = Phase.RELEASE

    async def can_handle(self, context: AgentContext) -> bool:
        text = context.user_request.lower()
        return any(term in text for term in ("release", "publicar", "pull request", "pr", "desplegar", "preview", "vista previa"))

    async def execute(self, context: AgentContext):
        approval = context.artifacts.get("approval", {})
        if approval.get("status") != "approved":
            return self.success(context, "Se preparó una vista previa; el release real sigue bloqueado hasta la aprobación humana.", {"release": {"mode": "preview", "direct_commit": False, "pr_required": True, "status": "waiting_approval"}})
        return self.success(context, "Se preparó una propuesta de release PR-only.", {"release": {"mode": "pull_request", "direct_commit": False, "pr_required": True, "status": "ready"}})
