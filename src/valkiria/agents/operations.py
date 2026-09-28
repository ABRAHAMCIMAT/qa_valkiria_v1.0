from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.llmops.lifecycle import Phase


class OperationsAgent(BaseAgent):
    name = "operations"
    phase = Phase.OPERATE

    def __init__(self, audit=None, metrics=None):
        self.audit = audit
        self.metrics = metrics

    async def can_handle(self, context: AgentContext) -> bool:
        return True

    async def execute(self, context: AgentContext):
        summary = {"trace_id": context.trace_id, "artifact_count": len(context.artifacts), "decision_count": len(context.decisions), "gates_recorded": list(context.quality_gates), "sensitive_data_policy": "redact"}
        return self.success(context, "La ejecución quedó preparada para operación y auditoría.", {"operations": summary}, [{"agent": self.name, "decision": "observability_summary_created"}])
