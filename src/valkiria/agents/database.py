from __future__ import annotations

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.domain.database_ports import DatabaseExecutor
from valkiria.infrastructure.synthetic_database import SyntheticSQLiteExecutor
from valkiria.llmops.lifecycle import Phase


class DatabaseAgent(BaseAgent):
    name = "database"
    phase = Phase.EVALUATION

    def __init__(self, executor: DatabaseExecutor | None = None):
        self.executor = executor or SyntheticSQLiteExecutor()

    async def can_handle(self, context: AgentContext) -> bool:
        text = context.user_request.lower()
        return any(term in text for term in ("base de datos", "database", "sql", "postgres", "mysql", "oracle", "sql server", "sintética", "sintetica", "inventario", "vehículos", "vehiculos"))

    async def execute(self, context: AgentContext):
        case_id = "HU011-TC-001"
        script = "SELECT vehicle_id, model, stock FROM vehicles WHERE stock > 0"
        result, report = self.executor.execute(script=script, case_id=case_id, trace_id=context.trace_id, output_format="pdf")
        if result.get("status") == "failed":
            return self.failure(context, "La base sintética no estuvo disponible.", retryable=True)
        if result.get("blocked"):
            return self.blocked(context, "El script de base de datos fue bloqueado por política.", {"database": result})
        return self.success(context, "Se validó la petición contra una base sintética y se generó evidencia.", {"database": {"result": result, "report": report}}, [{"agent": self.name, "decision": "synthetic_database_execution", "engine": result.get("engine"), "case_id": case_id}])
