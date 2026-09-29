from __future__ import annotations

import re

from valkiria.agents.base import BaseAgent
from valkiria.agents.contracts import AgentContext
from valkiria.llmops.lifecycle import Phase


class IntakeAgent(BaseAgent):
    name = "intake"
    phase = Phase.INTAKE

    async def can_handle(self, context: AgentContext) -> bool:
        return True

    async def execute(self, context: AgentContext):
        text = context.user_request.lower()
        keywords = re.findall(r"[a-záéíóúñ0-9_-]+", text)
        intents = []
        mapping = {
            "story": ("historia", "user story", "requerimiento", "épica", "epica"),
            "testing": ("prueba", "test", "matriz", "qa", "invest"),
            "risk": ("riesgo", "risk", "prioridad"),
            "automation": ("automatización", "automatizacion", "selenium", "playwright", "lote"),
            "database": ("base de datos", "sql", "postgresql", "oracle", "mysql", "sql server"),
            "performance": ("rendimiento", "performance", "jmeter", "k6", "locust"),
            "approval": ("aprobar", "aprobación", "aprobacion", "revisión", "revision"),
            "release": ("release", "publicar", "pull request", "pr", "desplegar"),
            "operations": ("métrica", "metrica", "log", "auditoría", "auditoria", "operación", "operacion"),
        }
        for intent, terms in mapping.items():
            if any(term in text for term in terms):
                intents.append(intent)
        inherited = False
        if not intents and context.memory.get("facts", {}).get("intents"):
            # Seguimiento de la conversación ("ahora para el Sentra"): se conserva la intención de la sesión.
            intents, inherited = list(context.memory["facts"]["intents"]), True
        if not intents:
            intents = ["general_qa"]
        artifact = {"intake": {"intents": intents, "keywords": keywords[:80], "scope": "repository", "language": "es", "intents_from_session": inherited}}
        return self.success(context, "Petición clasificada y acotada.", artifact, [{"agent": self.name, "decision": "intent_classification", "intents": intents, "from_session": inherited}])
